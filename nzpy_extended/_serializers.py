"""Serialization and deserialization helpers for PostgreSQL/Netezza types.

All functions previously defined as closures inside ``Connection.connect()``
have been moved here so they can be unit-tested and reused without the
overhead of re-creating them for every connection.
"""

from __future__ import annotations

import enum
import json
from collections import defaultdict
from datetime import date, time, timedelta as Timedelta
from decimal import Decimal
from functools import partial
from ipaddress import (
    IPv4Address,
    IPv4Network,
    IPv6Address,
    IPv6Network,
    ip_address,
    ip_network,
)
from typing import TYPE_CHECKING, Any
from uuid import UUID

from .types import (
    FC_BINARY,
    FC_TEXT,
    Interval,
    PGEnum,
    PGJson,
    PGJsonb,
    PGText,
    PGTsvector,
    PGVarchar,
    bytea_recv,
    bytea_send,
    bool_send,
    float4_recv,
    float8_recv,
    int2_recv,
    int4_recv,
    int8_recv,
    int_in,
    interval_recv_integer,
    interval_send_integer,
    null_send,
    timestamp_in,
    timestamp_send_integer,
    timestamptz_in,
    timestamptz_send_integer,
    uuid_recv,
    uuid_send,
    varbinary_recv,
)
from .utils import (
    d_pack,
    h_pack,
    h_le_unpack,
    i_pack,
    i_unpack,
    ii_pack,
    ii_unpack,
    iii_unpack,
    q_pack,
    q_le_unpack,
)

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Text / encoding helpers
# ---------------------------------------------------------------------------

def text_out(value: str, *, encoding: str) -> bytes:
    return value.encode(encoding)


def enum_out(value: enum.Enum, *, encoding: str) -> bytes:
    return str(value.value).encode(encoding)


def time_out(value: time, *, encoding: str) -> bytes:
    return value.isoformat().encode(encoding)


def date_out(value: date, *, encoding: str) -> bytes:
    return value.isoformat().encode(encoding)


def _unknown_out(value: object, *, encoding: str) -> bytes:
    return str(value).encode(encoding)


# ---------------------------------------------------------------------------
# Scalar recv helpers (encoding-aware)
# ---------------------------------------------------------------------------

def text_recv(data: bytes, offset: int, length: int, *, encoding: str) -> str:
    view = memoryview(data)
    return str(view[offset: offset + length], encoding)


def bool_recv(data: bytes, offset: int, length: int) -> bool:
    return data[offset] == 116  # b't'


def json_in(data: bytes, offset: int, length: int, *, encoding: str) -> Any:
    return json.loads(str(data[offset: offset + length], encoding))


def time_in(data: bytes, offset: int, length: int, *, encoding: str) -> time:
    hour = int(data[offset : offset + 2])
    minute = int(data[offset + 3 : offset + 5])
    sec = Decimal(data[offset + 6 : offset + length].decode(encoding))
    return time(hour, minute, int(sec), int((sec - int(sec)) * 1_000_000))


def date_in(data: bytes, offset: int, length: int, *, encoding: str) -> date | str:
    d = data[offset : offset + length].decode(encoding)
    try:
        return date(int(d[:4]), int(d[5:7]), int(d[8:10]))
    except ValueError:
        return d


def numeric_in(data: bytes, offset: int, length: int, *, encoding: str) -> Decimal:
    return Decimal(data[offset : offset + length].decode(encoding))


def numeric_out(d: Decimal, *, encoding: str) -> bytes:
    return str(d).encode(encoding)


# ---------------------------------------------------------------------------
# Array / vector recv helpers
# ---------------------------------------------------------------------------

def array_in(data: bytes, idx: int, length: int, *, encoding: str) -> list[Any]:
    text = data[idx : idx + length].decode(encoding)

    def parse_array(s: str, pos: int) -> tuple[list[Any], int]:
        result: list[Any] = []
        while pos < len(s) and s[pos].isspace():
            pos += 1
        if pos >= len(s) or s[pos] != "{":
            raise ValueError("Expected '{'")
        pos += 1
        while pos < len(s):
            while pos < len(s) and s[pos].isspace():
                pos += 1
            if pos >= len(s):
                break
            if s[pos] == "}":
                pos += 1
                break
            elif s[pos] == "{":
                arr, pos = parse_array(s, pos)
                result.append(arr)
            else:
                start = pos
                while pos < len(s) and s[pos] not in ("}", ","):
                    pos += 1
                val_str = s[start:pos].strip()
                if val_str.upper() == "NULL":
                    result.append(None)
                elif val_str:
                    result.append(Decimal(val_str))
            while pos < len(s) and s[pos].isspace():
                pos += 1
            if pos < len(s) and s[pos] == ",":
                pos += 1
        return result, pos

    arr, _ = parse_array(text, 0)
    return arr


def vector_in(data: bytes, idx: int, length: int, *, encoding: str) -> list[int]:
    text = data[idx : idx + length].decode(encoding)
    return [int(x) for x in text.replace(",", " ").split()]


# ---------------------------------------------------------------------------
# INET helpers
# ---------------------------------------------------------------------------

def inet_out(value: object, *, encoding: str) -> bytes:
    return str(value).encode(encoding)


def inet_in(data: bytes, offset: int, length: int, *, encoding: str) -> Any:
    inet_str = data[offset : offset + length].decode(encoding)
    if "/" in inet_str:
        return ip_network(inet_str, False)
    return ip_address(inet_str)


# ---------------------------------------------------------------------------
# Registry builders (called once per Connection)
# ---------------------------------------------------------------------------

ARRAY_OIDS: tuple[int, ...] = (
    1000, 1003, 1005, 1007, 1009, 1014, 1015, 1016, 1021, 1022, 1263,
)


def build_pg_types(encoding: str) -> defaultdict[Any, Any]:
    """Return the OID -> (format_code, recv_func) mapping.

    Each recv_func has signature ``(data, offset, length) -> value``.
    Encoding-aware functions are partially applied with *encoding*.
    """
    _text_r = partial(text_recv, encoding=encoding)
    _date_r = partial(date_in, encoding=encoding)
    _time_r = partial(time_in, encoding=encoding)
    _json_r = partial(json_in, encoding=encoding)
    _num_r = partial(numeric_in, encoding=encoding)
    _vec_r = partial(vector_in, encoding=encoding)
    _arr_txt = partial(array_in, encoding=encoding)
    _inet_r = partial(inet_in, encoding=encoding)

    base: dict[int, tuple[int, Any]] = {
        16: (FC_BINARY, bool_recv),
        17: (FC_BINARY, bytea_recv),
        19: (FC_BINARY, _text_r),
        20: (FC_BINARY, int8_recv),
        21: (FC_BINARY, int2_recv),
        22: (FC_TEXT, _vec_r),
        23: (FC_BINARY, int4_recv),
        25: (FC_BINARY, _text_r),
        26: (FC_TEXT, int_in),
        28: (FC_TEXT, int_in),
        114: (FC_TEXT, _json_r),
        700: (FC_BINARY, float4_recv),
        701: (FC_BINARY, float8_recv),
        705: (FC_BINARY, _text_r),
        829: (FC_TEXT, _text_r),
        869: (FC_TEXT, _inet_r),
        1042: (FC_BINARY, _text_r),
        1043: (FC_BINARY, _text_r),
        1082: (FC_TEXT, _date_r),
        1083: (FC_TEXT, _time_r),
        1114: (FC_TEXT, timestamp_in),
        1184: (FC_TEXT, timestamptz_in),
        1186: (FC_TEXT, interval_recv_integer),
        1231: (FC_TEXT, _arr_txt),
        1700: (FC_TEXT, _num_r),
        2275: (FC_BINARY, _text_r),
        2500: (FC_TEXT, int_in),
        2568: (FC_TEXT, varbinary_recv),
        2569: (FC_TEXT, varbinary_recv),
        2950: (FC_BINARY, uuid_recv),
        3802: (FC_TEXT, _json_r),
    }

    # array_recv needs access to the pg_types dict itself (to resolve
    # element types at runtime).  We build a closure that captures
    # *base* so it can look up element conversion functions.
    def _array_recv(data: bytes, idx: int, length: int) -> list[Any]:
        final_idx = idx + length
        dim, _hasnull, typeoid = iii_unpack(data, idx)
        idx += 12
        entry = base.get(typeoid)
        if entry is None:
            entry = base.get(25, (FC_BINARY, _text_r))
        conversion = entry[1]
        dim_lengths: list[int] = []
        for _ in range(dim):
            dim_lengths.append(ii_unpack(data, idx)[0])
            idx += 8
        values: list[Any] = []
        while idx < final_idx:
            element_len = i_unpack(data, idx)[0]
            idx += 4
            if element_len == -1:
                values.append(None)
            else:
                values.append(conversion(data, idx, element_len))
                idx += element_len
        for length_ in reversed(dim_lengths[1:]):
            values = list(map(list, zip(*[iter(values)] * length_)))
        return values

    for oid in ARRAY_OIDS:
        base[oid] = (FC_BINARY, _array_recv)

    return defaultdict(lambda: (FC_TEXT, _text_r), base)


def build_py_types(encoding: str) -> dict[Any, tuple[int, int, Any]]:
    """Return the Python type -> (OID, format_code, send_func) mapping."""
    _text_w = partial(text_out, encoding=encoding)
    _enum_w = partial(enum_out, encoding=encoding)
    _time_w = partial(time_out, encoding=encoding)
    _date_w = partial(date_out, encoding=encoding)
    _num_w = partial(numeric_out, encoding=encoding)
    _inet_w = partial(inet_out, encoding=encoding)

    result: dict[Any, tuple[int, int, Any]] = {
        type(None): (-1, FC_BINARY, null_send),
        bool: (16, FC_BINARY, bool_send),
        bytearray: (17, FC_BINARY, bytea_send),
        20: (20, FC_BINARY, q_pack),
        21: (21, FC_BINARY, h_pack),
        23: (23, FC_BINARY, i_pack),
        PGText: (25, FC_TEXT, _text_w),
        float: (701, FC_BINARY, d_pack),
        PGEnum: (705, FC_TEXT, _enum_w),
        date: (1082, FC_TEXT, _date_w),
        time: (1083, FC_TEXT, _time_w),
        1114: (1114, FC_BINARY, timestamp_send_integer),
        PGVarchar: (1043, FC_TEXT, _text_w),
        1184: (1184, FC_BINARY, timestamptz_send_integer),
        PGJson: (114, FC_TEXT, _text_w),
        PGJsonb: (3802, FC_TEXT, _text_w),
        Timedelta: (1186, FC_BINARY, interval_send_integer),
        Interval: (1186, FC_BINARY, interval_send_integer),
        Decimal: (1700, FC_TEXT, _num_w),
        PGTsvector: (3614, FC_TEXT, _text_w),
        UUID: (2950, FC_BINARY, uuid_send),
    }

    result[bytes] = (17, FC_BINARY, bytea_send)
    result[str] = (705, FC_TEXT, _text_w)
    result[enum.Enum] = (705, FC_TEXT, _enum_w)

    # INET types
    result[IPv4Address] = (869, FC_TEXT, _inet_w)
    result[IPv6Address] = (869, FC_TEXT, _inet_w)
    result[IPv4Network] = (869, FC_TEXT, _inet_w)
    result[IPv6Network] = (869, FC_TEXT, _inet_w)

    return result


__all__ = [
    "text_out",
    "enum_out",
    "time_out",
    "date_out",
    "_unknown_out",
    "text_recv",
    "bool_recv",
    "json_in",
    "time_in",
    "date_in",
    "numeric_in",
    "numeric_out",
    "array_in",
    "vector_in",
    "inet_out",
    "inet_in",
    "build_pg_types",
    "build_py_types",
    "ARRAY_OIDS",
]
