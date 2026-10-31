"""Selection of the native DBOS row decoder.

Provides ``_HAVE_NATIVE`` and ``_native_ext``. All modules that need the native
decoder read them through this module (never through a cached local name), so
monkeypatching ``_native._HAVE_NATIVE`` affects every consumer.

Two modes:

* native (default): ``nzpy_extended.rust_ext`` is used when it imports.
* pure Python: ``NZPY_EXTENDED_NO_NATIVE=1`` forces the pure-Python decoder.

If the native module cannot be imported and pure Python was not requested,
the pure-Python decoder is used; the two produce identical results.
"""

from __future__ import annotations

import importlib
import os
from typing import Any

_FORCE_PURE_PYTHON = os.environ.get("NZPY_EXTENDED_NO_NATIVE", "").lower() in (
    "1",
    "true",
    "yes",
)


def _load_native() -> Any:
    if _FORCE_PURE_PYTHON:
        return None
    try:
        return importlib.import_module(f"{__package__}.rust_ext")
    except ImportError:
        return None


_native_ext: Any = _load_native()
_HAVE_NATIVE: bool = _native_ext is not None
