"""Tolerant SQL document indexing used by execution and completion.

This is deliberately not a full SQL parser. It is string/comment aware and
keeps working while the user is typing incomplete SQL, which is the important
property for an editor completion service.
"""

from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class SqlStatement:
    index: int
    sql: str
    start_offset: int
    end_offset: int


def _masked_sql(sql: str) -> str:
    out = list(sql)
    i = 0
    state = "code"
    while i < len(sql):
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < len(sql) else ""
        if state == "code":
            if ch == "'":
                state = "single"
                out[i] = " "
            elif ch == '"':
                state = "double"
                out[i] = " "
            elif ch == "-" and nxt == "-":
                out[i] = out[i + 1] = " "
                state = "line_comment"
                i += 1
            elif ch == "/" and nxt == "*":
                out[i] = out[i + 1] = " "
                state = "block_comment"
                i += 1
        elif state == "single":
            out[i] = " "
            if ch == "'":
                if nxt == "'":
                    out[i + 1] = " "
                    i += 1
                else:
                    state = "code"
        elif state == "double":
            out[i] = " "
            if ch == '"':
                if nxt == '"':
                    out[i + 1] = " "
                    i += 1
                else:
                    state = "code"
        elif state == "line_comment":
            out[i] = " "
            if ch in "\r\n":
                state = "code"
        else:
            out[i] = " "
            if ch == "*" and nxt == "/":
                out[i + 1] = " "
                state = "code"
                i += 1
        i += 1
    return "".join(out)


def split_statements(sql: str) -> list[SqlStatement]:
    masked = _masked_sql(sql)
    statements: list[SqlStatement] = []
    start = 0
    index = 0
    for position, char in enumerate(masked):
        if char != ";":
            continue
        raw = sql[start:position].strip()
        if raw and _masked_sql(raw).strip():
            left = start + len(sql[start:position]) - len(sql[start:position].lstrip())
            statements.append(SqlStatement(index, raw, left, position))
            index += 1
        start = position + 1
    raw = sql[start:].strip()
    if raw and _masked_sql(raw).strip():
        left = start + len(sql[start:]) - len(sql[start:].lstrip())
        statements.append(SqlStatement(index, raw, left, len(sql)))
    return statements


def statement_for_cursor(sql: str, offset: int) -> SqlStatement | None:
    for statement in split_statements(sql):
        # Monaco reports the cursor after the last character.  For a
        # statement terminated with `;`, that means the offset is one past
        # `end_offset` immediately after typing the terminator.
        if statement.start_offset <= offset <= statement.end_offset + 1:
            return statement
    return None


WRITE_RE = re.compile(
    r"(?is)^\s*(?:INSERT|UPDATE|DELETE|MERGE|CREATE|ALTER|DROP|TRUNCATE|CALL|EXEC(?:UTE)?|GRANT|REVOKE|GROOM|GENERATE|LOAD)\b"
)


def command_type(sql: str) -> str:
    match = re.match(r"(?is)^\s*([A-Za-z]+)", _masked_sql(sql))
    return (match.group(1).upper() if match else "UNKNOWN")


def is_write_statement(sql: str) -> bool:
    return bool(WRITE_RE.search(_masked_sql(sql)))
