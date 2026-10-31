"""Deterministic load_data fixtures shared by offline golden tests and live round trips.

Each ``RoundTrip`` case names the input rows, the optional explicit columns, the
golden text that the renderer must produce for the default options, the SQL types
that inference must choose, and the Python values that Netezza must return after
the load. ``ErrorCase`` entries are inputs that must be refused before any row is
stored, with the exception class and a message fragment.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Any

from nzpy_extended.exceptions import DataError, ProgrammingError

Row = tuple[Any, ...]


@dataclass(frozen=True)
class RoundTrip:
    name: str
    rows: Callable[[], Any]
    expected: Callable[[], list[Row]]
    columns: list[Any] | None = None
    types: tuple[str, ...] | None = None  # SQL types chosen by inference (no explicit columns)
    golden: str | None = None  # rendered text with default options (decoded from the codec)
    existing_ddl: str | None = None  # table created before the load
    kwargs: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ErrorCase:
    name: str
    rows: Callable[[], Any]
    exc: type[Exception]
    fragment: str
    columns: list[Any] | None = None
    existing_ddl: str | None = None
    kwargs: dict[str, Any] = field(default_factory=dict)
    table: str | None = None  # overrides the generated table name (for invalid names)


def _ints_smallint() -> list[Row]:
    return [(1,), (-32768,), (32767,)]


def _stream_rows(count: int) -> Callable[[], Iterator[Row]]:
    def make() -> Iterator[Row]:
        for i in range(count):
            yield (i, f"v{i}")
    return make


def _stream_expected(count: int) -> Callable[[], list[Row]]:
    return lambda: [(i, f"v{i}") for i in range(count)]


ROUND_TRIPS: list[RoundTrip] = [
    RoundTrip(
        "ints_smallint_bounds",
        rows=lambda: _ints_smallint(),
        expected=lambda: [(1,), (-32768,), (32767,)],
        types=("SMALLINT",),
        golden="1\n-32768\n32767\n",
    ),
    RoundTrip(
        # The first row is small; inference must still widen to INTEGER for the later rows.
        "ints_widen_after_first_row",
        rows=lambda: [(1,), (40000,), (5,)],
        expected=lambda: [(1,), (40000,), (5,)],
        types=("INTEGER",),
        golden="1\n40000\n5\n",
    ),
    RoundTrip(
        "ints_bigint",
        rows=lambda: [(3_000_000_000,), (-1,)],
        expected=lambda: [(3_000_000_000,), (-1,)],
        types=("BIGINT",),
        golden="3000000000\n-1\n",
    ),
    RoundTrip(
        "nulls_and_mixed_inference",
        rows=lambda: [(1, None, None, None), (2, "x", 3.5, None)],
        expected=lambda: [(1, None, None, None), (2, "x", 3.5, None)],
        types=("SMALLINT", "VARCHAR(1)", "DOUBLE PRECISION", "VARCHAR(255)"),
        golden="1|\\N|\\N|\\N\n2|x|3.5|\\N\n",
    ),
    RoundTrip(
        # '' is text in a text column and NULL in a numeric one (CSV convention).
        "empty_string_text_vs_numeric",
        rows=lambda: [(1, "", "")],
        expected=lambda: [(1, "", None)],
        columns=[("id", "INT"), ("s", "VARCHAR(10)"), ("n", "INT")],
        golden="1||\\N\n",
    ),
    RoundTrip(
        "delimiter_and_backslash",
        rows=lambda: [(1, "a|b"), (2, "back\\slash"), (3, "trail\\"), (4, "x\\|y")],
        expected=lambda: [(1, "a|b"), (2, "back\\slash"), (3, "trail\\"), (4, "x\\|y")],
        columns=[("id", "INT"), ("s", "VARCHAR(40)")],
        golden=r"1|a\|b" "\n" r"2|back\\slash" "\n" r"3|trail\\" "\n" r"4|x\\\|y" "\n",
    ),
    RoundTrip(
        "line_breaks_inside_values",
        rows=lambda: [(1, "line1\nline2"), (2, "cr\rhere"), (3, "crlf\r\nend")],
        expected=lambda: [(1, "line1\nline2"), (2, "cr\rhere"), (3, "crlf\r\nend")],
        columns=[("id", "INT"), ("s", "VARCHAR(40)")],
        golden="1|line1\\\nline2\n2|cr\\\rhere\n3|crlf\\\r\\\nend\n",
    ),
    RoundTrip(
        # The text \N must not turn into NULL; NULL is written as the bare marker.
        "null_marker_text_and_empty",
        rows=lambda: [(1, "\\N"), (2, None), (3, "")],
        expected=lambda: [(1, "\\N"), (2, None), (3, "")],
        columns=[("id", "INT"), ("s", "VARCHAR(10)")],
        golden="1|\\\\N\n2|\\N\n3|\n",
    ),
    RoundTrip(
        "latin9_accents_varchar",
        rows=lambda: [(1, "café"), (2, "naïve")],
        expected=lambda: [(1, "café"), (2, "naïve")],
        # Any non-ASCII text infers NVARCHAR: always representable, no LATIN9 encoding failures.
        types=("SMALLINT", "NVARCHAR(5)"),
        golden="1|café\n2|naïve\n",
    ),
    RoundTrip(
        "unicode_nvarchar_inferred",
        rows=lambda: [(1, "zażółć"), (2, "漢字")],
        expected=lambda: [(1, "zażółć"), (2, "漢字")],
        types=("SMALLINT", "NVARCHAR(6)"),
        golden="1|zażółć\n2|漢字\n",
    ),
    RoundTrip(
        "unicode_mixed_varchar_and_nvarchar",
        rows=lambda: [(1, "abc", "zażółć")],
        expected=lambda: [(1, "abc", "zażółć")],
        columns=[("id", "INT"), ("a", "VARCHAR(10)"), ("u", "NVARCHAR(10)")],
        golden="1|abc|zażółć\n",
    ),
    RoundTrip(
        "decimal_inference_and_exponent_form",
        rows=lambda: [(Decimal("100.50"),), (Decimal("-0.5"),), (Decimal("1E+2"),)],
        expected=lambda: [(Decimal("100.5"),), (Decimal("-0.5"),), (Decimal("100"),)],
        types=("NUMERIC(4,1)",),
        golden="100.5\n-0.5\n100\n",
    ),
    RoundTrip(
        "float_specials_and_exponents",
        rows=lambda: [(1e-05,), (1e20,), (-2.5,), (float("inf"),), (float("-inf"),), (0.1,)],
        expected=lambda: [(1e-05,), (1e20,), (-2.5,), (float("inf"),), (float("-inf"),), (0.1,)],
        columns=[("f", "DOUBLE PRECISION")],
        golden="1e-05\n1e+20\n-2.5\nInfinity\n-Infinity\n0.1\n",
    ),
    RoundTrip(
        "boolean_text_forms_on_explicit_column",
        rows=lambda: [(1, True), (2, False), (3, "yes"), (4, "NO"), (5, None), (6, "t"), (7, 0)],
        expected=lambda: [(1, True), (2, False), (3, True), (4, False), (5, None), (6, True), (7, False)],
        columns=[("id", "INT"), ("b", "BOOLEAN")],
        golden="1|1\n2|0\n3|1\n4|0\n5|\\N\n6|1\n7|0\n",
    ),
    RoundTrip(
        "boolean_inference",
        rows=lambda: [(True,), (False,), (None,)],
        expected=lambda: [(True,), (False,), (None,)],
        types=("BOOLEAN",),
        golden="1\n0\n\\N\n",
    ),
    RoundTrip(
        "date_time_timestamp_inference",
        rows=lambda: [(date(1900, 1, 1), time(0, 0), datetime(2024, 2, 29, 23, 59, 59, 123456))],
        expected=lambda: [(date(1900, 1, 1), time(0, 0), datetime(2024, 2, 29, 23, 59, 59, 123456))],
        types=("DATE", "TIME", "TIMESTAMP"),
        golden="1900-01-01|00:00:00|2024-02-29 23:59:59.123456\n",
    ),
    RoundTrip(
        "date_only_values_in_timestamp_column",
        rows=lambda: [(datetime(2024, 1, 2, 3, 4, 5),), (date(2024, 1, 3),)],
        expected=lambda: [(datetime(2024, 1, 2, 3, 4, 5),), (datetime(2024, 1, 3, 0, 0),)],
        columns=[("ts", "TIMESTAMP")],
        golden="2024-01-02 03:04:05\n2024-01-03 00:00:00\n",
    ),
    RoundTrip(
        "dict_rows_inferred_names",
        rows=lambda: [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}],
        expected=lambda: [(1, "a"), (2, "b")],
        types=("SMALLINT", "VARCHAR(1)"),
        golden="1|a\n2|b\n",
    ),
    RoundTrip(
        "dict_rows_with_requested_order",
        rows=lambda: [{"id": 1, "name": "a"}, {"name": "b", "id": 2}],
        expected=lambda: [("a", 1), ("b", 2)],
        columns=["name", "id"],
        golden="a|1\nb|2\n",
    ),
    RoundTrip(
        "whitespace_preserved",
        rows=lambda: [("  pad  ",), ("\tTab",)],
        expected=lambda: [("  pad  ",), ("\tTab",)],
        columns=[("s", "VARCHAR(20)")],
        golden="  pad  \n\tTab\n",
    ),
    RoundTrip(
        "long_varchar_near_limit",
        rows=lambda: [("x" * 4000,), ("y" * 1,)],
        expected=lambda: [("x" * 4000,), ("y",)],
        columns=[("s", "VARCHAR(4000)")],
        golden="x" * 4000 + "\ny\n",
    ),
    RoundTrip(
        # VARCHAR(n) counts bytes, NVARCHAR(n) counts characters: 2500 'ż' are 5000 bytes.
        "long_nvarchar_counts_characters",
        rows=lambda: [("ż" * 2500,)],
        expected=lambda: [("ż" * 2500,)],
        columns=[("s", "NVARCHAR(2500)")],
        golden="ż" * 2500 + "\n",
    ),
    RoundTrip(
        "bigint_extremes_explicit",
        rows=lambda: [(-(2**63),), (2**63 - 1,)],
        expected=lambda: [(-(2**63),), (2**63 - 1,)],
        columns=[("n", "BIGINT")],
        golden=f"{-(2**63)}\n{2**63 - 1}\n",
    ),
    RoundTrip(
        "existing_table_catalog_types_and_order",
        rows=lambda: [("a", 1), ("b", 2)],
        expected=lambda: [(1, "a", None, None), (2, "b", None, None)],
        columns=["name", "id"],
        existing_ddl="id INT, name VARCHAR(10), flag BOOLEAN, n NUMERIC(6,2)",
        golden="a|1\nb|2\n",
    ),
    RoundTrip(
        "existing_table_empty_load",
        rows=lambda: [],
        expected=lambda: [],
        existing_ddl="id INT, name VARCHAR(10)",
    ),
    RoundTrip(
        "streamed_generator_20000_rows",
        rows=_stream_rows(20000),
        expected=_stream_expected(20000),
        columns=[("id", "INT"), ("s", "VARCHAR(20)")],
    ),
]


ERROR_CASES: list[ErrorCase] = [
    ErrorCase("tz_aware_datetime", lambda: [(datetime(2024, 1, 1, tzinfo=timezone.utc),)],
              DataError, "timezone-aware"),
    ErrorCase("bytes_value", lambda: [(b"\x00\xff",)], DataError, "bytes"),
    ErrorCase("nan_float_second_row", lambda: [(1.0,), (float("nan"),)], DataError, "row 2"),
    ErrorCase("ragged_row", lambda: [(1, "a"), (2, "b", "extra")], DataError, "row 2"),
    ErrorCase("bad_int_in_explicit_column", lambda: [(1,), ("x",)], DataError, "row 2, column id",
              columns=[("id", "INT")]),
    ErrorCase("int_out_of_range", lambda: [(40000,)], DataError, "out of range",
              columns=[("id", "SMALLINT")]),
    ErrorCase("decimal_too_many_places", lambda: [(Decimal("1.234"),)], DataError,
              "decimal places", columns=[("d", "NUMERIC(5,2)")]),
    ErrorCase("varchar_too_long", lambda: [("abcd",)], DataError, "allows 3",
              columns=[("s", "VARCHAR(3)")]),
    ErrorCase("non_latin9_in_varchar", lambda: [("漢",)], DataError, "cannot be stored",
              columns=[("s", "VARCHAR(10)")]),
    ErrorCase("missing_dict_key", lambda: [{"id": 1, "s": "a"}, {"id": 2}], DataError, "row 2"),
    ErrorCase("unknown_column_for_existing_table", lambda: [("a",)], ProgrammingError,
              "does not exist", columns=["nope"], existing_ddl="id INT, name VARCHAR(10)"),
    ErrorCase("empty_rows_without_table", lambda: [], ProgrammingError, "No rows to load"),
    ErrorCase("missing_table_without_create", lambda: [(1,)], ProgrammingError, "does not exist",
              kwargs={"create_if_missing": False}),
    ErrorCase("delimiter_is_newline", lambda: [(1,)], ProgrammingError, "delimiter",
              kwargs={"delimiter": "\n"}),
    ErrorCase("escape_equals_delimiter", lambda: [(1,)], ProgrammingError, "must differ",
              kwargs={"escape_char": "|"}),
    ErrorCase("delimiter_without_escape", lambda: [("a|b",)], DataError, "escape_char",
              columns=[("s", "VARCHAR(10)")], kwargs={"escape_char": None}),
    ErrorCase("escape_none_with_null_text", lambda: [("\\N",)], DataError, "NULL marker",
              columns=[("s", "VARCHAR(10)")], kwargs={"escape_char": None}),
    ErrorCase("unsupported_encoding", lambda: [(1,)], ProgrammingError, "unsupported encoding",
              kwargs={"encoding": "WIN1252"}),
    ErrorCase("utf8_with_varchar", lambda: [("a",)], ProgrammingError, "UTF8",
              columns=[("s", "VARCHAR(10)")], kwargs={"encoding": "UTF8"}),
    ErrorCase("invalid_table_name", lambda: [(1,)], ProgrammingError, "invalid table name",
              table="bad name; drop"),
    ErrorCase("invalid_column_name", lambda: [(1,)], ProgrammingError, "invalid column name",
              columns=["a b"]),
    ErrorCase("nan_in_numeric_column", lambda: [(float("nan"),)], DataError, "NaN",
              columns=[("d", "NUMERIC(10,2)")]),
    ErrorCase("bad_date_text", lambda: [("2024-13-01",)], DataError, "not a date",
              columns=[("d", "DATE")]),
]

__all__ = ["ROUND_TRIPS", "ERROR_CASES", "RoundTrip", "ErrorCase"]
