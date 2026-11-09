//! Rust DBOS row decoder (`nzpy_extended.rust_ext`).
//!
//! Exposes `process_dbos_row` and `process_dbos_batch` with the same arguments
//! and exception contract:
//! - `BufferError`: row layout fault (the caller retires the connection);
//! - `ValueError` / `UnicodeDecodeError` / `OverflowError`: value fault
//!   (`process_dbos_row` raises it; `process_dbos_batch` stops before the row);
//! - `TypeError`: metadata is not a list.

mod datetime_capi;
mod fields;
// Public only so the cargo-fuzz targets in `rust/fuzz` can reach the pure
// framing and layout code. Not part of the documented API.
#[doc(hidden)]
pub mod frames;
#[doc(hidden)]
pub mod layout;
mod numeric;
mod nztype;
mod pycache;
mod temporal;

use pyo3::exceptions::{PyBufferError, PyTypeError};
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyList, PyTuple};
use std::borrow::Cow;

use fields::{Codecs, DecodeCache};
use layout::{FieldMeta, LayoutError, RowShape};

fn layout_error(e: LayoutError) -> PyErr {
    PyBufferError::new_err(format!(
        "Invalid RowStandard payload: {}; reconnect is required.",
        e.0
    ))
}

/// Column metadata as passed from Python: six parallel lists, one entry per field.
struct Metadata {
    shape: RowShape,
    fields: Vec<FieldMeta>,
    codecs: Codecs,
}

#[allow(clippy::too_many_arguments)]
fn load_metadata(
    py: Python<'_>,
    field_type: &Bound<'_, PyAny>,
    field_size: &Bound<'_, PyAny>,
    field_true_size: &Bound<'_, PyAny>,
    field_offset: &Bound<'_, PyAny>,
    field_fixed_size: &Bound<'_, PyAny>,
    field_phys_field: &Bound<'_, PyAny>,
    num_fields: i32,
    nulls_allowed: i32,
    fixed_fields_size: i32,
    num_varying: i32,
    char_enc: &str,
    client_enc: &str,
) -> PyResult<Metadata> {
    if num_fields < 0 || num_varying < 0 || fixed_fields_size < 0 {
        return Err(layout_error(LayoutError("field header is invalid".into())));
    }
    let lists = [
        field_phys_field,
        field_type,
        field_size,
        field_true_size,
        field_offset,
        field_fixed_size,
    ];
    let mut columns: Vec<Vec<i64>> = Vec::with_capacity(6);
    for list in lists {
        let list = list
            .cast::<PyList>()
            .map_err(|_| PyTypeError::new_err("field metadata must be lists"))?;
        if (list.len() as i64) < num_fields as i64 {
            return Err(layout_error(LayoutError(
                "field metadata is truncated".into(),
            )));
        }
        let values = list
            .iter()
            .take(num_fields as usize)
            .map(|v| v.extract::<i64>())
            .collect::<PyResult<Vec<i64>>>()?;
        columns.push(values);
    }
    let fields = (0..num_fields as usize)
        .map(|i| FieldMeta {
            phys: columns[0][i],
            ftype: columns[1][i],
            size: columns[2][i],
            true_size: columns[3][i],
            offset: columns[4][i],
            fixed: columns[5][i],
        })
        .collect();
    Ok(Metadata {
        shape: RowShape {
            num_fields: num_fields as i64,
            nulls_allowed: nulls_allowed != 0,
            fixed_fields_size: fixed_fields_size as i64,
            num_varying: num_varying as i64,
        },
        fields,
        codecs: Codecs::new(py, char_enc, client_enc)?,
    })
}

/// The payload bytes: zero-copy for `bytes`, a copy for any other buffer.
fn payload_bytes<'a>(buf: &'a Bound<'_, PyAny>) -> PyResult<Cow<'a, [u8]>> {
    if let Ok(b) = buf.cast::<PyBytes>() {
        return Ok(Cow::Borrowed(b.as_bytes()));
    }
    let view = pyo3::buffer::PyBuffer::<u8>::get(buf)?;
    Ok(Cow::Owned(view.to_vec(buf.py())?))
}

/// Record the failing column as an exception note (`column <index>`), which the
/// driver puts into its `DataError` message. The exception type is unchanged.
fn with_column(py: Python<'_>, err: PyErr, index: usize) -> PyErr {
    let _ = err
        .value(py)
        .call_method1("add_note", (format!("column {index}"),));
    err
}

/// Decode one row payload into a Python list.
fn decode_row<'py>(
    py: Python<'py>,
    meta: &Metadata,
    row: &[u8],
    cache: Option<&DecodeCache>,
) -> PyResult<Bound<'py, PyList>> {
    let starts = layout::resolve_starts(&meta.shape, &meta.fields, row).map_err(layout_error)?;
    let out = PyList::empty(py);
    for (index, (field, start)) in meta.fields.iter().zip(starts).enumerate() {
        let value = match start {
            None => py.None().into_bound(py),
            Some(off) => fields::decode_field(py, field, row, off, &meta.codecs, cache)
                .map_err(|e| with_column(py, e, index))?,
        };
        out.append(value)?;
    }
    Ok(out)
}

/// Decode one DBOS row payload.
#[pyfunction]
#[allow(clippy::too_many_arguments)]
fn process_dbos_row<'py>(
    py: Python<'py>,
    buf: &Bound<'py, PyAny>,
    field_type: &Bound<'py, PyAny>,
    field_size: &Bound<'py, PyAny>,
    field_true_size: &Bound<'py, PyAny>,
    field_offset: &Bound<'py, PyAny>,
    field_fixed_size: &Bound<'py, PyAny>,
    field_phys_field: &Bound<'py, PyAny>,
    num_fields: i32,
    nulls_allowed: i32,
    fixed_fields_size: i32,
    num_varying: i32,
    char_enc: &str,
    client_enc: &str,
) -> PyResult<Bound<'py, PyList>> {
    let meta = load_metadata(
        py,
        field_type,
        field_size,
        field_true_size,
        field_offset,
        field_fixed_size,
        field_phys_field,
        num_fields,
        nulls_allowed,
        fixed_fields_size,
        num_varying,
        char_enc,
        client_enc,
    )?;
    let data = payload_bytes(buf)?;
    decode_row(py, &meta, &data, None)
}

/// Decode consecutive `Y` frames. Returns `(rows, consumed_bytes)`. A partial
/// trailing frame, or a non-`Y` message, stops the scan and stays unconsumed.
fn batch_rows<'py>(
    py: Python<'py>,
    meta: &Metadata,
    data: &[u8],
    cache: Option<&DecodeCache>,
) -> PyResult<Bound<'py, PyTuple>> {
    let rows = PyList::empty(py);
    let mut pos = 0usize;
    while let Some((start, len)) = frames::next_row_frame(data, pos) {
        match decode_row(py, meta, &data[start..start + len], cache) {
            Ok(row) => rows.append(row)?,
            // A layout fault retires the connection. Any other failure is a value
            // that does not decode: stop in front of this row and leave it
            // unconsumed. The caller decodes that row alone, which defers the
            // error, so no row is decoded twice.
            Err(e) if e.is_instance_of::<PyBufferError>(py) => return Err(e),
            Err(_) => break,
        }
        pos = start + len;
    }
    PyTuple::new(py, [rows.into_any(), pos.into_pyobject(py)?.into_any()])
}

/// A decoder bound to one result set's metadata.
///
/// The driver creates one per tuple descriptor and reuses it for every row and
/// batch, so the metadata lists are parsed once instead of once per call.
#[pyclass(module = "nzpy_extended.rust_ext")]
struct DbosDecoder {
    meta: Metadata,
    cache: DecodeCache,
}

#[pymethods]
impl DbosDecoder {
    #[new]
    #[allow(clippy::too_many_arguments)]
    fn new(
        py: Python<'_>,
        field_type: &Bound<'_, PyAny>,
        field_size: &Bound<'_, PyAny>,
        field_true_size: &Bound<'_, PyAny>,
        field_offset: &Bound<'_, PyAny>,
        field_fixed_size: &Bound<'_, PyAny>,
        field_phys_field: &Bound<'_, PyAny>,
        num_fields: i32,
        nulls_allowed: i32,
        fixed_fields_size: i32,
        num_varying: i32,
        char_enc: &str,
        client_enc: &str,
    ) -> PyResult<Self> {
        Ok(Self {
            meta: load_metadata(
                py,
                field_type,
                field_size,
                field_true_size,
                field_offset,
                field_fixed_size,
                field_phys_field,
                num_fields,
                nulls_allowed,
                fixed_fields_size,
                num_varying,
                char_enc,
                client_enc,
            )?,
            cache: DecodeCache::default(),
        })
    }

    /// Decode one row payload into a Python list.
    fn row<'py>(&self, py: Python<'py>, buf: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyList>> {
        let data = payload_bytes(buf)?;
        decode_row(py, &self.meta, &data, Some(&self.cache))
    }

    /// Decode consecutive `Y` frames into `(rows, consumed_bytes)`.
    fn batch<'py>(
        &self,
        py: Python<'py>,
        buf: &Bound<'py, PyAny>,
    ) -> PyResult<Bound<'py, PyTuple>> {
        let data = payload_bytes(buf)?;
        batch_rows(py, &self.meta, &data, Some(&self.cache))
    }
}

/// Decode consecutive `Y` frames. Returns `(rows, consumed_bytes)`. A partial
/// trailing frame, or a non-`Y` message, stops the scan and stays unconsumed.
#[pyfunction]
#[allow(clippy::too_many_arguments)]
fn process_dbos_batch<'py>(
    py: Python<'py>,
    buf: &Bound<'py, PyAny>,
    field_type: &Bound<'py, PyAny>,
    field_size: &Bound<'py, PyAny>,
    field_true_size: &Bound<'py, PyAny>,
    field_offset: &Bound<'py, PyAny>,
    field_fixed_size: &Bound<'py, PyAny>,
    field_phys_field: &Bound<'py, PyAny>,
    num_fields: i32,
    nulls_allowed: i32,
    fixed_fields_size: i32,
    num_varying: i32,
    char_enc: &str,
    client_enc: &str,
) -> PyResult<Bound<'py, PyTuple>> {
    let meta = load_metadata(
        py,
        field_type,
        field_size,
        field_true_size,
        field_offset,
        field_fixed_size,
        field_phys_field,
        num_fields,
        nulls_allowed,
        fixed_fields_size,
        num_varying,
        char_enc,
        client_enc,
    )?;
    let data = payload_bytes(buf)?;
    batch_rows(py, &meta, &data, None)
}

#[pymodule]
fn rust_ext(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<DbosDecoder>()?;
    m.add_function(wrap_pyfunction!(process_dbos_row, m)?)?;
    m.add_function(wrap_pyfunction!(process_dbos_batch, m)?)?;
    Ok(())
}
