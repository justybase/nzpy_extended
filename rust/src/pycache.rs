//! Lazily imported Python callables used by the decoder.

use pyo3::prelude::*;
use pyo3::sync::PyOnceLock;

static DECIMAL: PyOnceLock<Py<PyAny>> = PyOnceLock::new();
static INTERVAL: PyOnceLock<Py<PyAny>> = PyOnceLock::new();
static TIMETZ_OUT: PyOnceLock<Py<PyAny>> = PyOnceLock::new();

fn cached<'py>(
    py: Python<'py>,
    cell: &PyOnceLock<Py<PyAny>>,
    module: &str,
    attr: &str,
) -> PyResult<Bound<'py, PyAny>> {
    cell.get_or_try_init(py, || Ok(py.import(module)?.getattr(attr)?.unbind()))
        .map(|obj| obj.bind(py).clone())
}

/// `decimal.Decimal`.
pub fn decimal(py: Python<'_>) -> PyResult<Bound<'_, PyAny>> {
    cached(py, &DECIMAL, "decimal", "Decimal")
}

/// `nzpy_extended.types.Interval`.
pub fn interval(py: Python<'_>) -> PyResult<Bound<'_, PyAny>> {
    cached(py, &INTERVAL, "nzpy_extended.types", "Interval")
}

/// `nzpy_extended.types.timetz_out_timetzadt`.
pub fn timetz_out(py: Python<'_>) -> PyResult<Bound<'_, PyAny>> {
    cached(
        py,
        &TIMETZ_OUT,
        "nzpy_extended.types",
        "timetz_out_timetzadt",
    )
}
