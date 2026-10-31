"""Parity of the native DBOS decoder (``rust_ext``) against the Python reference.

The pure-Python decoder (``DbosParser._build_dbos_row_python``) is the reference.
Rows in the appliance's observed shapes, mutated rows, and out-of-spec widths
must all decode identically on the native backend.
"""

from __future__ import annotations

import importlib
import random
from types import SimpleNamespace
from typing import Any

import pytest

from nzpy_extended import _native
from nzpy_extended import protocol as P
from nzpy_extended._dbos import DbosParser
from tests._dbos_builder import (
    ENCODINGS,
    Field,
    build_row,
    canon,
    desc_obj,
    random_row,
)

pytestmark = pytest.mark.unit


def _load(name: str) -> Any | None:
    try:
        return importlib.import_module(f"nzpy_extended.{name}")
    except ImportError:
        return None


NATIVE = _load("rust_ext")

needs_native = pytest.mark.skipif(NATIVE is None, reason="native extension is not built")


def _decode(payload: bytes, desc: dict[str, Any], enc: tuple[str, str], native: Any | None) -> list[Any]:
    """Outcome of one row through the public path: ``["ok", row]`` or ``["exc", class]``."""
    conn = SimpleNamespace(_char_varchar_encoding=enc[0], _client_encoding=enc[1])
    parser = DbosParser(conn)  # type: ignore[arg-type]
    cursor = SimpleNamespace(cached_rows=[])
    old_flag, old_mod = _native._HAVE_NATIVE, _native._native_ext
    _native._HAVE_NATIVE = native is not None
    _native._native_ext = native
    try:
        parser._process_dbos_payload(cursor, desc_obj(desc), memoryview(payload))  # type: ignore[arg-type]
        return ["ok", [canon(v) for v in cursor.cached_rows[0]]]
    except Exception as exc:  # noqa: BLE001 - the exception class is the contract
        return ["exc", type(exc).__name__]
    finally:
        _native._HAVE_NATIVE, _native._native_ext = old_flag, old_mod


def _cases(seed: int, count: int, in_spec: bool, mutated_ok: bool) -> list[tuple[bytes, dict[str, Any], tuple[str, str]]]:
    rng = random.Random(seed)
    out = []
    while len(out) < count:
        enc = rng.choice(ENCODINGS)
        payload, desc, mutated = random_row(rng, enc, in_spec=in_spec)
        if mutated and not mutated_ok:
            continue
        out.append((payload, desc, enc))
    return out


def _assert_parity(cases: list[tuple[bytes, dict[str, Any], tuple[str, str]]], native: Any) -> None:
    mismatches = []
    for payload, desc, enc in cases:
        want = _decode(payload, desc, enc, None)
        got = _decode(payload, desc, enc, native)
        if got != want:
            mismatches.append((payload.hex(), want, got))
    assert not mismatches, f"{len(mismatches)} mismatches, first: {mismatches[:1]}"


@needs_native
def test_native_matches_python_on_appliance_shaped_rows() -> None:
    _assert_parity(_cases(seed=101, count=1500, in_spec=True, mutated_ok=False), NATIVE)


@needs_native
def test_native_matches_python_on_mutated_rows() -> None:
    _assert_parity(_cases(seed=202, count=3000, in_spec=True, mutated_ok=True), NATIVE)


@needs_native
def test_native_matches_python_on_out_of_spec_widths() -> None:
    _assert_parity(_cases(seed=303, count=1500, in_spec=False, mutated_ok=True), NATIVE)


@needs_native
def test_nchar_keeps_non_latin1_text_intact() -> None:
    # Regression: padding built the result as a 1-byte-kind string (maxchar 127),
    # so U+017C ('ż') was stored as its low byte and came back as '|'. Found on
    # a live Netezza 11.2 appliance.
    fields = [Field(P.NzTypeNChar, 6, 6, False, False, "ż".encode("utf-8"))]
    row, desc = build_row(fields, 0, random.Random(0))
    want = _decode(row, desc, ("utf-8", "utf-8"), None)
    assert want == ["ok", [["str", "ż     "]]]
    assert _decode(row, desc, ("utf-8", "utf-8"), NATIVE) == want


@needs_native
def test_native_layout_fault_raises_buffer_error() -> None:
    desc = {
        "field_type": [P.NzTypeInt],
        "field_size": [4],
        "field_trueSize": [4],
        "field_offset": [3],
        "field_fixedSize": [4],
        "field_physField": [0],
        "numFields": 1,
        "nullsAllowed": 0,
        "fixedFieldsSize": 7,
        "numVaryingFields": 0,
    }
    with pytest.raises(BufferError, match="reconnect is required"):
        NATIVE.process_dbos_row(b"\x00\x00\x00\x01", *_meta(desc), 1, 0, 7, 0, "utf-8", "utf-8")


@needs_native
def test_native_accepts_bytes_and_mutable_buffers_alike() -> None:
    rng = random.Random(7)
    payload, desc = build_row([Field(P.NzTypeInt, 4, 4, False, True, (-5).to_bytes(4, "little", signed=True))], 0, rng)
    args = (*_meta(desc), desc["numFields"], desc["nullsAllowed"], desc["fixedFieldsSize"], desc["numVaryingFields"],
            "utf-8", "utf-8")
    from_bytes = NATIVE.process_dbos_row(payload, *args)
    from_view = NATIVE.process_dbos_row(bytearray(payload), *args)
    from_memview = NATIVE.process_dbos_row(memoryview(bytearray(payload)), *args)
    assert from_bytes == from_view == from_memview == [-5]


def _meta(desc: dict[str, Any]) -> tuple[list[int], ...]:
    """Metadata lists in the native argument order (after the buffer)."""
    return (
        desc["field_type"],
        desc["field_size"],
        desc["field_trueSize"],
        desc["field_offset"],
        desc["field_fixedSize"],
        desc["field_physField"],
    )
