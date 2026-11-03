"""Backend ErrorResponse parsing and DB-API classification.

The payloads are verbatim messages captured from a Netezza appliance.
"""

import pytest

from nzpy_extended._errors import classify, error_from_response, parse_error
from nzpy_extended.exceptions import (
    DataError,
    Error,
    IntegrityError,
    InterfaceError,
    InternalError,
    OperationalError,
    ProgrammingError,
)

pytestmark = [pytest.mark.full, pytest.mark.unit]

APPLIANCE_MESSAGES = [
    (b"ERROR:  Divide by 0\n\x00", DataError),
    (b'ERROR:  pg_atoi: error in "abc": can\'t parse "abc"\n\x00', DataError),
    (b"ERROR:  Bad date external representation '2020-13-45'\n\x00", DataError),
    (b"ERROR:  Bad numeric input format 'x1'\n\x00", DataError),
    (b'ERROR:  pg_atoi: error reading "99999999999": Numerical result out of range\n\x00', DataError),
    (b"ERROR:  Numeric overflow.  Value does not fit in a numeric(4,2)\n\x00", DataError),
    (b"ERROR:  value 'abcdefghijk' too large for column \n\x00", DataError),
    (b"ERROR:  Column 2 : Field cannot contain null values\n\x00", IntegrityError),
    (b"ERROR:  relation does not exist JUST_DATA.ADMIN.ZZ_RUST_NO_SUCH_TABLE\n\x00", ProgrammingError),
    (b"ERROR:  Attribute 'NO_SUCH_COL' not found\n\x00", ProgrammingError),
    (b"ERROR:  'SELEC 1'\nerror    ^ found \"SELEC\" (at char 1) expecting a keyword\n\x00",
     ProgrammingError),
    (b"ERROR:  Function 'NO_SUCH_FUNC(INT4)' does not exist\n\tUnable to identify a function\n\x00",
     ProgrammingError),
    (b'ERROR:  CREATE TEMP TABLE: object "ZZ_RUST_AUDIT_E" already exists.\n\x00', ProgrammingError),
    (b"ERROR:  Attribute ZZ_RUST_AUDIT_E.V must be GROUPed or used in an aggregate function\n\x00",
     ProgrammingError),
    (b"ERROR:  Query was cancelled.\n\x00", OperationalError),
    # A column whose name merely contains "connection" is not a connectivity error.
    (b"ERROR:  Attribute 'CONNECTION_ID' not found\n\x00", ProgrammingError),
]


@pytest.mark.parametrize("payload, expected", APPLIANCE_MESSAGES)
def test_appliance_messages_are_classified(payload: bytes, expected: type) -> None:
    exc = error_from_response(payload, "utf8")
    assert type(exc) is expected


def test_message_is_plain_text_without_severity_or_terminator() -> None:
    exc = error_from_response(b"ERROR:  Divide by 0\n\x00", "utf8")
    assert str(exc) == "Divide by 0"
    assert exc.diag == {"S": "ERROR", "M": "Divide by 0"}
    assert exc.sqlstate is None


def test_multiline_message_keeps_its_detail_lines() -> None:
    fields = parse_error(b"ERROR:  'SELEC 1'\nerror    ^ found \"SELEC\"\n\x00", "utf8")
    assert fields["M"] == "'SELEC 1'\nerror    ^ found \"SELEC\""


def test_undecodable_bytes_do_not_raise() -> None:
    exc = error_from_response(b"ERROR:  bad \xff byte\n\x00", "utf8")
    assert "bad" in str(exc)


@pytest.mark.parametrize("code, expected", [
    ("23505", IntegrityError),
    ("28000", InterfaceError),
    ("22012", DataError),
    ("26000", InternalError),
    ("40001", OperationalError),
    ("57014", OperationalError),
    ("42601", ProgrammingError),
])
def test_postgres_style_fields_use_the_sqlstate(code: str, expected: type) -> None:
    payload = b"SERROR\x00C" + code.encode() + b"\x00Mtest msg\x00\x00"
    exc = error_from_response(payload, "utf8")
    assert type(exc) is expected
    assert exc.sqlstate == code
    assert str(exc) == "test msg"


def test_error_str_falls_back_for_plain_arguments() -> None:
    assert str(ProgrammingError("plain")) == "plain"
    assert str(Error({"M": "from dict"})) == "from dict"
    assert classify({"M": "anything else"}) is ProgrammingError
