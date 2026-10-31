"""Selection of the native DBOS row decoder.

Provides ``_HAVE_NATIVE`` and ``_native_ext``. All modules that need the native
decoder read them through this module (never through a cached local name), so
monkeypatching ``_native._HAVE_NATIVE`` affects every consumer.

Two modes:

* native (default): ``nzpy_extended.rust_ext`` is used when it imports.
* pure Python: ``NZPY_EXTENDED_NO_NATIVE=1`` forces the pure-Python decoder.

If the native module cannot be imported and pure Python was not requested,
the pure-Python decoder is used with a ``RuntimeWarning``. Both decoders
produce identical results; the pure-Python one is much slower.
"""

from __future__ import annotations

import importlib
import os
import warnings
from typing import Any

_TRUE_VALUES = ("1", "true", "yes")
# Accepted for one release after the rename; remove in 0.8.
_DEPRECATED_NO_NATIVE = "NZPY_EXTENDED_NO_CEXT"


def _pure_python_requested() -> bool:
    if os.environ.get("NZPY_EXTENDED_NO_NATIVE", "").lower() in _TRUE_VALUES:
        return True
    if os.environ.get(_DEPRECATED_NO_NATIVE, "").lower() in _TRUE_VALUES:
        warnings.warn(
            f"{_DEPRECATED_NO_NATIVE} is deprecated; use NZPY_EXTENDED_NO_NATIVE. "
            "The old name will be removed in 0.8.",
            FutureWarning,
            stacklevel=2,
        )
        return True
    return False


def _load_native() -> Any:
    if _pure_python_requested():
        return None
    try:
        return importlib.import_module(f"{__package__}.rust_ext")
    except ImportError as exc:
        warnings.warn(
            "nzpy_extended: the native DBOS decoder (rust_ext) could not be "
            f"imported ({exc}); falling back to the much slower pure-Python "
            "decoder. Reinstall the package, or set NZPY_EXTENDED_NO_NATIVE=1 "
            "to choose pure Python explicitly and silence this warning.",
            RuntimeWarning,
            stacklevel=2,
        )
        return None


_native_ext: Any = _load_native()
_HAVE_NATIVE: bool = _native_ext is not None
