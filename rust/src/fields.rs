//! Per-field value decoding (needs the Python interpreter).
//!
//! Each arm mirrors `DbosParser._build_dbos_row_python` in
//! `nzpy_extended/_dbos.py`, which is the reference behaviour.

use std::ffi::CStr;

use pyo3::exceptions::PyValueError;
use pyo3::ffi;
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDate, PyDateTime, PyFloat, PyString, PyTime};

use crate::layout::FieldMeta;
use crate::nztype as t;
use crate::{numeric, pycache, temporal};

/// Client and CHAR/VARCHAR codecs, as NUL-terminated names for `PyUnicode_Decode`.
pub struct Codecs {
    pub char_enc: std::ffi::CString,
    pub client_enc: std::ffi::CString,
    char_is_utf8: bool,
    client_is_utf8: bool,
}

impl Codecs {
    pub fn new(char_enc: &str, client_enc: &str) -> PyResult<Self> {
        let to_c = |s: &str| {
            std::ffi::CString::new(s)
                .map_err(|_| PyValueError::new_err("encoding name contains NUL"))
        };
        Ok(Codecs {
            char_is_utf8: is_utf8_name(char_enc),
            client_is_utf8: is_utf8_name(client_enc),
            char_enc: to_c(char_enc)?,
            client_enc: to_c(client_enc)?,
        })
    }
}

fn is_utf8_name(name: &str) -> bool {
    matches!(
        name.to_ascii_lowercase().as_str(),
        "utf-8" | "utf8" | "utf_8"
    )
}

/// Strict decode, the same as `str(bytes, encoding)`.
///
/// UTF-8 input that is valid skips the codec machinery entirely. Anything
/// else (including invalid UTF-8, so that the exact codec error is raised)
/// goes through `PyUnicode_Decode`, the same codec path Python uses.
fn decode<'py>(
    py: Python<'py>,
    bytes: &[u8],
    enc: &CStr,
    is_utf8: bool,
) -> PyResult<Bound<'py, PyString>> {
    if is_utf8 {
        if let Ok(s) = std::str::from_utf8(bytes) {
            return Ok(PyString::new(py, s));
        }
    }
    // SAFETY: `bytes` is valid for `len` bytes for the duration of the call,
    // `enc` is NUL-terminated, and a null `errors` selects strict handling.
    unsafe {
        let ptr = ffi::PyUnicode_Decode(
            bytes.as_ptr().cast(),
            bytes.len() as ffi::Py_ssize_t,
            enc.as_ptr(),
            std::ptr::null(),
        );
        Bound::from_owned_ptr_or_err(py, ptr)?
            .cast_into::<PyString>()
            .map_err(Into::into)
    }
}

/// CHAR and NCHAR storage: strip trailing NULs, then pad with spaces to `width`
/// characters (the Python reference does `.rstrip("\x00").ljust(width)`).
fn pad_text<'py>(
    py: Python<'py>,
    text: Bound<'py, PyString>,
    width: usize,
) -> PyResult<Bound<'py, PyAny>> {
    let s = text.to_str()?;
    let trimmed = s.trim_end_matches('\0');
    let chars = trimmed.chars().count();
    if trimmed.len() == s.len() && chars >= width {
        return Ok(text.into_any());
    }
    let mut out = String::with_capacity(trimmed.len() + width.saturating_sub(chars));
    out.push_str(trimmed);
    out.extend(std::iter::repeat_n(' ', width.saturating_sub(chars)));
    Ok(PyString::new(py, &out).into_any())
}

/// Signed little-endian integer of `bytes`. Sign-extends from the top bit for
/// fewer than 8 bytes, reads the first 8 bytes otherwise. Zero bytes give 0.
fn read_signed(bytes: &[u8]) -> i64 {
    match bytes.len() {
        0 => 0,
        n if n >= 8 => i64::from_le_bytes(bytes[..8].try_into().unwrap()),
        n => {
            let mut buf = [0u8; 8];
            buf[..n].copy_from_slice(bytes);
            let shift = 64 - 8 * n as u32;
            (i64::from_le_bytes(buf) << shift) >> shift
        }
    }
}

fn i32_at(row: &[u8], at: usize) -> i64 {
    i32::from_le_bytes(row[at..at + 4].try_into().unwrap()) as i64
}

fn to_u8(v: i128, what: &str) -> PyResult<u8> {
    u8::try_from(v).map_err(|_| PyValueError::new_err(format!("{what} out of range")))
}

fn to_i32(v: i128) -> PyResult<i32> {
    i32::try_from(v).map_err(|_| PyValueError::new_err("year out of range"))
}

/// Decode field `f`, whose validated payload starts at `off` in `row`.
pub fn decode_field<'py>(
    py: Python<'py>,
    f: &FieldMeta,
    row: &[u8],
    off: usize,
    codecs: &Codecs,
) -> PyResult<Bound<'py, PyAny>> {
    let ftype = if f.ftype == t::UNKNOWN {
        t::VARCHAR
    } else {
        f.ftype
    };
    let p = &row[off..];
    match ftype {
        t::CHAR => {
            let width = f.size.max(0) as usize;
            let text = decode(
                py,
                &row[off..off + width],
                &codecs.char_enc,
                codecs.char_is_utf8,
            )?;
            pad_text(py, text, width)
        }
        t::NCHAR | t::NVARCHAR => {
            let cursize = (u16::from_le_bytes([p[0], p[1]]) as usize).saturating_sub(2);
            let text = decode(
                py,
                &row[off + 2..off + 2 + cursize],
                &codecs.client_enc,
                codecs.client_is_utf8,
            )?;
            if ftype == t::NCHAR {
                pad_text(py, text, f.size.max(0) as usize)
            } else {
                Ok(text.into_any())
            }
        }
        t::VARCHAR
        | t::VARFIXEDCHAR
        | t::GEOMETRY
        | t::VARBINARY
        | t::JSON
        | t::JSONB
        | t::JSONPATH
        | t::VECTOR => {
            let cursize = (u16::from_le_bytes([p[0], p[1]]) as usize).saturating_sub(2);
            let text = decode(
                py,
                &row[off + 2..off + 2 + cursize],
                &codecs.char_enc,
                codecs.char_is_utf8,
            )?;
            Ok(text.into_any())
        }
        t::INT8 => Ok(i64::from_le_bytes(p[..8].try_into().unwrap())
            .into_pyobject(py)?
            .into_any()),
        t::INT => Ok((i32_at(row, off)).into_pyobject(py)?.into_any()),
        t::INT2 => Ok((i16::from_le_bytes([p[0], p[1]]) as i64)
            .into_pyobject(py)?
            .into_any()),
        t::INT1 => Ok((p[0] as i8 as i64).into_pyobject(py)?.into_any()),
        t::DOUBLE => {
            Ok(PyFloat::new(py, f64::from_le_bytes(p[..8].try_into().unwrap())).into_any())
        }
        t::FLOAT => {
            Ok(PyFloat::new(py, f32::from_le_bytes(p[..4].try_into().unwrap()) as f64).into_any())
        }
        t::DATE => {
            let width = f.size.clamp(0, 8) as usize;
            let (y, m, d) = temporal::date_from_workspace(read_signed(&p[..width]));
            Ok(PyDate::new(py, to_i32(y)?, to_u8(m, "month")?, to_u8(d, "day")?)?.into_any())
        }
        t::TIME => {
            let width = f.size.clamp(0, 8) as usize;
            let (h, mi, s, us) = temporal::time_parts(read_signed(&p[..width]));
            let (h, mi, s) = (
                to_u8(h as i128, "hour")?,
                to_u8(mi as i128, "minute")?,
                to_u8(s as i128, "second")?,
            );
            Ok(PyTime::new(py, h, mi, s, us as u32, None)?.into_any())
        }
        t::TIMESTAMP => {
            if f.size != 8 {
                return Ok(py.None().into_bound(py));
            }
            let micros = i64::from_le_bytes(p[..8].try_into().unwrap());
            match temporal::timestamp_parts(micros) {
                None => Ok(py.None().into_bound(py)),
                Some((y, m, d, h, mi, s, us)) => Ok(PyDateTime::new(
                    py,
                    to_i32(y)?,
                    to_u8(m, "month")?,
                    to_u8(d, "day")?,
                    to_u8(h as i128, "hour")?,
                    to_u8(mi as i128, "minute")?,
                    to_u8(s as i128, "second")?,
                    us as u32,
                    None,
                )?
                .into_any()),
            }
        }
        t::NUMERIC => {
            let count = (f.true_size / 4) as usize;
            let scale = f.size & 0xFF;
            let coeff = numeric::coefficient(&p[..count * 4], count);
            let text = numeric::decimal_text(coeff, scale);
            pycache::decimal(py)?.call1((text,))
        }
        t::BOOL => Ok(PyBool::new(py, p[0] == 1).to_owned().into_any()),
        t::INTERVAL => {
            let time = read_signed(
                &p[..(if f.size >= 12 {
                    8
                } else {
                    (f.size - 4).max(0) as usize
                })],
            );
            let month = i32_at(row, off + (f.size - 4) as usize);
            pycache::interval(py)?.call1((time, 0i64, month))
        }
        t::TIMETZ => {
            let time = read_signed(
                &p[..(if f.size >= 12 {
                    8
                } else {
                    (f.size - 4).max(0) as usize
                })],
            );
            let zone = i32_at(row, off + (f.size - 4) as usize);
            pycache::timetz_out(py)?.call1((time, zone))
        }
        _ => Ok(py.None().into_bound(py)),
    }
}
