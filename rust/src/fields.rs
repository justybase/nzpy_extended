//! Per-field value decoding (needs the Python interpreter).
//!
//! Each arm mirrors `DbosParser._build_dbos_row_python` in
//! `nzpy_extended/_dbos.py`, which is the reference behaviour.

use std::collections::HashMap;
use std::ffi::CStr;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::Mutex;

use pyo3::exceptions::PyValueError;
use pyo3::ffi;
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyBytes, PyFloat, PyString};

use crate::layout::FieldMeta;
use crate::nztype as t;
use crate::{datetime_capi, numeric, pycache, temporal};

/// How text in one codec is decoded. Chosen once per codec name, from the
/// canonical name the codec registry reports.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TextKind {
    /// UTF-8: valid input is decoded directly.
    Utf8,
    /// ISO-8859-1: every byte is a code point, so no codec lookup is needed.
    Latin1,
    /// ASCII-compatible single-byte codec: ASCII input is valid UTF-8 and decodes
    /// the same under any of these codecs.
    AsciiCompatible,
    /// Anything else, and names the registry does not know: the codec path.
    Other,
}

/// Classify a canonical codec name (the `name` attribute of `codecs.lookup`).
///
/// The list is explicit on purpose: every entry is an ASCII-compatible codec.
/// `tests/test_text_codec_unit.py` checks the ASCII identity for each entry.
pub fn classify(canonical: &str) -> TextKind {
    match canonical {
        "utf-8" => TextKind::Utf8,
        "iso8859-1" => TextKind::Latin1,
        "ascii" => TextKind::AsciiCompatible,
        c if c.starts_with("iso8859-") || c.starts_with("cp125") => TextKind::AsciiCompatible,
        _ => TextKind::Other,
    }
}

/// Canonical name from the codec registry, or `None` when the registry does not know it.
fn canonical_codec_name(py: Python<'_>, name: &str) -> Option<String> {
    let codecs = py.import("codecs").ok()?;
    let info = codecs.call_method1("lookup", (name,)).ok()?;
    info.getattr("name").ok()?.extract::<String>().ok()
}

/// Cached `classify(canonical_codec_name(name))`, keyed by the name as given.
///
/// The lock is not held across the registry call, which can import code.
fn text_kind(py: Python<'_>, name: &str) -> TextKind {
    static KINDS: Mutex<Option<HashMap<String, TextKind>>> = Mutex::new(None);
    if let Ok(guard) = KINDS.lock() {
        if let Some(kind) = guard.as_ref().and_then(|m| m.get(name)) {
            return *kind;
        }
    }
    let kind =
        canonical_codec_name(py, name).map_or(TextKind::Other, |canonical| classify(&canonical));
    if let Ok(mut guard) = KINDS.lock() {
        guard
            .get_or_insert_with(HashMap::new)
            .insert(name.to_owned(), kind);
    }
    kind
}

/// Client and CHAR/VARCHAR codecs, as NUL-terminated names for `PyUnicode_Decode`.
pub struct Codecs {
    pub char_enc: std::ffi::CString,
    pub client_enc: std::ffi::CString,
    char_kind: TextKind,
    client_kind: TextKind,
}

impl Codecs {
    pub fn new(py: Python<'_>, char_enc: &str, client_enc: &str) -> PyResult<Self> {
        let to_c = |s: &str| {
            std::ffi::CString::new(s)
                .map_err(|_| PyValueError::new_err("encoding name contains NUL"))
        };
        Ok(Codecs {
            char_kind: text_kind(py, char_enc),
            client_kind: text_kind(py, client_enc),
            char_enc: to_c(char_enc)?,
            client_enc: to_c(client_enc)?,
        })
    }
}

/// Decides whether a cell cache is paying off.
///
/// The first `probe_limit` lookups are counted. If fewer than 2% of them hit,
/// the cache is disabled for the rest of the result set. High-cardinality
/// columns otherwise pay lock/hash/insert cost on every cell for no reuse.
/// The decision resets with every decoder, i.e. with every result set.
struct CacheState {
    enabled: AtomicBool,
    lookups: AtomicUsize,
    hits: AtomicUsize,
    probe_limit: usize,
}

impl CacheState {
    fn new(probe_limit: usize) -> Self {
        Self {
            enabled: AtomicBool::new(true),
            lookups: AtomicUsize::new(0),
            hits: AtomicUsize::new(0),
            probe_limit,
        }
    }

    fn use_cache(&self) -> bool {
        self.enabled.load(Ordering::Relaxed)
    }

    fn record(&self, hit: bool) {
        // After the probe the decision is fixed; stop paying atomic RMW per
        // cell. `lookups`/`hits` are diagnostics only and are not exposed.
        if self.lookups.load(Ordering::Relaxed) >= self.probe_limit {
            return;
        }
        let lookups = self.lookups.fetch_add(1, Ordering::Relaxed) + 1;
        if hit {
            self.hits.fetch_add(1, Ordering::Relaxed);
        }
        if lookups == self.probe_limit {
            let hits = self.hits.load(Ordering::Relaxed);
            if hits * 50 < lookups {
                self.enabled.store(false, Ordering::Relaxed);
            }
        }
    }
}

/// Bounded memoization for immutable values that repeat across rows.
///
/// `NUMERIC` decoding formats text and calls `Decimal` per cell, which is the
/// most expensive field type. Fact tables commonly repeat prices, and dates
/// repeat even more, so returning the same immutable object for an identical
/// value is safe and skips the constructor. The caps bound memory for results
/// with high cardinality; the probe turns the cache off entirely when there is
/// nothing to reuse.
struct ColumnCache {
    entries: HashMap<Vec<u8>, Py<PyAny>>,
}

pub struct DecodeCache {
    numerics: Mutex<HashMap<(i128, i64), Py<PyAny>>>,
    timestamps: Mutex<HashMap<i64, Py<PyAny>>>,
    columns: Vec<Mutex<ColumnCache>>,
    column_states: Vec<CacheState>,
    numeric_state: CacheState,
    timestamp_state: CacheState,
}

const NUMERIC_CACHE_LIMIT: usize = 131_072;
const TIMESTAMP_CACHE_LIMIT: usize = 8_192;
const COLUMN_CACHE_LIMIT: usize = 4_096;
const COLUMN_MAX_KEY_BYTES: usize = 64;
const COLUMN_PROBE_LOOKUPS: usize = 8_192;

impl DecodeCache {
    pub(crate) fn new(columns: usize) -> Self {
        Self {
            numerics: Mutex::new(HashMap::new()),
            timestamps: Mutex::new(HashMap::new()),
            columns: (0..columns)
                .map(|_| {
                    Mutex::new(ColumnCache {
                        entries: HashMap::new(),
                    })
                })
                .collect(),
            column_states: (0..columns)
                .map(|_| CacheState::new(COLUMN_PROBE_LOOKUPS))
                .collect(),
            numeric_state: CacheState::new(32_768),
            timestamp_state: CacheState::new(8_192),
        }
    }
    fn decimal<'py>(
        &self,
        py: Python<'py>,
        coefficient: i128,
        scale: i64,
        text: &str,
    ) -> PyResult<Bound<'py, PyAny>> {
        let use_cache = self.numeric_state.use_cache();
        if use_cache {
            if let Ok(cache) = self.numerics.lock() {
                if let Some(value) = cache.get(&(coefficient, scale)) {
                    let result = value.bind(py).clone();
                    drop(cache);
                    self.numeric_state.record(true);
                    return Ok(result);
                }
            }
        }
        let value = pycache::decimal(py)?.call1((text,))?;
        if use_cache {
            if let Ok(mut cache) = self.numerics.lock() {
                if cache.len() < NUMERIC_CACHE_LIMIT {
                    cache.insert((coefficient, scale), value.clone().unbind());
                }
            }
            self.numeric_state.record(false);
        }
        Ok(value)
    }

    /// Memoize one immutable cell by its exact wire bytes.
    ///
    /// Used for text, DATE, TIME and TIMETZ, where the decoded Python object
    /// is immutable and repeated values are common (dimensions, status flags,
    /// dates). The per-column map is bounded and disables itself when the
    /// probe finds almost no repetition.
    fn cached_bytes<'py, F>(
        &self,
        py: Python<'py>,
        column: usize,
        key: &[u8],
        create: F,
    ) -> PyResult<Bound<'py, PyAny>>
    where
        F: FnOnce() -> PyResult<Bound<'py, PyAny>>,
    {
        let state = &self.column_states[column];
        if !state.use_cache() {
            return create();
        }
        let cache = &self.columns[column];
        let cacheable = key.len() <= COLUMN_MAX_KEY_BYTES;
        if cacheable {
            if let Ok(guard) = cache.lock() {
                if let Some(value) = guard.entries.get(key) {
                    let result = value.bind(py).clone();
                    drop(guard);
                    state.record(true);
                    return Ok(result);
                }
            }
        }
        state.record(false);

        let value = create()?;
        if cacheable && !value.is_none() {
            if let Ok(mut guard) = cache.lock() {
                if state.use_cache() && guard.entries.len() < COLUMN_CACHE_LIMIT {
                    guard.entries.insert(key.to_vec(), value.clone().unbind());
                }
            }
        }
        Ok(value)
    }

    fn timestamp<'py>(&self, py: Python<'py>, micros: i64) -> PyResult<Bound<'py, PyAny>> {
        let use_cache = self.timestamp_state.use_cache();
        if use_cache {
            if let Ok(cache) = self.timestamps.lock() {
                if let Some(value) = cache.get(&micros) {
                    let result = value.bind(py).clone();
                    drop(cache);
                    self.timestamp_state.record(true);
                    return Ok(result);
                }
            }
        }
        let value = match temporal::timestamp_parts(micros) {
            None => py.None().into_bound(py),
            Some((y, m, d, h, mi, s, us)) => datetime_capi::datetime(
                py,
                to_i32(y)?,
                to_u8(m, "month")?,
                to_u8(d, "day")?,
                to_u8(h as i128, "hour")?,
                to_u8(mi as i128, "minute")?,
                to_u8(s as i128, "second")?,
                us as u32,
            )?,
        };
        if use_cache {
            if !value.is_none() {
                if let Ok(mut cache) = self.timestamps.lock() {
                    if cache.len() < TIMESTAMP_CACHE_LIMIT {
                        cache.insert(micros, value.clone().unbind());
                    }
                }
            }
            self.timestamp_state.record(false);
        }
        Ok(value)
    }
}

/// Strict decode, the same as `str(bytes, encoding)`.
///
/// Fast paths only where the result is identical to the codec's: valid UTF-8,
/// ASCII under an ASCII-compatible codec, and any byte under ISO-8859-1.
/// Everything else (including invalid UTF-8, so that the exact codec error is
/// raised) goes through `PyUnicode_Decode`, the same codec path Python uses.
fn decode<'py>(
    py: Python<'py>,
    bytes: &[u8],
    enc: &CStr,
    kind: TextKind,
) -> PyResult<Bound<'py, PyString>> {
    let utf8_fast = match kind {
        TextKind::Utf8 => true,
        TextKind::AsciiCompatible => bytes.is_ascii(),
        TextKind::Latin1 | TextKind::Other => false,
    };
    if utf8_fast {
        if let Ok(s) = std::str::from_utf8(bytes) {
            return Ok(PyString::new(py, s));
        }
    }
    // SAFETY: `bytes` is valid for `len` bytes for the duration of the call.
    // ISO-8859-1 maps every byte, so this cannot fail on content.
    if kind == TextKind::Latin1 {
        unsafe {
            let ptr = ffi::PyUnicode_DecodeLatin1(
                bytes.as_ptr().cast(),
                bytes.len() as ffi::Py_ssize_t,
                std::ptr::null(),
            );
            return Bound::from_owned_ptr_or_err(py, ptr)?
                .cast_into::<PyString>()
                .map_err(Into::into);
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
    column: usize,
    codecs: &Codecs,
    cache: Option<&DecodeCache>,
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
            let raw = &row[off..off + width];
            let create = || {
                let text = decode(py, raw, &codecs.char_enc, codecs.char_kind)?;
                pad_text(py, text, width)
            };
            match cache {
                Some(cache) => cache.cached_bytes(py, column, raw, create),
                None => create(),
            }
        }
        t::NCHAR | t::NVARCHAR => {
            let cursize = (u16::from_le_bytes([p[0], p[1]]) as usize).saturating_sub(2);
            let raw = &row[off + 2..off + 2 + cursize];
            let create = || {
                let text = decode(py, raw, &codecs.client_enc, codecs.client_kind)?;
                if ftype == t::NCHAR {
                    pad_text(py, text, f.size.max(0) as usize)
                } else {
                    Ok(text.into_any())
                }
            };
            match cache {
                Some(cache) => cache.cached_bytes(py, column, raw, create),
                None => create(),
            }
        }
        t::VARBINARY | t::GEOMETRY => {
            // Binary storage: the bytes as stored, no text decoding.
            let cursize = (u16::from_le_bytes([p[0], p[1]]) as usize).saturating_sub(2);
            Ok(PyBytes::new(py, &row[off + 2..off + 2 + cursize]).into_any())
        }
        t::VARCHAR | t::VARFIXEDCHAR | t::JSON | t::JSONB | t::JSONPATH | t::VECTOR => {
            let cursize = (u16::from_le_bytes([p[0], p[1]]) as usize).saturating_sub(2);
            let raw = &row[off + 2..off + 2 + cursize];
            let create = || {
                let text = decode(py, raw, &codecs.char_enc, codecs.char_kind)?;
                Ok(text.into_any())
            };
            match cache {
                Some(cache) => cache.cached_bytes(py, column, raw, create),
                None => create(),
            }
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
            let raw = &p[..width];
            let create = || {
                let (y, m, d) = temporal::date_from_workspace(read_signed(raw));
                datetime_capi::date(py, to_i32(y)?, to_u8(m, "month")?, to_u8(d, "day")?)
            };
            match cache {
                Some(cache) => cache.cached_bytes(py, column, raw, create),
                None => create(),
            }
        }
        t::TIME => {
            let width = f.size.clamp(0, 8) as usize;
            let raw = &p[..width];
            let create = || {
                let (h, mi, s, us) = temporal::time_parts(read_signed(raw));
                let (h, mi, s) = (
                    to_u8(h as i128, "hour")?,
                    to_u8(mi as i128, "minute")?,
                    to_u8(s as i128, "second")?,
                );
                datetime_capi::time(py, h, mi, s, us as u32)
            };
            match cache {
                Some(cache) => cache.cached_bytes(py, column, raw, create),
                None => create(),
            }
        }
        t::TIMESTAMP => {
            if f.size != 8 {
                return Err(PyValueError::new_err(format!(
                    "TIMESTAMP storage is {} bytes, expected 8",
                    f.size
                )));
            }
            let micros = i64::from_le_bytes(p[..8].try_into().unwrap());
            match cache {
                Some(cache) => cache.timestamp(py, micros),
                None => match temporal::timestamp_parts(micros) {
                    None => Ok(py.None().into_bound(py)),
                    Some((y, m, d, h, mi, s, us)) => datetime_capi::datetime(
                        py,
                        to_i32(y)?,
                        to_u8(m, "month")?,
                        to_u8(d, "day")?,
                        to_u8(h as i128, "hour")?,
                        to_u8(mi as i128, "minute")?,
                        to_u8(s as i128, "second")?,
                        us as u32,
                    ),
                },
            }
        }
        t::NUMERIC => {
            let count = (f.true_size / 4) as usize;
            let scale = f.size & 0xFF;
            let coeff = numeric::coefficient(&p[..count * 4], count);
            // Format into a stack buffer: an owned String per cell was a
            // measurable part of the NUMERIC cost.
            let mut buf = [0u8; numeric::DECIMAL_TEXT_CAPACITY];
            let len = numeric::decimal_text_into(coeff, scale, &mut buf);
            // SAFETY: decimal_text_into writes only ASCII digits, '-', 'E'.
            let text = unsafe { std::str::from_utf8_unchecked(&buf[..len]) };
            match cache {
                Some(cache) => cache.decimal(py, coeff, scale, text),
                None => pycache::decimal(py)?.call1((text,)),
            }
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
            let raw = &row[off..off + f.size.max(0) as usize];
            let create = || {
                let time = read_signed(
                    &p[..(if f.size >= 12 {
                        8
                    } else {
                        (f.size - 4).max(0) as usize
                    })],
                );
                let zone = i32_at(row, off + (f.size - 4) as usize);
                pycache::timetz_out(py)?.call1((time, zone))
            };
            match cache {
                Some(cache) => cache.cached_bytes(py, column, raw, create),
                None => create(),
            }
        }
        _ => Ok(py.None().into_bound(py)),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn classifies_registry_names() {
        assert_eq!(classify("utf-8"), TextKind::Utf8);
        assert_eq!(classify("iso8859-1"), TextKind::Latin1);
        assert_eq!(classify("ascii"), TextKind::AsciiCompatible);
        assert_eq!(classify("iso8859-15"), TextKind::AsciiCompatible);
        assert_eq!(classify("cp1252"), TextKind::AsciiCompatible);
        // Not ASCII-compatible: must keep the full codec path.
        assert_eq!(classify("utf-16"), TextKind::Other);
        assert_eq!(classify("cp037"), TextKind::Other);
        assert_eq!(classify("shift_jis"), TextKind::Other);
    }

    #[test]
    fn cache_disables_after_a_probe_with_no_hits() {
        let state = CacheState::new(4);
        for _ in 0..4 {
            state.record(false);
        }
        assert!(!state.use_cache());
    }

    #[test]
    fn cache_stays_enabled_when_values_repeat() {
        let state = CacheState::new(4);
        state.record(false);
        state.record(true);
        state.record(true);
        state.record(true);
        assert!(state.use_cache());
    }
}
