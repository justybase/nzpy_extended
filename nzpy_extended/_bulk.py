"""Client-side preparation for external-table bulk loads.

Pure Python, no I/O. ``Connection.load_data`` uses this module to

* validate identifiers and options before any statement is sent,
* resolve the target schema (from the catalog for an existing table, from
  the caller's column list, or inferred from every row),
* render rows into the delimited text that Netezza reads through
  ``INSERT ... SELECT * FROM EXTERNAL``, with strict per-cell validation so
  that a bad value is reported with its row and column number instead of a
  generic server ``maxerrors`` failure.

Wire format: one row per line, fields joined by the delimiter, ``\\N`` for
NULL (``NULLVALUE '\\N'``), and the escape character in front of the
delimiter, the escape character itself, LF and CR (``ESCAPECHAR``).
"""

from __future__ import annotations

import datetime
import math
import re
from collections.abc import AsyncIterable, AsyncIterator, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .exceptions import DataError, ProgrammingError

NULL_MARKER = "\\N"
BATCH_ROWS = 4096
DEFAULT_TEXT_LENGTH = 255
MAX_VARCHAR_BYTES = 64000
MAX_NVARCHAR_CHARS = 16000

_IDENT_PART = r'(?:[A-Za-z_][A-Za-z0-9_]*|"(?:[^"]|"")+")'
_TABLE_RE = re.compile(rf"{_IDENT_PART}(?:\.{_IDENT_PART}){{0,2}}")
_COLUMN_RE = re.compile(_IDENT_PART)
_BARE_UPPER_RE = re.compile(r"[A-Z_][A-Z0-9_]*")
_TYPE_RE = re.compile(r"([A-Z][A-Z ]*?)\s*(?:\(\s*(\d+)\s*(?:,\s*(\d+)\s*)?\))?")
_INT_TEXT_RE = re.compile(r"[+-]?\d+")
_DECIMAL_TEXT_RE = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_FLOAT_TEXT_RE = _DECIMAL_TEXT_RE
_DATE_TEXT_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_TIME_TEXT_RE = re.compile(r"\d{2}:\d{2}:\d{2}")
_TIMESTAMP_TEXT_RE = re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}")
_BOOL_TRUE = frozenset({"true", "t", "yes", "y", "on", "1"})
_BOOL_FALSE = frozenset({"false", "f", "no", "n", "off", "0"})
_ENCODING_CODECS = {"UTF8": "utf-8", "INTERNAL": "utf-8", "LATIN9": "iso8859-15"}

_INT_TYPES: dict[str, tuple[int, int, str]] = {
    "BYTEINT": (-128, 127, "BYTEINT"),
    "SMALLINT": (-32768, 32767, "SMALLINT"),
    "INT": (-2147483648, 2147483647, "INTEGER"),
    "INTEGER": (-2147483648, 2147483647, "INTEGER"),
    "BIGINT": (-(2**63), 2**63 - 1, "BIGINT"),
}
_FLOAT_TYPES = {"REAL": "REAL", "FLOAT": "DOUBLE PRECISION", "DOUBLE": "DOUBLE PRECISION",
                "DOUBLE PRECISION": "DOUBLE PRECISION"}
_VARCHAR_NAMES = {"VARCHAR": "VARCHAR", "CHARACTER VARYING": "VARCHAR",
                  "CHAR": "CHAR", "CHARACTER": "CHAR"}
_NVARCHAR_NAMES = {"NVARCHAR": "NVARCHAR", "NATIONAL CHARACTER VARYING": "NVARCHAR",
                   "NCHAR": "NCHAR", "NATIONAL CHARACTER": "NCHAR", "NATIONAL CHAR": "NCHAR"}
_SIMPLE_TYPES = {"BOOLEAN": "bool", "BOOL": "bool", "DATE": "date", "TIME": "time",
                 "TIMESTAMP": "timestamp"}


class _CellError(Exception):
    """A single value cannot be represented in its column."""


@dataclass(frozen=True)
class ColumnType:
    """A supported Netezza column type, normalised for load_data."""

    kind: str  # int | float | numeric | bool | date | time | timestamp | text
    sql: str  # canonical type text used in CREATE TABLE / EXTERNAL column list
    low: int = 0  # int range
    high: int = 0
    precision: int = 0  # numeric
    scale: int = 0
    length: int | None = None  # text: bytes (VARCHAR) or characters (NVARCHAR)
    national: bool = False  # text stored as NVARCHAR/NCHAR (character length)


@dataclass(frozen=True)
class Column:
    """A target column. ``lookup_names`` are the dict-row keys that match it."""

    sql_name: str
    lookup_names: tuple[str, ...]
    ctype: ColumnType


@dataclass(frozen=True)
class LoadOptions:
    delimiter: str
    escape_char: str | None
    encoding: str | None  # explicit ENCODING, or None for automatic choice
    logdir: str


# ----- identifiers and options ------------------------------------------------

def check_table_name(name: str) -> str:
    """Accept ``TABLE``, ``SCHEMA.TABLE`` or ``DB.SCHEMA.TABLE`` (bare or quoted parts)."""
    if not isinstance(name, str) or not _TABLE_RE.fullmatch(name):
        raise ProgrammingError(
            f"invalid table name {name!r}; use NAME, SCHEMA.NAME or \"quoted\" identifiers")
    return name


def _user_identifier(name: str) -> tuple[str, tuple[str, ...]]:
    """Return ``(sql_text, lookup_names)`` for a column name written by the caller."""
    if not isinstance(name, str) or not _COLUMN_RE.fullmatch(name):
        raise ProgrammingError(
            f"invalid column name {name!r}; use a bare identifier or \"quoted\" name")
    if name.startswith('"'):
        return name, (name[1:-1].replace('""', '"'),)
    return name, (name, name.upper())


def _sql_identifier_from_catalog(name: str) -> str:
    if _BARE_UPPER_RE.fullmatch(name):
        return name
    return '"' + name.replace('"', '""') + '"'


def _sql_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def build_options(delimiter: str, escape_char: str | None, encoding: str | None,
                  logdir: str) -> LoadOptions:
    if not isinstance(delimiter, str) or len(delimiter) != 1 or delimiter in "\r\n":
        raise ProgrammingError("delimiter must be one character other than CR or LF")
    if escape_char is not None:
        if not isinstance(escape_char, str) or len(escape_char) != 1 or escape_char in "\r\n":
            raise ProgrammingError("escape_char must be None or one character other than CR or LF")
        if escape_char == delimiter:
            raise ProgrammingError("escape_char and delimiter must differ")
    if encoding is not None:
        encoding = encoding.upper()
        if encoding not in _ENCODING_CODECS:
            raise ProgrammingError(
                f"unsupported encoding {encoding!r}; use UTF8, LATIN9 or INTERNAL, or None for automatic")
    return LoadOptions(delimiter=delimiter, escape_char=escape_char, encoding=encoding, logdir=logdir)


def requested_columns(columns: Sequence[Any] | None) -> list[tuple[str, str | None]] | None:
    """Normalise ``columns``: names, ``(name, type)`` pairs, or a mix of both."""
    if columns is None:
        return None
    out: list[tuple[str, str | None]] = []
    for item in columns:
        if isinstance(item, str):
            out.append((item, None))
        elif isinstance(item, (tuple, list)) and len(item) == 2:
            name_part: object = item[0]
            type_part: object = item[1]
            if not isinstance(name_part, str) or not isinstance(type_part, str):
                raise ProgrammingError("columns must be names or (name, type) pairs")
            out.append((name_part, type_part))
        else:
            raise ProgrammingError("columns must be names or (name, type) pairs")
    if not out:
        raise ProgrammingError("columns must not be empty")
    seen: set[str] = set()
    for name, _ in out:
        # Bare names fold to upper case, so 'id' and 'ID' name the same column.
        key = _user_identifier(name)[1][-1]
        if key in seen:
            raise ProgrammingError(f"duplicate column name {name!r}")
        seen.add(key)
    return out


# ----- types -----------------------------------------------------------------

def parse_type(nz_type: str) -> ColumnType:
    """Map a Netezza type name (DDL or catalog ``format_type`` form) to a ColumnType."""
    text = " ".join(nz_type.upper().split())
    match = _TYPE_RE.fullmatch(text)
    if match is None:
        raise ProgrammingError(f"unsupported column type {nz_type!r} for load_data")
    base, p1, p2 = match.group(1), match.group(2), match.group(3)
    if base in _INT_TYPES:
        low, high, sql = _INT_TYPES[base]
        return ColumnType("int", sql, low=low, high=high)
    if base in _FLOAT_TYPES:
        return ColumnType("float", _FLOAT_TYPES[base])
    if base in ("NUMERIC", "DECIMAL"):
        precision = int(p1) if p1 else 38
        scale = int(p2) if p2 else 0
        return ColumnType("numeric", f"NUMERIC({precision},{scale})", precision=precision, scale=scale)
    if base in _VARCHAR_NAMES:
        name = _VARCHAR_NAMES[base]
        length = int(p1) if p1 else None
        suffix = f"({length})" if length is not None else ""
        return ColumnType("text", f"{name}{suffix}", length=length)
    if base in _NVARCHAR_NAMES:
        name = _NVARCHAR_NAMES[base]
        length = int(p1) if p1 else None
        suffix = f"({length})" if length is not None else ""
        return ColumnType("text", f"{name}{suffix}", length=length, national=True)
    if base in _SIMPLE_TYPES and p1 is None:
        kind = _SIMPLE_TYPES[base]
        return ColumnType(kind, "BOOLEAN" if kind == "bool" else kind.upper())
    raise ProgrammingError(f"unsupported column type {nz_type!r} for load_data")


def _int_type(low: int, high: int) -> ColumnType:
    if -32768 <= low and high <= 32767:
        return ColumnType("int", "SMALLINT", low=-32768, high=32767)
    if -2147483648 <= low and high <= 2147483647:
        return ColumnType("int", "INTEGER", low=-2147483648, high=2147483647)
    if -(2**63) <= low and high <= 2**63 - 1:
        return ColumnType("int", "BIGINT", low=-(2**63), high=2**63 - 1)
    raise DataError(f"integer values {low}..{high} exceed BIGINT; use an explicit NUMERIC column")


def _numeric_type(precision: int, scale: int) -> ColumnType:
    if precision > 38:
        raise DataError(f"inferred NUMERIC({precision},{scale}) exceeds 38 digits; declare the column type")
    return ColumnType("numeric", f"NUMERIC({precision},{scale})", precision=precision, scale=scale)


def _text_type(max_bytes: int, max_chars: int, unicode_text: bool) -> ColumnType:
    if unicode_text:
        if max_chars > MAX_NVARCHAR_CHARS:
            raise DataError(f"text of {max_chars} characters exceeds NVARCHAR({MAX_NVARCHAR_CHARS})")
        length = max(max_chars, 1)
        return ColumnType("text", f"NVARCHAR({length})", length=length, national=True)
    if max_bytes > MAX_VARCHAR_BYTES:
        raise DataError(f"text of {max_bytes} bytes exceeds VARCHAR({MAX_VARCHAR_BYTES})")
    length = max(max_bytes, 1)
    return ColumnType("text", f"VARCHAR({length})", length=length)


def _decimal_shape(value: Decimal) -> tuple[int, int]:
    """Return ``(integer_digits, scale)`` of a normalised, finite Decimal."""
    _sign, digits, exponent = value.as_tuple()
    if not isinstance(exponent, int):
        raise DataError(f"non-finite decimal {value} cannot be stored")
    count = len(digits)
    if exponent >= 0:
        return count + exponent, 0
    scale = -exponent
    return max(count - scale, 0), scale


def _classify(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, Decimal):
        return "decimal"
    if isinstance(value, datetime.datetime):
        return "datetime"
    if isinstance(value, datetime.date):
        return "date"
    if isinstance(value, datetime.time):
        return "time"
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "bytes"
    if isinstance(value, str):
        return "str"
    return "other"


def _infer_string_column(values: list[str]) -> ColumnType:
    nonempty = [v.strip() for v in values if v.strip()]
    if not nonempty:
        return _text_type(DEFAULT_TEXT_LENGTH, DEFAULT_TEXT_LENGTH, False)
    lowers = [v.lower() for v in nonempty]
    keywords = {"true", "false", "t", "f", "yes", "no"}
    if any(v in keywords for v in lowers) and all(v in keywords | {"0", "1"} for v in lowers):
        return ColumnType("bool", "BOOLEAN")
    if all(_INT_TEXT_RE.fullmatch(v) for v in nonempty) and not any(
            len(v.lstrip("+-")) > 1 and v.lstrip("+-")[0] == "0" for v in nonempty):
        ints = [int(v) for v in nonempty]
        return _int_type(min(ints), max(ints))
    if all(_DATE_TEXT_RE.fullmatch(v) for v in nonempty):
        try:
            for v in nonempty:
                datetime.date.fromisoformat(v)
            return ColumnType("date", "DATE")
        except ValueError:
            pass
    if all(_TIME_TEXT_RE.fullmatch(v) for v in nonempty):
        return ColumnType("time", "TIME")
    if all(_TIMESTAMP_TEXT_RE.fullmatch(v) for v in nonempty):
        try:
            for v in nonempty:
                datetime.datetime.fromisoformat(v)
            return ColumnType("timestamp", "TIMESTAMP")
        except ValueError:
            pass
    if any("." in v for v in nonempty) and all(_FLOAT_TEXT_RE.fullmatch(v) for v in nonempty):
        int_digits = 0
        scale = 0
        for v in nonempty:
            raw = v.lstrip("+-")
            whole, _, frac = raw.partition(".")
            int_digits = max(int_digits, len(whole.lstrip("0")))
            scale = max(scale, len(frac))
        precision = max(int_digits + scale, 1)
        return _numeric_type(precision, min(scale, precision))
    max_chars = max(len(v) for v in values)
    max_bytes = max(len(v.encode("utf-8")) for v in values)
    return _text_type(max_bytes, max_chars, not all(v.isascii() for v in values))


def _infer_column_type(values: list[Any], name: str) -> ColumnType:
    non_null = [v for v in values if v is not None]
    if not non_null:
        return _text_type(DEFAULT_TEXT_LENGTH, DEFAULT_TEXT_LENGTH, False)
    kinds = {_classify(v) for v in non_null}
    if "bytes" in kinds:
        raise DataError(f"column {name!r} holds bytes values, which load_data cannot store; decode them to text")
    if kinds == {"bool"}:
        return ColumnType("bool", "BOOLEAN")
    if kinds == {"int"}:
        ints = [int(v) for v in non_null]
        return _int_type(min(ints), max(ints))
    if kinds <= {"int", "float"} and "float" in kinds:
        return ColumnType("float", "DOUBLE PRECISION")
    if kinds <= {"int", "decimal"}:
        int_digits = 0
        scale = 0
        for v in non_null:
            if isinstance(v, Decimal):
                normalised = v.normalize()
                if not normalised.is_finite():
                    raise DataError(f"column {name!r} holds a non-finite Decimal, which cannot be stored")
                digits, places = _decimal_shape(normalised)
            else:
                digits, places = len(str(abs(int(v)))), 0
            int_digits = max(int_digits, digits)
            scale = max(scale, places)
        precision = max(int_digits + scale, 1)
        return _numeric_type(precision, scale)
    if kinds == {"date"}:
        return ColumnType("date", "DATE")
    if kinds == {"time"}:
        return ColumnType("time", "TIME")
    if kinds <= {"datetime", "date"} and "datetime" in kinds:
        return ColumnType("timestamp", "TIMESTAMP")
    if kinds == {"str"}:
        return _infer_string_column([v for v in non_null])
    texts = [v if isinstance(v, str) else _text_of(v) for v in non_null]
    return _text_type(max(len(t.encode("utf-8")) for t in texts), max(len(t) for t in texts),
                      not all(t.isascii() for t in texts))


def _text_of(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime.datetime):
        return value.isoformat(sep=" ")
    return str(value)


# ----- schema resolution -----------------------------------------------------

def columns_from_catalog(table: str, catalog: list[tuple[str, str]],
                         requested: list[tuple[str, str | None]] | None) -> list[Column]:
    """Columns of an existing table, in table order or in the requested order.

    Types always come from the catalog; a requested type is only a name hint.
    """
    by_name = {name: (name, nz_type) for name, nz_type in catalog}
    folded = {name.upper() if _BARE_UPPER_RE.fullmatch(name) else name: name for name, _ in catalog}
    if requested is None:
        return [_catalog_column(name, nz_type) for name, nz_type in catalog]
    out: list[Column] = []
    for name, _ in requested:
        _sql, lookup = _user_identifier(name)
        actual = folded.get(lookup[-1])  # quoted: exact; bare: folded to upper case
        if actual is None:
            raise ProgrammingError(f"column {name} does not exist in table {table}")
        out.append(_catalog_column(by_name[actual][0], by_name[actual][1]))
    return out


def _catalog_column(name: str, nz_type: str) -> Column:
    return Column(sql_name=_sql_identifier_from_catalog(name),
                  lookup_names=tuple(dict.fromkeys((name, name.lower()))),
                  ctype=parse_type(nz_type))


def explicit_columns(requested: list[tuple[str, str | None]] | None) -> list[Column] | None:
    """Columns when every name has a type; ``None`` when inference is still needed."""
    if requested is None or any(nz_type is None for _, nz_type in requested):
        return None
    out: list[Column] = []
    for name, nz_type in requested:
        sql, lookup = _user_identifier(name)
        out.append(Column(sql, lookup, parse_type(nz_type or "")))
    return out


def infer_columns(rows: Sequence[Any], requested: list[tuple[str, str | None]] | None) -> list[Column]:
    """Infer every column that has no declared type, scanning all rows."""
    if not rows:
        raise ProgrammingError("No rows to load")
    first = rows[0]
    if isinstance(first, Mapping):
        names = [name for name, _ in requested] if requested else [str(k) for k in first.keys()]
        identifiers = [_user_identifier(name) for name in names]
        columns_values: list[list[Any]] = [[] for _ in names]
        for number, row in enumerate(rows, 1):
            if not isinstance(row, Mapping) or len(row) != len(names):
                raise DataError(f"load_data: row {number}: expected the keys {names}")
            for idx, (_sql, lookup) in enumerate(identifiers):
                value = _lookup_mapping(row, lookup, names[idx], number)
                columns_values[idx].append(value)
    else:
        width = len(first) if isinstance(first, (tuple, list)) else -1
        if width < 0:
            raise ProgrammingError("rows must be tuples, lists or dicts")
        if requested is not None and len(requested) != width:
            raise ProgrammingError(f"columns lists {len(requested)} names but rows have {width} values")
        names = [name for name, _ in requested] if requested else [f"col{i + 1}" for i in range(width)]
        identifiers = [_user_identifier(name) if requested else (name, (name, name.upper()))
                       for name in names]
        columns_values = [[] for _ in range(width)]
        for number, row in enumerate(rows, 1):
            if not isinstance(row, (tuple, list)):
                row = tuple(row)
            if len(row) != width:
                raise DataError(f"load_data: row {number}: expected {width} values, got {len(row)}")
            for idx in range(width):
                columns_values[idx].append(row[idx])

    out: list[Column] = []
    for idx, name in enumerate(names):
        sql, lookup = identifiers[idx]
        declared = None
        if requested is not None:
            declared = requested[idx][1]
        ctype = parse_type(declared) if declared else _infer_column_type(columns_values[idx], name)
        out.append(Column(sql, lookup, ctype))
    return out


def _lookup_mapping(row: Mapping[Any, Any], lookup: tuple[str, ...], name: str, number: int) -> Any:
    for key in lookup:
        if key in row:
            return row[key]
    raise DataError(f"load_data: row {number}: missing key for column {name!r}")


# ----- encoding and SQL ------------------------------------------------------

def resolve_encoding(options: LoadOptions, columns: Sequence[Column]) -> str:
    """Pick the external-table ENCODING that matches the column types.

    Netezza rejects UTF8 with VARCHAR columns and requires INTERNAL when both
    VARCHAR and NVARCHAR are present, so the automatic choice follows the
    text columns: NVARCHAR-only -> UTF8, mixed -> INTERNAL, otherwise LATIN9.
    """
    if options.encoding is not None:
        if options.encoding == "UTF8" and any(
                c.ctype.kind == "text" and not c.ctype.national for c in columns):
            raise ProgrammingError(
                "encoding UTF8 cannot be used with VARCHAR columns; use NVARCHAR columns or encoding=None")
        return options.encoding
    texts = [c.ctype.national for c in columns if c.ctype.kind == "text"]
    if any(texts) and not all(texts):
        return "INTERNAL"
    if texts and all(texts):
        return "UTF8"
    return "LATIN9"


def create_table_sql(table: str, columns: Sequence[Column], temporary: bool,
                     distribute_on_random: bool) -> str:
    defs = ", ".join(f"{c.sql_name} {c.ctype.sql}" for c in columns)
    head = "CREATE TEMP TABLE" if temporary else "CREATE TABLE"
    tail = " DISTRIBUTE ON RANDOM" if distribute_on_random else ""
    return f"{head} {table} ({defs}){tail}"


def insert_sql(table: str, columns: Sequence[Column], marker: str, options: LoadOptions,
               encoding: str) -> str:
    names = ", ".join(c.sql_name for c in columns)
    defs = ", ".join(f"{c.sql_name} {c.ctype.sql}" for c in columns)
    using = [f"ENCODING {_sql_str(encoding)}", "REMOTESOURCE 'python'",
             f"DELIMITER {_sql_str(options.delimiter)}"]
    if options.escape_char is not None:
        using.append(f"ESCAPECHAR {_sql_str(options.escape_char)}")
    using.append(f"NULLVALUE {_sql_str(NULL_MARKER)}")
    if any(c.ctype.kind == "bool" for c in columns):
        using.append("BOOLSTYLE '1_0'")
    using.append(f"LOGDIR {_sql_str(options.logdir)}")
    return (f"INSERT INTO {table} ({names}) SELECT * FROM EXTERNAL {_sql_str(marker)} "
            f"({defs}) USING ({' '.join(using)})")


# ----- value conversion ------------------------------------------------------

def _conv_int(ctype: ColumnType) -> Callable[[Any], str | None]:
    low, high = ctype.low, ctype.high

    def convert(value: Any) -> str | None:
        if isinstance(value, bool):
            raise _CellError("a boolean cannot be stored in an integer column")
        if isinstance(value, int):
            number = value
        elif isinstance(value, str):
            text = value.strip()
            if text == "":
                return None
            if not _INT_TEXT_RE.fullmatch(text):
                raise _CellError(f"{value!r} is not an integer")
            number = int(text)
        elif isinstance(value, (float, Decimal)):
            if not math.isfinite(float(value)) or value != int(value):
                raise _CellError(f"{value!r} is not a whole number")
            number = int(value)
        else:
            raise _CellError(f"unsupported {type(value).__name__} value for an integer column")
        if not low <= number <= high:
            raise _CellError(f"{number} is out of range for {ctype.sql}")
        return str(number)

    return convert


def _conv_float(value: Any) -> str | None:
    if isinstance(value, bool):
        raise _CellError("a boolean cannot be stored in a floating-point column")
    if isinstance(value, str):
        text = value.strip()
        if text == "":
            return None
        lowered = text.lower()
        if lowered in ("inf", "+inf", "infinity", "+infinity"):
            return "Infinity"
        if lowered in ("-inf", "-infinity"):
            return "-Infinity"
        if not _FLOAT_TEXT_RE.fullmatch(text):
            raise _CellError(f"{value!r} is not a number")
        number = float(text)
    elif isinstance(value, (int, float, Decimal)):
        try:
            number = float(value)
        except OverflowError as exc:
            raise _CellError(f"{value} is too large for a floating-point column") from exc
    else:
        raise _CellError(f"unsupported {type(value).__name__} value for a floating-point column")
    if math.isnan(number):
        raise _CellError("NaN cannot be stored; pass None for NULL")
    if math.isinf(number):
        return "Infinity" if number > 0 else "-Infinity"
    return repr(number)


def _conv_numeric(ctype: ColumnType) -> Callable[[Any], str | None]:
    precision, scale = ctype.precision, ctype.scale

    def convert(value: Any) -> str | None:
        if isinstance(value, bool):
            raise _CellError("a boolean cannot be stored in a numeric column")
        if isinstance(value, str):
            text = value.strip()
            if text == "":
                return None
            if not _DECIMAL_TEXT_RE.fullmatch(text):
                raise _CellError(f"{value!r} is not a decimal number")
            number = Decimal(text)
        elif isinstance(value, int):
            number = Decimal(value)
        elif isinstance(value, float):
            if not math.isfinite(value):
                raise _CellError("NaN and infinity cannot be stored in a numeric column")
            number = Decimal(repr(value))
        elif isinstance(value, Decimal):
            number = value
        else:
            raise _CellError(f"unsupported {type(value).__name__} value for a numeric column")
        if not number.is_finite():
            raise _CellError("NaN and infinity cannot be stored in a numeric column")
        number = number.normalize()
        digits, places = _decimal_shape(number)
        if places > scale:
            raise _CellError(f"{value!r} has more than {scale} decimal places for {ctype.sql}")
        if digits > precision - scale:
            raise _CellError(f"{value!r} has more than {precision - scale} integer digits for {ctype.sql}")
        return format(number, "f")

    return convert


def _conv_bool(value: Any) -> str | None:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int) and value in (0, 1):
        return str(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text == "":
            return None
        if text in _BOOL_TRUE:
            return "1"
        if text in _BOOL_FALSE:
            return "0"
    raise _CellError(f"{value!r} is not a boolean (use true/false, yes/no, 1/0)")


def _conv_date(value: Any) -> str | None:
    if isinstance(value, datetime.datetime):
        if value.tzinfo is not None:
            raise _CellError("timezone-aware datetime cannot be stored; convert it to naive first")
        if value.time() != datetime.time():
            raise _CellError(f"{value!r} has a time part for a DATE column")
        return value.date().isoformat()
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, str):
        text = value.strip()
        if text == "":
            return None
        try:
            return datetime.date.fromisoformat(text).isoformat()
        except ValueError as exc:
            raise _CellError(f"{value!r} is not a date (YYYY-MM-DD)") from exc
    raise _CellError(f"unsupported {type(value).__name__} value for a DATE column")


def _conv_time(value: Any) -> str | None:
    if isinstance(value, datetime.time):
        if value.tzinfo is not None:
            raise _CellError("timezone-aware time cannot be stored; convert it to naive first")
        return value.isoformat()
    if isinstance(value, str):
        text = value.strip()
        if text == "":
            return None
        try:
            parsed = datetime.time.fromisoformat(text)
        except ValueError as exc:
            raise _CellError(f"{value!r} is not a time (HH:MM:SS[.ffffff])") from exc
        if parsed.tzinfo is not None:
            raise _CellError("timezone-aware time cannot be stored")
        return parsed.isoformat()
    raise _CellError(f"unsupported {type(value).__name__} value for a TIME column")


def _conv_timestamp(value: Any) -> str | None:
    if isinstance(value, datetime.datetime):
        if value.tzinfo is not None:
            raise _CellError("timezone-aware datetime cannot be stored; convert it to naive first")
        return value.isoformat(sep=" ")
    if isinstance(value, datetime.date):
        return f"{value.isoformat()} 00:00:00"
    if isinstance(value, str):
        text = value.strip()
        if text == "":
            return None
        try:
            parsed = datetime.datetime.fromisoformat(text)
        except ValueError as exc:
            raise _CellError(f"{value!r} is not a timestamp (YYYY-MM-DD HH:MM:SS[.ffffff])") from exc
        if parsed.tzinfo is not None:
            raise _CellError("timezone-aware timestamp cannot be stored; convert it to naive first")
        return parsed.isoformat(sep=" ")
    raise _CellError(f"unsupported {type(value).__name__} value for a TIMESTAMP column")


def _conv_text(ctype: ColumnType, options: LoadOptions, codec: str) -> Callable[[Any], str | None]:
    limit = ctype.length
    national = ctype.national
    escape = options.escape_char

    def convert(value: Any) -> str | None:
        if isinstance(value, str):
            text = value
        elif isinstance(value, (bytes, bytearray, memoryview)):
            raise _CellError("binary values cannot be loaded; decode them to text first")
        elif isinstance(value, bool):
            text = "true" if value else "false"
        elif isinstance(value, datetime.datetime):
            if value.tzinfo is not None:
                raise _CellError("timezone-aware datetime cannot be stored; convert it to naive first")
            text = value.isoformat(sep=" ")
        elif isinstance(value, datetime.date):
            text = value.isoformat()
        elif isinstance(value, datetime.time):
            text = value.isoformat()
        elif isinstance(value, float):
            if not math.isfinite(value):
                raise _CellError("NaN and infinity cannot be stored in a text column; pass None for NULL")
            text = repr(value)
        elif isinstance(value, Decimal):
            if not value.is_finite():
                raise _CellError("NaN and infinity cannot be stored in a text column; pass None for NULL")
            text = format(value, "f")
        else:
            text = str(value)
        if text == NULL_MARKER and escape != "\\":
            raise _CellError(r"the text \N is the NULL marker; use escape_char='\\' to store it")
        if text.isascii():
            size = len(text)
        else:
            try:
                encoded = text.encode(codec)
            except UnicodeEncodeError as exc:
                raise _CellError(
                    f"character {text[exc.start]!r} cannot be stored with encoding "
                    f"{_encoding_name(codec)}; use NVARCHAR columns or encoding=None") from None
            size = len(text) if national else len(encoded)
        if limit is not None and size > limit:
            unit = "characters" if national else "bytes"
            raise _CellError(f"value is {size} {unit} long; the column allows {limit}")
        return text

    return convert


def _encoding_name(codec: str) -> str:
    for name, value in _ENCODING_CODECS.items():
        if value == codec:
            return name
    return codec


def _converter(ctype: ColumnType, options: LoadOptions, codec: str) -> Callable[[Any], str | None]:
    kind = ctype.kind
    if kind == "int":
        return _conv_int(ctype)
    if kind == "float":
        return _conv_float
    if kind == "numeric":
        return _conv_numeric(ctype)
    if kind == "bool":
        return _conv_bool
    if kind == "date":
        return _conv_date
    if kind == "time":
        return _conv_time
    if kind == "timestamp":
        return _conv_timestamp
    return _conv_text(ctype, options, codec)


def _escaper(options: LoadOptions) -> Callable[[str], str]:
    delimiter = options.delimiter
    escape = options.escape_char
    if escape is None:
        special = re.compile("[" + re.escape(delimiter + "\r\n") + "]")

        def reject(text: str) -> str:
            if special.search(text):
                raise _CellError(
                    "value contains the delimiter or a line break; set escape_char to escape it")
            return text

        return reject
    table = str.maketrans({escape: escape + escape, delimiter: escape + delimiter,
                           "\n": escape + "\n", "\r": escape + "\r"})

    def escape_text(text: str) -> str:
        return text.translate(table)

    return escape_text


class RowRenderer:
    """Validate rows against the target columns and render them as delimited text."""

    def __init__(self, columns: Sequence[Column], options: LoadOptions) -> None:
        if not columns:
            raise ProgrammingError("load_data needs at least one column")
        self.columns = list(columns)
        self.options = options
        self.encoding = resolve_encoding(options, self.columns)
        self.codec = _ENCODING_CODECS[self.encoding]
        self._converters = [_converter(c.ctype, options, self.codec) for c in self.columns]
        self._escape = _escaper(options)
        self._width = len(self.columns)
        self._delimiter = options.delimiter

    def render(self, rows: Sequence[Any], first_number: int = 1) -> bytes:
        """Render a batch of rows; numbers in errors start at ``first_number``."""
        if not rows:
            return b""
        lines = [self._line(row, first_number + offset) for offset, row in enumerate(rows)]
        return ("\n".join(lines) + "\n").encode(self.codec)

    async def stream(self, rows: Iterable[Any] | AsyncIterable[Any]) -> AsyncIterator[bytes]:
        """Yield rendered chunks lazily; validation errors surface while streaming."""
        number = 1
        async for batch in _batches(rows):
            chunk = self.render(batch, number)
            number += len(batch)
            if chunk:
                yield chunk

    def _values(self, row: Any) -> Sequence[Any]:
        if isinstance(row, str):
            raise _CellError("a row must be a tuple, list or dict, not a string")
        if isinstance(row, Mapping):
            if len(row) != self._width:
                raise _CellError(f"expected keys for {self._width} columns, got {len(row)} keys")
            picked: list[Any] = []
            for column in self.columns:
                for key in column.lookup_names:
                    if key in row:
                        picked.append(row[key])
                        break
                else:
                    raise _CellError(f"no value for column {column.sql_name}")
            return picked
        values: Sequence[Any] = row
        if type(row) not in (tuple, list):
            try:
                values = tuple(row)
            except TypeError:
                raise _CellError(f"a row must be a tuple, list or dict, not {type(row).__name__}") from None
        if len(values) != self._width:
            raise _CellError(f"expected {self._width} values, got {len(values)}")
        return values

    def _line(self, row: Any, number: int) -> str:
        try:
            values = self._values(row)
        except _CellError as exc:
            raise DataError(f"load_data: row {number}: {exc}") from None
        fields: list[str] = []
        for column, convert, value in zip(self.columns, self._converters, values):
            if value is None:
                fields.append(NULL_MARKER)
                continue
            try:
                text = convert(value)
                fields.append(NULL_MARKER if text is None else self._escape(text))
            except _CellError as exc:
                raise DataError(
                    f"load_data: row {number}, column {column.sql_name}: {exc}") from None
        return self._delimiter.join(fields)


async def _batches(rows: Iterable[Any] | AsyncIterable[Any]) -> AsyncIterator[list[Any]]:
    batch: list[Any] = []
    if isinstance(rows, AsyncIterable):
        async for row in rows:
            batch.append(row)
            if len(batch) >= BATCH_ROWS:
                yield batch
                batch = []
    else:
        for row in rows:
            batch.append(row)
            if len(batch) >= BATCH_ROWS:
                yield batch
                batch = []
    if batch:
        yield batch


__all__ = [
    "NULL_MARKER",
    "BATCH_ROWS",
    "Column",
    "ColumnType",
    "LoadOptions",
    "RowRenderer",
    "build_options",
    "check_table_name",
    "columns_from_catalog",
    "create_table_sql",
    "explicit_columns",
    "infer_columns",
    "insert_sql",
    "parse_type",
    "requested_columns",
    "resolve_encoding",
]
