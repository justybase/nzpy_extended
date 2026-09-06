"""Backend SQL authoring service used by Monaco."""

from __future__ import annotations

import re
from typing import Any

from app.services.completion_context import extract_from_tables
from app.services.schema_service import SchemaService
from app.services.sql_document import _masked_sql, split_statements


KEYWORDS = [
    "SELECT", "FROM", "WHERE", "AND", "OR", "NOT", "NULL", "IS", "IN", "JOIN", "ON", "USING",
    "LEFT JOIN", "RIGHT JOIN", "INNER JOIN", "FULL JOIN", "CROSS JOIN", "GROUP BY", "ORDER BY", "HAVING",
    "LIMIT", "OFFSET", "DISTINCT", "UNION", "UNION ALL", "WITH", "AS", "CASE", "WHEN", "THEN", "ELSE", "END",
    "INSERT INTO", "VALUES", "UPDATE", "SET", "DELETE FROM", "MERGE INTO", "CREATE TABLE", "CREATE VIEW",
    "ALTER TABLE", "DROP TABLE", "TRUNCATE TABLE", "CALL", "DISTRIBUTE ON", "ORGANIZE ON", "GROOM TABLE",
    "GENERATE STATISTICS", "COUNT", "SUM", "AVG", "MIN", "MAX", "COALESCE", "NVL", "CAST", "ROW_NUMBER",
    "CURRENT_DATE", "CURRENT_TIMESTAMP", "TRUE", "FALSE", "INT", "INTEGER", "BIGINT", "VARCHAR", "CHAR",
    "BOOLEAN", "NUMERIC", "DECIMAL", "DATE", "TIME", "TIMESTAMP",
]

SNIPPETS = [
    ("nzselect", "SELECT template", "SELECT ${1:column}\nFROM ${2:schema}.${3:table}\nWHERE ${4:condition}\nLIMIT 100;"),
    ("nzjoin", "JOIN template", "SELECT a.${1:column}\nFROM ${2:schema}.${3:table1} a\nJOIN ${4:schema}.${5:table2} b ON a.${6:id} = b.${7:id};"),
    ("nzcte", "CTE template", "WITH ${1:name} AS (\n  SELECT ${2:*} FROM ${3:schema}.${4:table}\n)\nSELECT * FROM ${1:name};"),
]


class LanguageService:
    def __init__(self, schema: SchemaService) -> None:
        self.schema = schema

    async def completion(self, sql: str, offset: int, database: str | None = None, schema: str | None = None) -> dict[str, Any]:
        offset = max(0, min(len(sql), offset))
        before = sql[:offset]
        token = re.search(r"[A-Za-z0-9_$]*$", before)
        prefix = token.group(0) if token else ""
        replace_start = offset - len(prefix)
        context = self._context(before)
        items: list[dict[str, Any]] = []

        def add(label: str, kind: str, detail: str = "", insert: str | None = None, priority: int = 9, documentation: str | None = None) -> None:
            items.append({"label": label, "kind": kind, "detail": detail, "insertText": insert or label, "sortText": f"{priority:02d}_{label.upper()}", "documentation": documentation, "replaceRange": {"start": replace_start, "end": offset}})

        from_tables = extract_from_tables(before)
        if context == "column-dot":
            qualifier_match = re.search(r"([A-Za-z_][A-Za-z0-9_$]*)\.\s*[A-Za-z0-9_$]*$", before)
            qualifier = qualifier_match.group(1).upper() if qualifier_match else ""
            for table in from_tables:
                if table["alias"] == qualifier or table["name"].split(".")[-1] == qualifier:
                    parts = table["name"].split(".")
                    columns = await self.schema.get_columns(parts[-1], parts[-2] if len(parts) > 1 else schema, database)
                    for column in columns:
                        add(column.get("column_name", ""), "column", column.get("data_type", ""), priority=1)
                    return {"items": items}
        if context in {"table", "default"}:
            if context == "table":
                tables = await self.schema.get_tables(schema=schema, database=database)
                views = await self.schema.get_views(schema=schema, database=database)
                for table in [*tables, *views][:1000]:
                    name = table.get("table_name", "")
                    object_type = table.get("objtype", "TABLE")
                    if not name:
                        name = table.get("view_name", "")
                        object_type = "VIEW"
                    full = f"{table.get('schema', schema)}.{name}" if table.get("schema") or schema else name
                    add(full, "view" if str(object_type).upper() == "VIEW" else "table", str(object_type), priority=2)
            else:
                for prefix_name, label, insert in SNIPPETS:
                    add(prefix_name, "snippet", label, insert, priority=4)
        if context in {"column", "default"}:
            seen: set[str] = set()
            for table in from_tables[:8]:
                parts = table["name"].split(".")
                columns = await self.schema.get_columns(parts[-1], parts[-2] if len(parts) > 1 else schema, database)
                for column in columns:
                    name = str(column.get("column_name", ""))
                    if name.upper() not in seen:
                        seen.add(name.upper())
                        add(name, "column", f"{table['alias']} · {column.get('data_type', '')}", priority=1)
        for keyword in KEYWORDS:
            add(keyword, "keyword", "Netezza", priority=8)
        return {"items": items}

    @staticmethod
    def _context(before: str) -> str:
        masked = _masked_sql(before)
        if re.search(r"(?i)\b(FROM|JOIN|INTO|UPDATE|TABLE)\s+[A-Za-z0-9_.$]*$", masked):
            return "table"
        if re.search(r"(?i)[A-Za-z_][A-Za-z0-9_$]*\.\s*[A-Za-z0-9_$]*$", masked):
            return "column-dot"
        if re.search(r"(?i)\b(SELECT|WHERE|AND|OR|BY|HAVING|ON|=|,|\(|WHEN)\s+[A-Za-z0-9_.$, ]*$", masked):
            return "column"
        return "default"

    async def diagnostics(self, sql: str, database: str | None = None, schema: str | None = None) -> dict[str, Any]:
        diagnostics: list[dict[str, Any]] = []
        if sql.count("(") != sql.count(")"):
            diagnostics.append({"message": "Unbalanced parentheses", "severity": "error", "code": "PAR001", "start": {"line": 0, "character": 0}, "end": {"line": 0, "character": max(1, len(sql))}})
        statements = split_statements(sql)
        if sql.strip() and not statements:
            diagnostics.append({"message": "No executable SQL statement found", "severity": "error", "code": "SQL001", "start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 1}})
        return {"diagnostics": diagnostics}
