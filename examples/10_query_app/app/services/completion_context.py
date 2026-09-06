"""Pure-Python SQL context helpers shared by tests and the frontend spec.

Implements a small, dependency-free subset of the JustyBase completion
context logic: detect whether the cursor is in a table-name position,
a column position, or after ``alias.`` — plus FROM/JOIN table extraction
for scoping column suggestions.

The browser duplicates this logic in ``static/js/completion.js`` (JS cannot
import Python). Keep both implementations aligned and covered by
``tests/test_completion_context.py``.
"""

from __future__ import annotations

import re

_TABLE_POS_RE = re.compile(
    r"(?i)\b(FROM|JOIN|INTO|UPDATE|TABLE)\s+[A-Za-z0-9_.$]*$"
)
_DOT_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_$]*)\.\s*[A-Za-z0-9_$]*$")
_QUALIFIED_RE = re.compile(r"(?P<qual>(?:[A-Za-z_][A-Za-z0-9_$]*\.){1,2})[A-Za-z0-9_$]*$")
_COLUMN_HINT_RE = re.compile(
    r"(?i)\b(SELECT|WHERE|AND|OR|BY|HAVING|ON|=|,|\(|WHEN)\s+[A-Za-z0-9_.$, ]*$"
)
_FROM_JOIN_RE = re.compile(
    r"(?i)\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_$.]*)(?:\s+(?:AS\s+)?([A-Za-z_][A-Za-z0-9_$]*))?"
)


def detect_context(before_cursor: str) -> str:
    """Return 'table' | 'column-dot' | 'column' | 'default'."""
    if _DOT_RE.search(before_cursor):
        return "column-dot"
    if _TABLE_POS_RE.search(before_cursor):
        return "table"
    if _COLUMN_HINT_RE.search(before_cursor):
        return "column"
    return "default"


def dot_prefix(before_cursor: str) -> str:
    """Return the qualifier before the dot (alias or table), or ''."""
    m = _DOT_RE.search(before_cursor)
    return m.group(1) if m else ""


def qualified_prefix(before_cursor: str) -> tuple[list[str], str]:
    """Return identifier parts before the current token and its token prefix.

    Examples: ``BAZ`` -> ``([], "BAZ")``, ``BAZA.`` ->
    ``(["BAZA"], "")`` and ``BAZA.SCHEMA.T`` ->
    ``(["BAZA", "SCHEMA"], "T")``.  ``BAZA..`` is intentionally
    represented as ``(["BAZA", ""], "")`` because Netezza accepts that
    shorthand when resolving objects across schemas.
    """
    shorthand = re.search(r"(?P<database>[A-Za-z_][A-Za-z0-9_$]*)\.\.(?P<token>[A-Za-z0-9_$]*)$", before_cursor)
    if shorthand:
        return [shorthand.group("database"), ""], shorthand.group("token")
    match = re.search(r"(?P<value>[A-Za-z_][A-Za-z0-9_$]*(?:\.[A-Za-z_][A-Za-z0-9_$]*){0,2}\.?|[A-Za-z0-9_$]*)$", before_cursor)
    value = match.group("value") if match else ""
    if value.endswith("."):
        return [part for part in value[:-1].split(".") if part], ""
    if "." in value:
        parts = value.split(".")
        return parts[:-1], parts[-1]
    return [], value


def split_object_name(name: str, default_database: str | None = None, default_schema: str | None = None) -> tuple[str, str | None, str | None]:
    """Resolve Netezza object notation to ``(object, schema, database)``.

    The empty middle component in ``DATABASE..TABLE`` intentionally becomes
    ``None`` rather than an empty schema, so metadata lookup uses all schemas.
    """
    parts = [part.strip() for part in name.split(".")]
    if len(parts) >= 3:
        return parts[-1], parts[-2] or None, parts[-3] or default_database
    if len(parts) == 2:
        return parts[1], parts[0] or default_schema, default_database
    return parts[0], default_schema, default_database


def extract_from_tables(sql: str) -> list[dict[str, str]]:
    """Extract [{name, alias}] from FROM/JOIN clauses (upper-cased names)."""
    out: list[dict[str, str]] = []
    for m in _FROM_JOIN_RE.finditer(sql):
        name = m.group(1).upper()
        alias = (m.group(2) or "").upper()
        # skip bare keywords captured as alias (e.g. "FROM t WHERE")
        if alias in {"WHERE", "GROUP", "ORDER", "LIMIT", "JOIN", "ON", "INNER",
                     "LEFT", "RIGHT", "FULL", "OUTER", "HAVING", "UNION"}:
            alias = ""
        out.append({"name": name, "alias": alias or name.split(".")[-1]})
    return out
