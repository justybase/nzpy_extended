"""Selection of the native decoder: environment variables and fallback warning.

``nzpy_extended._native`` decides at import time, so each test reloads it
under a controlled environment and restores the default afterwards.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
import warnings

import pytest

from nzpy_extended import _native

pytestmark = pytest.mark.unit

_ENV_NAMES = ("NZPY_EXTENDED_NO_NATIVE", "NZPY_EXTENDED_NO_CEXT")


@pytest.fixture(autouse=True)
def _restore_native_module(monkeypatch: pytest.MonkeyPatch):
    yield
    monkeypatch.undo()
    importlib.reload(_native)


def _reload(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> object:
    for name in _ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return importlib.reload(_native)


def test_no_native_env_selects_pure_python_without_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _reload(monkeypatch, {"NZPY_EXTENDED_NO_NATIVE": "1"})
    assert _native._HAVE_NATIVE is False
    assert not [w for w in caught if issubclass(w.category, (RuntimeWarning, FutureWarning))]


@pytest.mark.skipif(
    importlib.util.find_spec("nzpy_extended.rust_ext") is None,
    reason="native extension is not built",
)
def test_default_selects_native_module(monkeypatch: pytest.MonkeyPatch) -> None:
    _reload(monkeypatch, {})
    assert _native._HAVE_NATIVE is True
    assert _native._native_ext.__name__ == "nzpy_extended.rust_ext"


@pytest.mark.skipif(
    importlib.util.find_spec("nzpy_extended.rust_ext") is None,
    reason="native extension is not built",
)
def test_removed_no_cext_name_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    # The pre-0.8 name was removed in 0.9 and no longer selects pure Python.
    _reload(monkeypatch, {"NZPY_EXTENDED_NO_CEXT": "1"})
    assert _native._HAVE_NATIVE is True


def test_missing_native_module_warns_and_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    # A None entry in sys.modules makes the import raise ImportError.
    monkeypatch.setitem(sys.modules, "nzpy_extended.rust_ext", None)
    with pytest.warns(RuntimeWarning, match="falling back to the much slower pure-Python"):
        _reload(monkeypatch, {})
    assert _native._HAVE_NATIVE is False
