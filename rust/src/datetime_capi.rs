//! Minimal binding to the public `datetime` C API.
//!
//! PyO3's limited-API build constructs `date`/`time`/`datetime` by calling the
//! Python classes. Importing the capsule (`PyCapsule_Import`) is in the stable
//! ABI, but the `PyDateTime_CAPI` struct layout is not: `datetime.h` is outside
//! the limited API. This binding relies on CPython keeping that struct
//! append-only (it has since 3.7), which the CI wheel tests exercise on every
//! supported Python version.

use std::ffi::{c_int, CStr};
use std::ptr;
use std::sync::atomic::{AtomicPtr, Ordering};

use pyo3::exceptions::PyRuntimeError;
use pyo3::ffi;
use pyo3::prelude::*;

/// The prefix of CPython's `PyDateTime_CAPI` needed here.
///
/// The struct is append-only, so the offsets of these fields are stable on
/// every supported Python version (3.11+).
#[repr(C)]
#[allow(dead_code)] // Fields before the constructors exist only to keep the layout.
struct DateTimeCapi {
    date_type: *mut ffi::PyTypeObject,
    datetime_type: *mut ffi::PyTypeObject,
    time_type: *mut ffi::PyTypeObject,
    delta_type: *mut ffi::PyTypeObject,
    tzinfo_type: *mut ffi::PyTypeObject,
    timezone_utc: *mut ffi::PyObject,
    date_from_date:
        unsafe extern "C" fn(c_int, c_int, c_int, *mut ffi::PyTypeObject) -> *mut ffi::PyObject,
    datetime_from_date_and_time: unsafe extern "C" fn(
        c_int,
        c_int,
        c_int,
        c_int,
        c_int,
        c_int,
        c_int,
        *mut ffi::PyObject,
        *mut ffi::PyTypeObject,
    ) -> *mut ffi::PyObject,
    time_from_time: unsafe extern "C" fn(
        c_int,
        c_int,
        c_int,
        c_int,
        *mut ffi::PyObject,
        *mut ffi::PyTypeObject,
    ) -> *mut ffi::PyObject,
}

static DATETIME_CAPI: AtomicPtr<DateTimeCapi> = AtomicPtr::new(ptr::null_mut());

const CAPSULE_NAME: &CStr = c"datetime.datetime_CAPI";

fn api() -> PyResult<&'static DateTimeCapi> {
    let mut cached = DATETIME_CAPI.load(Ordering::Acquire);
    if cached.is_null() {
        // SAFETY: the GIL is held; a null return means an exception is set.
        unsafe {
            cached = ffi::PyCapsule_Import(CAPSULE_NAME.as_ptr(), 0).cast();
        }
        if cached.is_null() {
            return Err(PyRuntimeError::new_err(
                "failed to import the datetime C API",
            ));
        }
        let _ = DATETIME_CAPI.compare_exchange(
            ptr::null_mut(),
            cached,
            Ordering::Release,
            Ordering::Relaxed,
        );
    }
    // SAFETY: the capsule owns a process-wide, immutable API table.
    unsafe { Ok(&*cached) }
}

pub fn date(py: Python<'_>, year: i32, month: u8, day: u8) -> PyResult<Bound<'_, PyAny>> {
    // SAFETY: the constructor returns a new reference, or null with an exception set.
    unsafe {
        let api = api()?;
        Bound::from_owned_ptr_or_err(
            py,
            (api.date_from_date)(year, month.into(), day.into(), api.date_type),
        )
    }
}

pub fn time(
    py: Python<'_>,
    hour: u8,
    minute: u8,
    second: u8,
    microsecond: u32,
) -> PyResult<Bound<'_, PyAny>> {
    // SAFETY: as above; `Py_None` is a borrowed immortal object accepted for tzinfo.
    unsafe {
        let api = api()?;
        Bound::from_owned_ptr_or_err(
            py,
            (api.time_from_time)(
                hour.into(),
                minute.into(),
                second.into(),
                microsecond as c_int,
                ffi::Py_None(),
                api.time_type,
            ),
        )
    }
}

#[allow(clippy::too_many_arguments)]
pub fn datetime(
    py: Python<'_>,
    year: i32,
    month: u8,
    day: u8,
    hour: u8,
    minute: u8,
    second: u8,
    microsecond: u32,
) -> PyResult<Bound<'_, PyAny>> {
    // SAFETY: as above; `Py_None` is a borrowed immortal object accepted for tzinfo.
    unsafe {
        let api = api()?;
        Bound::from_owned_ptr_or_err(
            py,
            (api.datetime_from_date_and_time)(
                year,
                month.into(),
                day.into(),
                hour.into(),
                minute.into(),
                second.into(),
                microsecond as c_int,
                ffi::Py_None(),
                api.datetime_type,
            ),
        )
    }
}
