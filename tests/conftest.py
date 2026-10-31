import pytest
import os
import sys

# Add the project root directory to sys.path so pytest can find nzpy_extended
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import nzpy_extended as nzpy

# Canonical name wins, including in legacy test modules reading NZ_DEV_DB.
if os.environ.get("NZ_DEV_DATABASE"):
    os.environ["NZ_DEV_DB"] = os.environ["NZ_DEV_DATABASE"]


def pytest_collection_modifyitems(items):
    configured = all(os.environ.get(key) for key in
                     ("NZ_DEV_HOST", "NZ_DEV_USER", "NZ_DEV_PASSWORD", "NZ_DEV_DB"))
    if not configured:
        for item in items:
            if item.get_closest_marker("unit") is None:
                item.add_marker(pytest.mark.skip(reason="Live tests require explicit NZ_DEV_* configuration"))


# ---------------------------------------------------------------------------
# Connection parameters — mirrors C# Config.cs exactly
# ---------------------------------------------------------------------------
NZ_HOST     = os.environ.get("NZ_DEV_HOST",     "192.168.0.144")
NZ_PORT     = int(os.environ.get("NZ_DEV_PORT",  "5480"))
NZ_DB       = os.environ.get("NZ_DEV_DATABASE") or os.environ.get("NZ_DEV_DB", "JUST_DATA")
NZ_USER     = os.environ.get("NZ_DEV_USER",      "admin")
NZ_PASSWORD = os.environ.get("NZ_DEV_PASSWORD",  "password")


@pytest.fixture(scope="class")
def db_kwargs():
    return {
        "user":     NZ_USER,
        "password": NZ_PASSWORD,
        "database": NZ_DB,
        "host":     NZ_HOST,
        "port":     NZ_PORT,
    }


# function-scoped alias for module-level tests
@pytest.fixture
def db_kwargs_fn():
    return {
        "user":     NZ_USER,
        "password": NZ_PASSWORD,
        "database": NZ_DB,
        "host":     NZ_HOST,
        "port":     NZ_PORT,
    }


@pytest.fixture
async def con(db_kwargs_fn):
    """Fresh async connection, closed after every test."""
    conn = await nzpy.connect(**db_kwargs_fn)
    yield conn
    try:
        await conn.close()
    except Exception:
        pass


@pytest.fixture
async def cursor(con):
    """Cursor on the test connection."""
    c = con.cursor()
    yield c
    try:
        await c.close()
    except Exception:
        pass


@pytest.fixture
def synccon(db_kwargs_fn):
    """Fresh sync connection, closed after every test."""
    import nzpy_extended.sync as sync_nzpy

    conn = sync_nzpy.connect(**db_kwargs_fn)
    yield conn
    try:
        conn.close()
    except Exception:
        pass


@pytest.fixture
def is_java():
    return "java" in sys.platform.lower()


# ---------------------------------------------------------------------------
# Native extension parity fixture
# ---------------------------------------------------------------------------

_NATIVE_ORIGINAL_FLAG = None


@pytest.fixture(params=[
    pytest.param(True, id="native"),
    pytest.param(False, id="python"),
])
def native_mode(request, monkeypatch):
    """
    Fixture parametrizing native extension usage.

    Yields bool indicating whether native extension should be used.
    When ``False``, monkeypatches ``nzpy_extended._native._HAVE_NATIVE``
    so that DBOS payload processing falls back to pure Python.
    """
    import nzpy_extended._native as _native

    use_native_ext = request.param

    # Global cache so the second parametrized run retains the original flag
    global _NATIVE_ORIGINAL_FLAG
    if _NATIVE_ORIGINAL_FLAG is None:
        _NATIVE_ORIGINAL_FLAG = getattr(_native, "_HAVE_NATIVE", False)

    if use_native_ext:
        # Forcing the flag on without a loaded module (NZPY_EXTENDED_NO_NATIVE=1,
        # or the import failed) would make the decoder assert; skip instead.
        if _native._native_ext is None:
            pytest.skip("native extension is not loaded in this run")
        monkeypatch.setattr(_native, "_HAVE_NATIVE", True)
    else:
        monkeypatch.setattr(_native, "_HAVE_NATIVE", False)

    yield use_native_ext

    # Restore original after test
    monkeypatch.setattr(_native, "_HAVE_NATIVE", _NATIVE_ORIGINAL_FLAG)


@pytest.fixture
async def con_native(db_kwargs_fn, native_mode):
    """Connection with pure-Python and native-ext parametrization, closed after test."""
    conn = await nzpy.connect(**db_kwargs_fn)
    yield conn
    try:
        await conn.close()
    except Exception:
        pass
