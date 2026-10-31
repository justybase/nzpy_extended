"""Deterministic DBOS row builder for decoder parity tests.

``build_row`` encodes random field values into a RowStandard payload and the
matching tuple-descriptor fields. ``in_spec=True`` uses only the widths a live
Netezza 11.2 appliance was observed to send (NUMERIC storage of 4, 8 or 16
bytes; DATE 4 bytes; TIME and TIMESTAMP 8 bytes). ``in_spec=False`` also
produces out-of-spec widths; the Python reference and the Rust decoder
decode them identically.
"""

from __future__ import annotations

import datetime as dt
import random
import struct
from dataclasses import dataclass
from typing import Any

from nzpy_extended import protocol as P
from nzpy_extended.types import DbosTupleDesc

J2000 = 2451545
ORDINAL_TO_JD = 1721425  # date.toordinal() + this == Julian day number

FIXED_TYPES = (
    P.NzTypeInt,
    P.NzTypeInt8,
    P.NzTypeInt2,
    P.NzTypeInt1,
    P.NzTypeBool,
    P.NzTypeDouble,
    P.NzTypeFloat,
    P.NzTypeDate,
    P.NzTypeTime,
    P.NzTypeTimestamp,
    P.NzTypeInterval,
    P.NzTypeTimeTz,
    P.NzTypeNumeric,
    P.NzTypeChar,
)
VARYING_TYPES = (
    P.NzTypeNChar,
    P.NzTypeNVarChar,
    P.NzTypeVarChar,
    P.NzTypeVarFixedChar,
    P.NzTypeJson,
    P.NzTypeVector,
    P.NzTypeVarBinary,
    P.NzTypeUnknown,
)
UNSUPPORTED_TYPES = (P.NzTypeRecAddr, P.NzTypeMoney, 14)  # decode to None

ENCODINGS = (("utf-8", "utf-8"), ("utf-8", "utf-8"), ("latin-1", "utf-8"), ("utf-8", "latin-1"))


@dataclass
class Field:
    ftype: int
    size: int
    true_size: int
    null: bool
    fixed: bool
    data: bytes


def _text(rng: random.Random, enc: str, max_chars: int) -> bytes:
    alphabet = "abcXYZ 09-é" if enc != "ascii" else "abcXYZ 09-"
    text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, max_chars)))
    try:
        return text.encode(enc)
    except UnicodeEncodeError:
        return text.encode("utf-8")


def _fixed_field(rng: random.Random, ftype: int, in_spec: bool, null: bool) -> Field:
    if ftype == P.NzTypeDate:
        width = 4 if in_spec else rng.choice([4, 8])
        raw = rng.randint(-200_000, 200_000)
        data = raw.to_bytes(width, "little", signed=True)
        return Field(ftype, width, width, null, True, data)
    if ftype == P.NzTypeTime:
        width = 8 if in_spec else rng.choice([8, 8, 4])
        raw = rng.randint(0, 86_400 * 10**6 - 1)
        return Field(ftype, width, width, null, True, (raw & ((1 << (8 * width)) - 1)).to_bytes(width, "little"))
    if ftype == P.NzTypeTimestamp:
        width = 8 if in_spec else rng.choice([8, 8, 4, 12])
        raw = rng.randint(-(10**15), 10**15)
        return Field(ftype, width, width, null, True, (raw & ((1 << (8 * width)) - 1)).to_bytes(width, "little"))
    if ftype == P.NzTypeInterval:
        data = struct.pack("<q", rng.randint(-(10**12), 10**12)) + struct.pack("<i", rng.randint(-1000, 1000))
        return Field(ftype, 12, 12, null, True, data)
    if ftype == P.NzTypeTimeTz:
        data = struct.pack("<q", rng.randint(0, 86_400 * 10**6 - 1)) + struct.pack(
            "<i", rng.randint(-50_400, 50_400)
        )
        return Field(ftype, 12, 12, null, True, data)
    if ftype == P.NzTypeNumeric:
        prec = rng.randint(1, 38)
        scale = rng.randint(0, prec)
        if in_spec:
            chunk = 4 if prec <= 9 else 8 if prec <= 18 else 16
        else:
            chunk = rng.choice([4, 8, 12, 16])
        bits = chunk * 8
        limit = min(10**prec, 2 ** (bits - 1))
        value = rng.randint(-limit + 1, limit - 1)
        unsigned = value & ((1 << bits) - 1)
        count = chunk // 4
        words = [(unsigned >> (32 * (count - 1 - i))) & 0xFFFF_FFFF for i in range(count)]
        data = b"".join(struct.pack("<I", w) for w in words)
        return Field(ftype, (prec << 8) | scale, chunk, null, True, data)
    if ftype == P.NzTypeChar:
        width = rng.randint(1, 24)
        text = _text(rng, "utf-8", width)[:width]
        return Field(ftype, width, width, null, True, text + b"\x00" * (width - len(text)))
    if ftype == P.NzTypeBool:
        return Field(ftype, 1, 1, null, True, bytes([rng.choice([0, 1, rng.randrange(256)])]))
    if ftype == P.NzTypeDouble:
        return Field(ftype, 8, 8, null, True, struct.pack("<d", rng.uniform(-1e9, 1e9)))
    if ftype == P.NzTypeFloat:
        return Field(ftype, 4, 4, null, True, struct.pack("<f", rng.uniform(-1e6, 1e6)))
    if ftype == P.NzTypeInt1:
        return Field(ftype, 1, 1, null, True, struct.pack("<b", rng.randint(-128, 127)))
    if ftype == P.NzTypeInt2:
        return Field(ftype, 2, 2, null, True, struct.pack("<h", rng.randint(-32768, 32767)))
    if ftype == P.NzTypeInt:
        return Field(ftype, 4, 4, null, True, struct.pack("<i", rng.randint(-(2**31), 2**31 - 1)))
    return Field(ftype, 8, 8, null, True, struct.pack("<q", rng.randint(-(2**63), 2**63 - 1)))


def gen_field(rng: random.Random, enc: str, nulls: bool, in_spec: bool = True) -> Field:
    null = nulls and rng.random() < 0.2
    pick = rng.random()
    if pick < 0.6:
        return _fixed_field(rng, rng.choice(FIXED_TYPES), in_spec, null)
    if pick < 0.75:
        # NUMERIC is fixed-width: chunk drives the number of 32-bit words.
        return _fixed_field(rng, P.NzTypeNumeric, in_spec, null)
    if pick < 0.85:
        return _fixed_field(rng, P.NzTypeChar, in_spec, null)
    if pick < 0.97:
        ftype = rng.choice(VARYING_TYPES)
        size = rng.randint(1, 24)
        data = _text(rng, enc, 30)
        if rng.random() < 0.05:
            data = bytes(rng.randrange(256) for _ in range(rng.randint(0, 6)))
        return Field(ftype, size, size, null, False, data)
    width = rng.randint(0, 8)
    ftype = rng.choice(UNSUPPORTED_TYPES)
    return Field(ftype, width, width, null, True, bytes(rng.randrange(256) for _ in range(width)))


def build_row(fields: list[Field], nulls_allowed: int, rng: random.Random) -> tuple[bytes, dict[str, Any]]:
    """Encode ``fields`` into a payload and a descriptor dict (see ``desc_obj``)."""
    n = len(fields)
    bitmaplen = (n + 7) // 8
    phys = list(range(n))
    rng.shuffle(phys)
    bitmap = bytearray(bitmaplen)
    for i, f in enumerate(fields):
        if nulls_allowed and f.null:
            bitmap[phys[i] >> 3] |= 1 << (phys[i] & 7)

    offsets = [0] * n
    cur = 2 + bitmaplen
    fixed_bytes = bytearray()
    for i, f in enumerate(fields):
        if f.fixed:
            offsets[i] = cur
            fixed_bytes += f.data
            cur += len(f.data)
    fixed_size = cur

    var_bytes = bytearray()
    vidx = 0
    for i, f in enumerate(fields):
        if f.fixed:
            continue
        offsets[i] = vidx
        vidx += 1
        body = b"" if f.null else f.data
        total = 2 + len(body)
        var_bytes += struct.pack("<H", total) + body
        if total % 2:
            var_bytes += b"\x00"

    payload = b"\x00\x00" + bytes(bitmap) + bytes(fixed_bytes) + bytes(var_bytes)
    desc = {
        "field_type": [f.ftype for f in fields],
        "field_size": [f.size for f in fields],
        "field_trueSize": [f.true_size for f in fields],
        "field_offset": offsets,
        "field_fixedSize": [1 if f.fixed else 0 for f in fields],
        "field_physField": phys,
        "numFields": n,
        "nullsAllowed": nulls_allowed,
        "fixedFieldsSize": fixed_size,
        "numVaryingFields": vidx,
    }
    return payload, desc


def mutate(payload: bytes, desc: dict[str, Any], rng: random.Random) -> tuple[bytes, dict[str, Any]]:
    """Corrupt a valid row: flip a byte, truncate it, or perturb descriptor entries."""
    buf = bytearray(payload)
    roll = rng.random()
    if roll < 0.4 and buf:
        buf[rng.randrange(len(buf))] = rng.randrange(256)
    elif roll < 0.6 and buf:
        del buf[rng.randrange(len(buf)) :]
    else:
        key = rng.choice(["field_offset", "field_size", "field_trueSize", "field_fixedSize", "field_physField"])
        desc[key][rng.randrange(desc["numFields"])] = rng.randint(-3, 40)
        if rng.random() < 0.2:
            desc["fixedFieldsSize"] = rng.randint(-2, len(payload) + 4)
        if rng.random() < 0.1:
            desc["numVaryingFields"] = rng.randint(-1, 4)
    return bytes(buf), desc


def desc_obj(d: dict[str, Any]) -> DbosTupleDesc:
    obj = DbosTupleDesc()
    obj.field_type = list(d["field_type"])
    obj.field_size = list(d["field_size"])
    obj.field_trueSize = list(d["field_trueSize"])
    obj.field_offset = list(d["field_offset"])
    obj.field_fixedSize = list(d["field_fixedSize"])
    obj.field_physField = list(d["field_physField"])
    obj.numFields = d["numFields"]
    obj.nullsAllowed = d["nullsAllowed"]
    obj.fixedFieldsSize = d["fixedFieldsSize"]
    obj.numVaryingFields = d["numVaryingFields"]
    return obj


def canon(value: Any) -> list[Any]:
    """Comparable form of a decoded value: type name plus exact representation."""
    if value is None:
        return ["None"]
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, int):
        return ["int", value]
    if isinstance(value, float):
        return ["float", repr(value)]
    if isinstance(value, str):
        return ["str", value]
    if isinstance(value, (dt.date, dt.time)):
        return [type(value).__name__, value.isoformat()]
    return [type(value).__name__, repr(value)]


def random_row(rng: random.Random, enc: tuple[str, str], in_spec: bool = True) -> tuple[bytes, dict[str, Any], bool]:
    """A random row (valid or mutated), with the nulls setting and encodings drawn from ``rng``."""
    nulls_allowed = rng.choice([0, 1, 1])
    n = rng.randint(1, 9)
    fields = [gen_field(rng, enc[0], bool(nulls_allowed), in_spec) for _ in range(n)]
    payload, desc = build_row(fields, nulls_allowed, rng)
    mutated = rng.random() < 0.35
    if mutated:
        payload, desc = mutate(payload, desc, rng)
    return payload, desc, mutated
