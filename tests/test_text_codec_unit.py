"""Text decoding fast paths must not change any result.

The native decoder decodes ASCII and ISO-8859-1 without the codec machinery, and
the pure-Python decoder maps ``latin`` to ``latin-1``. Both are only valid because
of the codec properties checked here, so the tests pin those properties and then
compare the two decoders outcome by outcome.
"""

from __future__ import annotations

import codecs
import random
from types import SimpleNamespace
from typing import Any

import pytest

from nzpy_extended import _native
from nzpy_extended import protocol as P
from nzpy_extended._dbos import DbosParser, _text_codec
from tests._dbos_builder import Field, build_row, canon, desc_obj

pytestmark = pytest.mark.unit

needs_native = pytest.mark.skipif(
    not _native._HAVE_NATIVE, reason="native extension is not built"
)

# Codecs the native fast path classifies as ASCII-compatible (see rust/src/fields.rs).
ASCII_COMPATIBLE = ("ascii", "iso8859-1", "iso8859-2", "iso8859-15", "cp1250", "cp1252", "cp1258")
LATIN_ALIASES = ("latin", "latin-1", "latin1", "iso-8859-1", "LATIN")

ENCODINGS = ("utf-8", "latin", "latin-1", "iso8859-15", "cp1252", "ascii", "utf-16", "no-such-codec")
TEXTS = (
    b"abc",
    b"",
    b"\x00\x00",
    b"caf\xe9 \x80",  # ISO-8859-1 text, not valid UTF-8
    b"\xff\xfe\xfd",  # invalid UTF-8 and ASCII
    "zł €".encode("utf-8"),  # valid UTF-8, outside ISO-8859-1
    b"\xe9",
)
TYPES = (P.NzTypeVarChar, P.NzTypeNVarChar, P.NzTypeChar)


@pytest.mark.parametrize("name", ASCII_COMPATIBLE)
def test_fast_path_codecs_are_ascii_compatible(name: str) -> None:
    # The Rust classifier treats these as ASCII-compatible; check the premise.
    assert codecs.lookup(name).name == name
    ascii_text = bytes(range(128))
    assert ascii_text.decode(name) == ascii_text.decode("ascii")


@pytest.mark.parametrize("name", LATIN_ALIASES)
def test_latin_aliases_map_to_the_fast_name(name: str) -> None:
    assert _text_codec(name) == "latin-1"


def test_latin_alias_decodes_every_byte_like_the_original() -> None:
    data = bytes(range(256))
    assert str(data, _text_codec("latin")) == str(data, "latin")


@pytest.mark.parametrize("name", ("utf-8", "cp1252", "no-such-codec"))
def test_other_names_are_returned_unchanged(name: str) -> None:
    assert _text_codec(name) == name


def _field(ftype: int, data: bytes) -> Field:
    if ftype == P.NzTypeChar:
        width = 20
        return Field(ftype, width, width, False, True, data.ljust(width, b"\x00")[:width])
    return Field(ftype, 50, 50, False, False, data)


def _outcome(payload: bytes, desc: dict[str, Any], encoding: str, use_native: bool) -> list[Any]:
    """``["ok", row]`` or ``["exc", class name]`` for one row through the public path."""
    conn = SimpleNamespace(_char_varchar_encoding=encoding, _client_encoding=encoding)
    parser = DbosParser(conn)  # type: ignore[arg-type]
    cursor = SimpleNamespace(cached_rows=[])
    old_flag = _native._HAVE_NATIVE
    _native._HAVE_NATIVE = use_native
    try:
        parser._process_dbos_payload(cursor, desc_obj(desc), memoryview(payload))  # type: ignore[arg-type]
        return ["ok", [canon(v) for v in cursor.cached_rows[0]]]
    except Exception as exc:  # noqa: BLE001 - the exception class is the contract
        return ["exc", type(exc).__name__]
    finally:
        _native._HAVE_NATIVE = old_flag


def test_latin_text_keeps_every_byte_on_the_reference_path() -> None:
    payload, desc = build_row([_field(P.NzTypeVarChar, b"caf\xe9 \x80")], 0, random.Random(0))
    assert _outcome(payload, desc, "latin", use_native=False) == ["ok", [["str", "caf\xe9 \x80"]]]


@needs_native
@pytest.mark.parametrize("encoding", ENCODINGS)
@pytest.mark.parametrize("ftype", TYPES)
@pytest.mark.parametrize("text", TEXTS, ids=[t.hex() or "empty" for t in TEXTS])
def test_native_text_outcome_matches_the_reference(encoding: str, ftype: int, text: bytes) -> None:
    payload, desc = build_row([_field(ftype, text)], 0, random.Random(0))
    want = _outcome(payload, desc, encoding, use_native=False)
    got = _outcome(payload, desc, encoding, use_native=True)
    assert got == want
