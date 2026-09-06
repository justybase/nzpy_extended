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
