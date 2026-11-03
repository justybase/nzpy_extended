"""Parsing and DB-API classification of backend ``ErrorResponse`` messages.

Netezza sends an error as plain text, e.g. ``b"ERROR:  Divide by 0\\n\\x00"``.
There is no SQLSTATE, so the DB-API class is chosen from the message text. The
patterns below come from messages captured on a real appliance.

A PostgreSQL-style field list (``C<sqlstate>\\0M<message>\\0\\0``) is still
understood, and its SQLSTATE takes precedence over the message patterns.
"""

from __future__ import annotations

import re

from .exceptions import (
    DataError,
    Error,
    ErrorResponseDict,
    IntegrityError,
    InterfaceError,
    InternalError,
    OperationalError,
    ProgrammingError,
)

_SEVERITY = re.compile(r"^(ERROR|FATAL|PANIC|WARNING|NOTICE):\s*")

# Checked in order; the first match wins. Anything unmatched is ProgrammingError.
_MESSAGE_CLASSES: tuple[tuple[re.Pattern[str], type[Error]], ...] = (
    (re.compile(r"cannot contain null values|violates .*constraint|duplicate key", re.I),
     IntegrityError),
    (re.compile(
        r"divide by 0|division by zero|pg_ato[il]|external representation|"
        r"input format|out of range|overflow|too large for column|"
        r"invalid (input|value|date|time|timestamp|interval|character)",
        re.I),
     DataError),
    (re.compile(
        r"query was cancel|cancell?ed by user|serializ|deadlock|"
        r"out of memory|too many (connections|clients|sessions)|"
        r"connection (lost|reset|closed|terminated|refused)|terminating|shutting down|"
        r"transaction .*abort|abort state",
        re.I),
     OperationalError),
)


def parse_error(data: bytes, encoding: str) -> ErrorResponseDict:
    """Fields of an ``ErrorResponse`` payload; ``M`` is always present."""
    if data.endswith(b"\x00\x00") or data[:1] == b"C":
        fields: dict[str, str] = {}
        for part in data.split(b"\x00"):
            if part:
                fields[part[:1].decode(encoding, "replace")] = part[1:].decode(encoding, "replace")
        if "M" in fields:
            return fields  # type: ignore[return-value]
    text = data.decode(encoding, "replace").rstrip("\x00").strip()
    result: ErrorResponseDict = {}
    match = _SEVERITY.match(text)
    if match:
        result["S"] = match.group(1)
        text = text[match.end():]
    result["M"] = text
    return result


def _class_for_sqlstate(code: str) -> type[Error]:
    if code == "28000":
        return InterfaceError
    if code.startswith("23"):
        return IntegrityError
    if code.startswith("08"):
        return OperationalError
    if code.startswith("22"):
        return DataError
    if code.startswith("26"):
        return InternalError
    if code.startswith(("40", "53", "57")):
        return OperationalError
    return ProgrammingError


def classify(fields: ErrorResponseDict) -> type[Error]:
    """DB-API exception class for a parsed error."""
    code = fields.get("C")
    if code:
        return _class_for_sqlstate(code)
    message = fields.get("M", "")
    for pattern, cls in _MESSAGE_CLASSES:
        if pattern.search(message):
            return cls
    return ProgrammingError


def error_from_response(data: bytes, encoding: str) -> Error:
    """The exception for an ``ErrorResponse`` payload."""
    fields = parse_error(data, encoding)
    return classify(fields)(fields)


__all__ = ["classify", "error_from_response", "parse_error"]
