"""Offline tests for the load_data pipeline: validation, inference, encoding, rendering.

Nothing here talks to a server. The same fixtures drive the live round trips in
``test_load_data_roundtrip.py``. The escape/parse round trip below is a property
check: rendered text must decode back to the original values.
"""

from __future__ import annotations

import asyncio
import random
import re
from datetime import date, datetime, time
from decimal import Decimal

import pytest

from nzpy_extended import _bulk
from nzpy_extended.exceptions import DataError, ProgrammingError
from tests.load_data_fixtures import ERROR_CASES, ROUND_TRIPS

pytestmark = pytest.mark.unit


def _opts(delimiter: str = "|", escape_char: str | None = "\\", encoding: str | None = None):
    return _bulk.build_options(delimiter, escape_char, encoding, ".")


def _columns_for(rows, columns):
    requested = _bulk.requested_columns(columns)
    explicit = _bulk.explicit_columns(requested)
    return explicit if explicit is not None else _bulk.infer_columns(list(rows), requested)


def _render(values, columns, **opts):
    rows = [(v,) for v in values]
    cols = _columns_for(rows, columns)
    renderer = _bulk.RowRenderer(cols, _opts(**opts))
    return renderer.render(rows).decode(renderer.codec)


def _parse(text: str, delimiter: str = "|", escape: str | None = "\\") -> list[list[str | None]]:
    """Decode rendered text the way the server reads it: escapes, delimiter, NULL marker."""
    rows: list[list[str | None]] = []
    row: list[str | None] = []
    chars: list[str] = []
    raw: list[str] = []

    def end_field() -> None:
        raw_text = "".join(raw)
        row.append(None if raw_text == _bulk.NULL_MARKER else "".join(chars))
        chars.clear()
        raw.clear()

    i = 0
    while i < len(text):
        ch = text[i]
        if escape is not None and ch == escape and i + 1 < len(text):
            raw.append(text[i:i + 2])
            chars.append(text[i + 1])
            i += 2
            continue
        if ch == delimiter:
            end_field()
        elif ch == "\n":
            end_field()
            rows.append(row)
            row = []
        else:
            raw.append(ch)
            chars.append(ch)
        i += 1
    assert not raw and not row, "rendered text must end on a row boundary"
    return rows


# ----- golden output and inference, driven by the shared fixtures -----------

GOLDEN_CASES = [c for c in ROUND_TRIPS if c.golden is not None and c.existing_ddl is None]
TYPED_CASES = [c for c in ROUND_TRIPS if c.types is not None]
OFFLINE_ERRORS = [c for c in ERROR_CASES
                  if c.existing_ddl is None and c.kwargs.get("create_if_missing") is not False]


@pytest.mark.parametrize("case", GOLDEN_CASES, ids=lambda c: c.name)
def test_rendered_text_matches_golden(case):
    rows = list(case.rows())
    renderer = _bulk.RowRenderer(_columns_for(rows, case.columns), _opts())
    assert renderer.render(rows).decode(renderer.codec) == case.golden


@pytest.mark.parametrize("case", TYPED_CASES, ids=lambda c: c.name)
def test_inferred_types_match_fixture(case):
    columns = _bulk.infer_columns(list(case.rows()), _bulk.requested_columns(case.columns))
    assert tuple(c.ctype.sql for c in columns) == case.types


@pytest.mark.parametrize("case", OFFLINE_ERRORS, ids=lambda c: c.name)
def test_bad_input_is_refused_before_any_load(case):
    with pytest.raises(case.exc, match=re.escape(case.fragment)):
        if case.table is not None:
            _bulk.check_table_name(case.table)
            return
        opts = _bulk.build_options(case.kwargs.get("delimiter", "|"),
                                   case.kwargs.get("escape_char", "\\"),
                                   case.kwargs.get("encoding"), ".")
        rows = list(case.rows())
        columns = _columns_for(rows, case.columns)
        _bulk.RowRenderer(columns, opts).render(rows)


# ----- escaping round trip ----------------------------------------------------

_ALPHABET = "ab|,\"\\N\n\r \t é"


def _random_value(rng: random.Random, delimiter: str, escape: str | None) -> str | None:
    if rng.random() < 0.1:
        return None
    while True:
        value = "".join(rng.choice(_ALPHABET) for _ in range(rng.randrange(0, 12)))
        if escape is None and (value == _bulk.NULL_MARKER or any(c in value for c in delimiter + "\r\n")):
            continue
        return value


@pytest.mark.parametrize(("delimiter", "escape"), [
    ("|", "\\"),
    ("\t", "\\"),
    (",", '"'),
    ("|", None),
], ids=["pipe-backslash", "tab-backslash", "comma-quote", "pipe-no-escape"])
def test_random_text_round_trips_through_escaping(delimiter, escape):
    rng = random.Random(20240601)
    values = [_random_value(rng, delimiter, escape) for _ in range(400)]
    rows = [(i, v) for i, v in enumerate(values)]
    columns = [("id", "INT"), ("s", "VARCHAR(200)")]
    renderer = _bulk.RowRenderer(_columns_for(rows, columns), _opts(delimiter, escape))
    text = renderer.render(rows).decode(renderer.codec)

    parsed = _parse(text, delimiter, escape)
    assert parsed == [[str(i), v] for i, v in enumerate(values)]


def test_text_null_marker_is_distinct_from_null():
    assert _render(["\\N", None], [("s", "VARCHAR(5)")]) == "\\\\N\n\\N\n"


def test_nvarchar_round_trip_keeps_unicode():
    values = ["zażółć", "漢字", "a|b\\c"]
    rows = [(v,) for v in values]
    renderer = _bulk.RowRenderer(_columns_for(rows, [("s", "NVARCHAR(20)")]), _opts())
    text = renderer.render(rows).decode(renderer.codec)
    assert [r[0] for r in _parse(text)] == values


# ----- streaming --------------------------------------------------------------

def test_stream_chunks_concatenate_to_full_render():
    rows = [(i, f"v{i}") for i in range(_bulk.BATCH_ROWS * 2 + 7)]
    renderer = _bulk.RowRenderer(_columns_for(rows, [("id", "INT"), ("s", "VARCHAR(20)")]), _opts())

    async def collect():
        return [chunk async for chunk in renderer.stream(rows)]

    chunks = asyncio.run(collect())
    assert len(chunks) == 3
    assert b"".join(chunks) == renderer.render(rows)


def test_stream_accepts_async_source_and_numbers_errors_across_batches():
    total = _bulk.BATCH_ROWS + 5
    bad_index = _bulk.BATCH_ROWS + 2  # zero-based, so row number BATCH_ROWS + 3

    async def source():
        for i in range(total):
            yield ("not-int",) if i == bad_index else (i,)

    renderer = _bulk.RowRenderer(_columns_for([(0,)], [("id", "INT")]), _opts())

    async def collect():
        return [chunk async for chunk in renderer.stream(source())]

    with pytest.raises(DataError, match=rf"row {_bulk.BATCH_ROWS + 3}, column id"):
        asyncio.run(collect())


# ----- identifiers, options, types --------------------------------------------

@pytest.mark.parametrize("name", ["ZZ_LDX_1", "SCHEMA1.TABLE1", "DB.SCHEMA1.TABLE1", '"My Table"'])
def test_check_table_name_accepts_common_forms(name):
    assert _bulk.check_table_name(name) == name


@pytest.mark.parametrize("name", ["", "a b", "t;drop", "a.b.c.d"])
def test_check_table_name_rejects_unsafe_names(name):
    with pytest.raises(ProgrammingError, match="invalid table name"):
        _bulk.check_table_name(name)


def test_user_identifier_folds_bare_names_and_keeps_quoted_ones():
    assert _bulk._user_identifier("id") == ("id", ("id", "ID"))
    assert _bulk._user_identifier('"Mixed"') == ('"Mixed"', ("Mixed",))


@pytest.mark.parametrize("name", ["a b", "x;", ""])
def test_user_identifier_rejects_unsafe_names(name):
    with pytest.raises(ProgrammingError, match="invalid column name"):
        _bulk._user_identifier(name)


@pytest.mark.parametrize(("delimiter", "escape"), [
    ("\n", "\\"),
    ("\r", "\\"),
    ("ab", "\\"),
    ("|", "\r"),
])
def test_build_options_rejects_bad_delimiter_or_escape(delimiter, escape):
    with pytest.raises(ProgrammingError):
        _bulk.build_options(delimiter, escape, None, ".")


def test_build_options_rejects_equal_escape_and_delimiter():
    with pytest.raises(ProgrammingError, match="must differ"):
        _bulk.build_options("|", "|", None, ".")


def test_build_options_uppercases_encoding_and_allows_tab_delimiter():
    options = _bulk.build_options("\t", None, "utf8", ".")
    assert options.encoding == "UTF8"
    assert options.delimiter == "\t"
    assert options.escape_char is None


def test_requested_columns_normalises_names_and_pairs():
    assert _bulk.requested_columns(["a", ("b", "INT")]) == [("a", None), ("b", "INT")]
    assert _bulk.requested_columns(None) is None


def test_requested_columns_rejects_duplicates_and_empty():
    with pytest.raises(ProgrammingError, match="duplicate column name"):
        _bulk.requested_columns(["id", "ID"])
    with pytest.raises(ProgrammingError, match="must not be empty"):
        _bulk.requested_columns([])


@pytest.mark.parametrize(("declared", "sql"), [
    ("INT", "INTEGER"),
    ("VARCHAR(10)", "VARCHAR(10)"),
    ("NUMERIC(6,2)", "NUMERIC(6,2)"),
    ("BOOLEAN", "BOOLEAN"),
    ("TIMESTAMP", "TIMESTAMP"),
])
def test_parse_type_normalises_declared_types(declared, sql):
    assert _bulk.parse_type(declared).sql == sql


def test_parse_type_flags_national_text_and_numeric_shape():
    nvarchar = _bulk.parse_type("NVARCHAR(5)")
    assert nvarchar.kind == "text" and nvarchar.national and nvarchar.length == 5
    numeric = _bulk.parse_type("NUMERIC(6,2)")
    assert (numeric.kind, numeric.precision, numeric.scale) == ("numeric", 6, 2)


def test_parse_type_rejects_unknown_types():
    with pytest.raises(ProgrammingError, match="unsupported column type"):
        _bulk.parse_type("FROBNICATE")


# ----- encoding choice --------------------------------------------------------

def _col(name: str, declared: str) -> _bulk.Column:
    return _bulk.Column(name, (name, name.upper()), _bulk.parse_type(declared))


@pytest.mark.parametrize(("columns", "expected"), [
    ([_col("n", "INT")], "LATIN9"),
    ([_col("s", "VARCHAR(5)")], "LATIN9"),
    ([_col("u", "NVARCHAR(5)")], "UTF8"),
    ([_col("s", "VARCHAR(5)"), _col("u", "NVARCHAR(5)")], "INTERNAL"),
])
def test_automatic_encoding_follows_text_columns(columns, expected):
    assert _bulk.resolve_encoding(_opts(), columns) == expected


def test_explicit_encoding_wins_when_compatible():
    assert _bulk.resolve_encoding(_opts(encoding="INTERNAL"), [_col("s", "VARCHAR(5)")]) == "INTERNAL"


def test_utf8_with_varchar_is_refused():
    with pytest.raises(ProgrammingError, match="UTF8"):
        _bulk.resolve_encoding(_opts(encoding="UTF8"), [_col("s", "VARCHAR(5)")])


# ----- inference --------------------------------------------------------------

@pytest.mark.parametrize(("values", "sql"), [
    ([1, 40000], "INTEGER"),
    ([-(2 ** 31)], "INTEGER"),
    ([2 ** 31], "BIGINT"),
    ([1, 2.5], "DOUBLE PRECISION"),
    ([Decimal("1.5"), Decimal("12")], "NUMERIC(3,1)"),
    ([date(2024, 1, 2)], "DATE"),
    ([time(12, 34)], "TIME"),
    ([date(2024, 1, 2), datetime(2024, 1, 2, 3, 4)], "TIMESTAMP"),
    (["2024-01-02", "2024-01-03"], "DATE"),
    (["1", "2"], "SMALLINT"),
    ([True, None], "BOOLEAN"),
    ([None, None], "VARCHAR(255)"),
], ids=repr)
def test_inference_picks_narrowest_type(values, sql):
    rows = [(v,) for v in values]
    assert _bulk.infer_columns(rows, None)[0].ctype.sql == sql


def test_inference_refuses_integers_beyond_bigint():
    with pytest.raises(DataError, match="exceed BIGINT"):
        _bulk.infer_columns([(2 ** 63,)], None)


def test_inference_refuses_bytes_and_non_finite_decimal():
    with pytest.raises(DataError, match="bytes"):
        _bulk.infer_columns([(b"\x00",)], None)
    with pytest.raises(DataError, match="non-finite"):
        _bulk.infer_columns([(Decimal("NaN"),)], None)


def test_inference_scans_every_row_not_only_the_first():
    rows = [(1,), (2,), (2 ** 40,)]
    assert _bulk.infer_columns(rows, None)[0].ctype.sql == "BIGINT"


def test_inference_names_ragged_row_and_rejects_empty_input():
    with pytest.raises(DataError, match="row 2"):
        _bulk.infer_columns([(1, "a"), (2,)], None)
    with pytest.raises(ProgrammingError, match="No rows to load"):
        _bulk.infer_columns([], None)


def test_dict_rows_match_mixed_case_keys_to_columns():
    rows = [{"ID": 5}]
    assert _render([5], [("id", "INT")]) == "5\n"
    renderer = _bulk.RowRenderer([_col("id", "INT")], _opts())
    assert renderer.render(rows).decode("iso8859-15") == "5\n"


# ----- value rendering --------------------------------------------------------

@pytest.mark.parametrize(("value", "text"), [
    ("true", "1"), ("Yes", "1"), ("on", "1"), ("t", "1"), ("1", "1"), (True, "1"),
    ("off", "0"), ("N", "0"), ("f", "0"), (0, "0"), ("", "\\N"), (None, "\\N"),
])
def test_boolean_text_forms(value, text):
    assert _render([value], [("b", "BOOLEAN")]) == text + "\n"


def test_boolean_rejects_unknown_word():
    with pytest.raises(DataError, match="is not a boolean"):
        _render(["maybe"], [("b", "BOOLEAN")])


@pytest.mark.parametrize(("value", "text"), [
    ("+7", "7"), (3.0, "3"), (Decimal("4"), "4"), ("", "\\N"),
])
def test_integer_accepts_whole_number_forms(value, text):
    assert _render([value], [("n", "INT")]) == text + "\n"


@pytest.mark.parametrize("value", [3.5, "x", True])
def test_integer_rejects_fractions_text_and_bools(value):
    with pytest.raises(DataError, match="column n"):
        _render([value], [("n", "INT")])


@pytest.mark.parametrize(("value", "text"), [
    (1.0, "1.0"), (1e16, "1e+16"), (-0.0, "-0.0"), (float("inf"), "Infinity"),
    (float("-inf"), "-Infinity"), (Decimal("2.5"), "2.5"),
])
def test_float_text_forms(value, text):
    assert _render([value], [("f", "DOUBLE PRECISION")]) == text + "\n"


def test_numeric_normalises_exponent_and_checks_shape():
    assert _render([Decimal("1E+2")], [("d", "NUMERIC(5,2)")]) == "100\n"
    assert _render([Decimal("3.14")], [("d", "NUMERIC(5,2)")]) == "3.14\n"
    with pytest.raises(DataError, match="integer digits"):
        _render([Decimal("1234567.5")], [("d", "NUMERIC(5,1)")])


def test_date_accepts_midnight_datetime_and_rejects_time_part():
    assert _render([datetime(2024, 1, 1)], [("d", "DATE")]) == "2024-01-01\n"
    with pytest.raises(DataError, match="time part"):
        _render([datetime(2024, 1, 1, 12, 0)], [("d", "DATE")])


def test_time_and_timestamp_text_inputs_are_normalised():
    assert _render(["12:34:56"], [("t", "TIME")]) == "12:34:56\n"
    assert _render(["2024-01-02T03:04:05"], [("t", "TIMESTAMP")]) == "2024-01-02 03:04:05\n"
    with pytest.raises(DataError, match="is not a time"):
        _render(["25:99"], [("t", "TIME")])


def test_varchar_length_counts_bytes_of_the_column_encoding():
    # LATIN9 is one byte per character, so 'ééa' fits VARCHAR(4) ...
    assert _render(["ééa"], [("s", "VARCHAR(4)")]) == "ééa\n"
    # ... while UTF-8 (INTERNAL) makes each é two bytes.
    assert _render(["éé"], [("s", "VARCHAR(4)")], encoding="INTERNAL") == "éé\n"
    with pytest.raises(DataError, match="5 bytes long; the column allows 4"):
        _render(["ééa"], [("s", "VARCHAR(4)")], encoding="INTERNAL")


def test_nvarchar_length_counts_characters():
    assert _render(["漢字者"], [("s", "NVARCHAR(3)")]) == "漢字者\n"
    with pytest.raises(DataError, match="4 characters long"):
        _render(["漢字者者"], [("s", "NVARCHAR(3)")])


def test_latin9_text_refuses_characters_outside_the_codec():
    with pytest.raises(DataError, match="cannot be stored with encoding LATIN9"):
        _render(["漢"], [("s", "VARCHAR(5)")])


def test_text_refuses_bytes_and_null_marker_without_escape():
    with pytest.raises(DataError, match="binary values"):
        _render([b"x"], [("s", "VARCHAR(5)")])
    with pytest.raises(DataError, match="NULL marker"):
        _render(["\\N"], [("s", "VARCHAR(5)")], escape_char=None)


def test_escape_none_refuses_delimiter_and_line_breaks():
    with pytest.raises(DataError, match="escape_char"):
        _render(["a|b"], [("s", "VARCHAR(5)")], escape_char=None)
    with pytest.raises(DataError, match="escape_char"):
        _render(["a\nb"], [("s", "VARCHAR(5)")], escape_char=None)


def test_string_rows_are_rejected_not_split_into_characters():
    renderer = _bulk.RowRenderer([_col("s", "VARCHAR(5)")], _opts())
    with pytest.raises(DataError, match="a row must be a tuple"):
        renderer.render(["ab"])


def test_dict_row_with_wrong_key_count_is_named_by_row():
    renderer = _bulk.RowRenderer([_col("id", "INT")], _opts())
    with pytest.raises(DataError, match="row 1: expected keys for 1 columns, got 2 keys"):
        renderer.render([{"ID": 1, "EXTRA": 2}])


# ----- generated SQL ----------------------------------------------------------

def test_insert_sql_carries_every_external_option():
    columns = [_col("id", "INT"), _col("ok", "BOOLEAN")]
    sql = _bulk.insert_sql("T", columns, "__nzpy_stream__", _opts(), "LATIN9")
    for fragment in ("INSERT INTO T (id, ok)", "EXTERNAL '__nzpy_stream__'",
                     "ENCODING 'LATIN9'", "REMOTESOURCE 'python'", "DELIMITER '|'",
                     "ESCAPECHAR '\\'", "NULLVALUE '\\N'", "BOOLSTYLE '1_0'", "LOGDIR '.'"):
        assert fragment in sql, fragment


def test_insert_sql_omits_escapechar_when_disabled_and_boolstyle_without_booleans():
    sql = _bulk.insert_sql("T", [_col("id", "INT")], "__nzpy_stream__", _opts(escape_char=None), "LATIN9")
    assert "ESCAPECHAR" not in sql
    assert "BOOLSTYLE" not in sql


def test_create_table_sql_variants():
    columns = [_col("id", "INT"), _col("ok", "BOOLEAN")]
    assert _bulk.create_table_sql("T", columns, temporary=False, distribute_on_random=True) == \
        "CREATE TABLE T (id INTEGER, ok BOOLEAN) DISTRIBUTE ON RANDOM"
    assert _bulk.create_table_sql("T", columns, temporary=True, distribute_on_random=False) == \
        "CREATE TEMP TABLE T (id INTEGER, ok BOOLEAN)"
