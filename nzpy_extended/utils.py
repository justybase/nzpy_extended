import datetime
import enum
import math
from collections.abc import Callable, Generator
from decimal import Decimal
from json import dumps
from struct import Struct
from typing import Any
from uuid import UUID

from .exceptions import (DataError, InterfaceError, NotSupportedError, ProgrammingError,
                         ArrayDimensionsNotConsistentError)


def pack_funcs(fmt: str) -> tuple[Callable[..., bytes], Callable[..., tuple[Any, ...]]]:
    struc = Struct('!' + fmt)
    return struc.pack, struc.unpack_from


i_pack: Callable[..., bytes]
i_unpack: Callable[..., tuple[Any, ...]]
h_pack: Callable[..., bytes]
h_unpack: Callable[..., tuple[Any, ...]]
q_pack: Callable[..., bytes]
q_unpack: Callable[..., tuple[Any, ...]]
d_pack: Callable[..., bytes]
d_unpack: Callable[..., tuple[Any, ...]]
f_pack: Callable[..., bytes]
f_unpack: Callable[..., tuple[Any, ...]]
iii_pack: Callable[..., bytes]
iii_unpack: Callable[..., tuple[Any, ...]]
ii_pack: Callable[..., bytes]
ii_unpack: Callable[..., tuple[Any, ...]]
qii_pack: Callable[..., bytes]
qii_unpack: Callable[..., tuple[Any, ...]]
dii_pack: Callable[..., bytes]
dii_unpack: Callable[..., tuple[Any, ...]]
ihic_pack: Callable[..., bytes]
ihic_unpack: Callable[..., tuple[Any, ...]]
ci_pack: Callable[..., bytes]
ci_unpack: Callable[..., tuple[Any, ...]]
c_pack: Callable[..., bytes]
c_unpack: Callable[..., tuple[Any, ...]]
bh_pack: Callable[..., bytes]
bh_unpack: Callable[..., tuple[Any, ...]]
cccc_pack: Callable[..., bytes]
cccc_unpack: Callable[..., tuple[Any, ...]]

i_pack, i_unpack = pack_funcs('i')
h_pack, h_unpack = pack_funcs('h')
q_pack, q_unpack = pack_funcs('q')
d_pack, d_unpack = pack_funcs('d')
f_pack, f_unpack = pack_funcs('f')
iii_pack, iii_unpack = pack_funcs('iii')
ii_pack, ii_unpack = pack_funcs('ii')
qii_pack, qii_unpack = pack_funcs('qii')
dii_pack, dii_unpack = pack_funcs('dii')
ihic_pack, ihic_unpack = pack_funcs('ihic')
ci_pack, ci_unpack = pack_funcs('ci')
c_pack, c_unpack = pack_funcs('c')
bh_pack, bh_unpack = pack_funcs('bh')
cccc_pack, cccc_unpack = pack_funcs('cccc')
h_le_unpack: Callable[..., tuple[Any, ...]] = Struct('<H').unpack_from
i_le_unpack: Callable[..., tuple[Any, ...]] = Struct('<i').unpack_from
q_le_unpack: Callable[..., tuple[Any, ...]] = Struct('<q').unpack_from

min_int2: int = -2 ** 15
max_int2: int = 2 ** 15 - 1
min_int4: int = -2 ** 31
max_int4: int = 2 ** 31 - 1
min_int8: int = -2 ** 63
max_int8: int = 2 ** 63 - 1


def _quote_text_literal(value: str) -> str:
    if "\x00" in value:
        # The query frame is NUL-terminated: the server would cut the statement
        # there and read the rest as a new protocol message.
        raise DataError("a text parameter cannot contain a NUL character")
    return "'" + value.replace("'", "''") + "'"


def convert_paramstyle(style: str, query: str) -> tuple[str, Callable[[object], tuple[Any, ...]]]:
    OUTSIDE = 0
    INSIDE_SQ = 1
    INSIDE_QI = 2
    INSIDE_CO = 4
    INSIDE_BC = 8

    output_query: list[str] = []
    state = OUTSIDE
    prev_c: str | None = None
    i = 0
    positional_count = 0
    ordered_names: list[str] = []
    name_to_index: dict[str, int] = {}

    def remember_name(name: str) -> int:
        if name not in name_to_index:
            name_to_index[name] = len(ordered_names) + 1
            ordered_names.append(name)
        return name_to_index[name]

    while i < len(query):
        c = query[i]
        next_c = query[i + 1] if i + 1 < len(query) else None

        if state == OUTSIDE:
            if c == "'":
                output_query.append(c)
                state = INSIDE_SQ
                prev_c = c
                i += 1
                continue
            if c == '"':
                output_query.append(c)
                state = INSIDE_QI
                prev_c = c
                i += 1
                continue
            if c == '-' and next_c == '-':
                output_query.extend((c, next_c))
                state = INSIDE_CO
                prev_c = next_c
                i += 2
                continue
            if c == '/' and next_c == '*':
                output_query.extend((c, next_c))
                state = INSIDE_BC
                prev_c = next_c
                i += 2
                continue
            if style == "qmark" and c == "?":
                positional_count += 1
                output_query.append(f"${positional_count}")
                prev_c = c
                i += 1
                continue
            if style == "numeric" and c == ":" and next_c is not None and next_c.isdigit() and prev_c != ":":
                j = i + 1
                while j < len(query) and query[j].isdigit():
                    j += 1
                output_query.append("$" + query[i + 1:j])
                prev_c = query[j - 1]
                i = j
                continue
            if style == "named" and c == ":" and next_c is not None and (next_c.isalpha() or next_c == "_") and prev_c != ":":
                j = i + 1
                while j < len(query) and (query[j].isalnum() or query[j] == "_"):
                    j += 1
                name = query[i + 1:j]
                output_query.append(f"${remember_name(name)}")
                prev_c = query[j - 1]
                i = j
                continue
            if style == "pyformat" and c == "%" and next_c == "(":
                j = i + 2
                while j < len(query) and (query[j].isalnum() or query[j] == "_"):
                    j += 1
                name = query[i + 2:j]
                if not name or j + 1 >= len(query) or query[j] != ")" or query[j + 1] != "s":
                    raise InterfaceError("Only %(name)s and %% are supported in the query.")
                output_query.append(f"${remember_name(name)}")
                prev_c = "s"
                i = j + 2
                continue
            if style in ("format", "pyformat") and c == "%":
                if next_c == "%":
                    output_query.extend((c, next_c))
                    prev_c = next_c
                    i += 2
                    continue
                if next_c == "s":
                    positional_count += 1
                    output_query.append(f"${positional_count}")
                    prev_c = next_c
                    i += 2
                    continue
                raise InterfaceError("Only %s and %% are supported in the query.")

            output_query.append(c)

        elif state == INSIDE_SQ:
            output_query.append(c)
            if c == "'":
                if next_c == "'":
                    output_query.append(next_c)
                    prev_c = next_c
                    i += 2
                    continue
                state = OUTSIDE

        elif state == INSIDE_QI:
            output_query.append(c)
            if c == '"':
                state = OUTSIDE

        elif state == INSIDE_CO:
            output_query.append(c)
            if c == "\n":
                state = OUTSIDE

        elif state == INSIDE_BC:
            output_query.append(c)
            if c == "*" and next_c == "/":
                output_query.append(next_c)
                state = OUTSIDE
                prev_c = next_c
                i += 2
                continue

        prev_c = c
        i += 1

    def make_args(vals: object) -> tuple[Any, ...]:
        if vals is None:
            return ()
        if style in ("named", "pyformat") and ordered_names:
            if not isinstance(vals, dict):
                raise ProgrammingError("Named parameters require a mapping.")
            try:
                return tuple(vals[name] for name in ordered_names)
            except KeyError as exc:
                raise ProgrammingError(f"Missing value for parameter '{exc.args[0]}'") from exc
        if isinstance(vals, tuple):
            return vals
        if isinstance(vals, list):
            return tuple(vals)
        if isinstance(vals, dict):
            raise ProgrammingError("Positional parameters require a sequence, not a mapping.")
        try:
            return tuple(vals)  # type: ignore[arg-type]
        except TypeError:
            return (vals,)

    return ''.join(output_query), make_args


def _sql_literal(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, Decimal):
        if not value.is_finite():
            # NUMERIC has no NaN or Infinity on Netezza; the server rejects them.
            raise NotSupportedError(
                "Decimal NaN/Infinity cannot be sent as NUMERIC; "
                "pass a float if DOUBLE PRECISION semantics are intended")
        return str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        # Netezza accepts these only as typed casts. A bare nan/inf is an
        # unknown identifier.
        if math.isnan(value):
            return "CAST('NaN' AS DOUBLE PRECISION)"
        if math.isinf(value):
            return "CAST('Infinity' AS DOUBLE PRECISION)" if value > 0 \
                else "CAST('-Infinity' AS DOUBLE PRECISION)"
        return repr(value)
    if isinstance(value, datetime.datetime):
        return _quote_text_literal(value.isoformat(sep=" "))
    if isinstance(value, datetime.date):
        return _quote_text_literal(value.isoformat())
    if isinstance(value, datetime.time):
        return _quote_text_literal(value.isoformat())
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"x'{bytes(value).hex()}'"
    if isinstance(value, UUID):
        return _quote_text_literal(str(value))
    if isinstance(value, enum.Enum):
        return _quote_text_literal(str(value.value))
    if isinstance(value, dict):
        return _quote_text_literal(dumps(value))
    if isinstance(value, (list, tuple)):
        return "ARRAY[" + ", ".join(_sql_literal(item) for item in value) + "]"
    return _quote_text_literal(str(value))


def render_prepared_statement(statement: str, args: tuple[Any, ...]) -> tuple[str, int]:
    OUTSIDE = 0
    INSIDE_SQ = 1
    INSIDE_QI = 2
    INSIDE_CO = 4
    INSIDE_BC = 8

    output_query: list[str] = []
    state = OUTSIDE
    i = 0
    max_index = 0

    while i < len(statement):
        c = statement[i]
        next_c = statement[i + 1] if i + 1 < len(statement) else None

        if state == OUTSIDE:
            if c == "'":
                output_query.append(c)
                state = INSIDE_SQ
                i += 1
                continue
            if c == '"':
                output_query.append(c)
                state = INSIDE_QI
                i += 1
                continue
            if c == '-' and next_c == '-':
                output_query.extend((c, next_c))
                state = INSIDE_CO
                i += 2
                continue
            if c == '/' and next_c == '*':
                output_query.extend((c, next_c))
                state = INSIDE_BC
                i += 2
                continue
            if c == "$" and next_c is not None and next_c.isdigit():
                j = i + 1
                while j < len(statement) and statement[j].isdigit():
                    j += 1
                param_index = int(statement[i + 1:j])
                if param_index <= 0 or param_index > len(args):
                    raise ProgrammingError(
                        f"Statement requires parameter ${param_index}, but only {len(args)} values were supplied."
                    )
                max_index = max(max_index, param_index)
                output_query.append(_sql_literal(args[param_index - 1]))
                i = j
                continue
            output_query.append(c)

        elif state == INSIDE_SQ:
            output_query.append(c)
            if c == "'":
                if next_c == "'":
                    output_query.append(next_c)
                    i += 2
                    continue
                state = OUTSIDE

        elif state == INSIDE_QI:
            output_query.append(c)
            if c == '"':
                state = OUTSIDE

        elif state == INSIDE_CO:
            output_query.append(c)
            if c == "\n":
                state = OUTSIDE

        elif state == INSIDE_BC:
            output_query.append(c)
            if c == "*" and next_c == "/":
                output_query.append(next_c)
                state = OUTSIDE
                i += 2
                continue

        i += 1

    return ''.join(output_query), max_index


def walk_array(arr: list[Any]) -> Generator[tuple[list[Any], int, object], None, None]:
    for i, v in enumerate(arr):
        if isinstance(v, list):
            yield from walk_array(v)
        else:
            yield arr, i, v


def array_find_first_element(arr: list[Any]) -> object | None:
    for v in array_flatten(arr):
        if v is not None:
            return v
    return None


def array_flatten(arr: list[Any]) -> Generator[object, None, None]:
    for v in arr:
        if isinstance(v, list):
            yield from array_flatten(v)
        else:
            yield v


def array_check_dimensions(arr: list[Any]) -> list[Any]:
    if len(arr) > 0:
        v0 = arr[0]
        if isinstance(v0, list):
            req_len = len(v0)
            req_inner_lengths = array_check_dimensions(v0)
            for v in arr:
                inner_lengths = array_check_dimensions(v)
                if len(v) != req_len or inner_lengths != req_inner_lengths:
                    raise ArrayDimensionsNotConsistentError(
                        "array dimensions not consistent")
            retval = [req_len]
            retval.extend(req_inner_lengths)
            return retval
        else:
            for v in arr:
                if isinstance(v, list):
                    raise ArrayDimensionsNotConsistentError(
                        "array dimensions not consistent")
    return []


def array_has_null(arr: list[Any]) -> bool:
    for v in array_flatten(arr):
        if v is None:
            return True
    return False


def array_dim_lengths(arr: list[Any]) -> list[Any]:
    len_arr = len(arr)
    retval = [len_arr]
    if len_arr > 0:
        v0 = arr[0]
        if isinstance(v0, list):
            retval.extend(array_dim_lengths(v0))
    return retval


pg_array_types: dict[int, int] = {
    16: 1000,
    25: 1009,
    701: 1022,
    1043: 1009,
    1700: 1231,
}

pg_to_py_encodings: dict[str, str | None] = {
    "mule_internal": None,
    "euc_tw": None,
    "euc_cn": "gb2312",
    "iso_8859_5": "is8859_5",
    "iso_8859_6": "is8859_6",
    "iso_8859_7": "is8859_7",
    "iso_8859_8": "is8859_8",
    "koi8": "koi8_r",
    "latin1": "iso8859-1",
    "latin2": "iso8859_2",
    "latin3": "iso8859_3",
    "latin4": "iso8859_4",
    "latin5": "iso8859_9",
    "latin6": "iso8859_10",
    "latin7": "iso8859_13",
    "latin8": "iso8859_14",
    "latin9": "iso8859_15",
    "sql_ascii": "ascii",
    "win866": "cp866",
    "win874": "cp874",
    "win1250": "cp1250",
    "win1251": "cp1251",
    "win1252": "cp1252",
    "win1253": "cp1253",
    "win1254": "cp1254",
    "win1255": "cp1255",
    "win1256": "cp1256",
    "win1257": "cp1257",
    "win1258": "cp1258",
    "unicode": "utf-8",
}


def infer_columns_from_rows(rows: list[Any]) -> list[tuple[str, str]]:
    """Return ``(name, Netezza type)`` for each column, inferred from every row.

    Columns are named ``col1``, ``col2``, ... The inference rules live in
    ``nzpy_extended._bulk``; see ``docs/load_data.md``.
    """
    if not rows:
        return []
    from ._bulk import infer_columns

    return [(column.sql_name, column.ctype.sql) for column in infer_columns(rows, None)]


__all__ = [
    "pack_funcs", "i_pack", "i_unpack", "h_pack", "h_unpack",
    "q_pack", "q_unpack", "d_pack", "d_unpack",
    "f_pack", "f_unpack",
    "iii_pack", "iii_unpack", "ii_pack", "ii_unpack",
    "qii_pack", "qii_unpack", "dii_pack", "dii_unpack",
    "ihic_pack", "ihic_unpack", "ci_pack", "ci_unpack",
    "c_pack", "c_unpack", "bh_pack", "bh_unpack",
    "cccc_pack", "cccc_unpack",
    "h_le_unpack", "i_le_unpack", "q_le_unpack",
    "min_int2", "max_int2", "min_int4", "max_int4",
    "min_int8", "max_int8",
    "convert_paramstyle", "_sql_literal",
    "render_prepared_statement",
    "walk_array", "array_find_first_element", "array_flatten",
    "array_check_dimensions", "array_has_null",
    "array_dim_lengths",
    "pg_array_types", "pg_to_py_encodings",
    "infer_columns_from_rows",
]
