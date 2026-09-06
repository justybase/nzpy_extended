"""Backend SQL authoring service used by Monaco."""

from __future__ import annotations

import re
from typing import Any

from app.services.completion_context import extract_from_tables, qualified_prefix, split_object_name
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
        qualified_parts, qualified_token = qualified_prefix(before)

        async def add_objects(scope_schema: str | None, scope_database: str | None, *, include_schema: bool, insert_schema: bool | None = None, schema_detail: bool = False) -> None:
            tables = await self.schema.get_tables(schema=scope_schema, database=scope_database)
            views = await self.schema.get_views(schema=scope_schema, database=scope_database)
            seen: set[str] = set()
            for item in [*tables, *views][:2000]:
                name = str(item.get("table_name") or item.get("view_name") or "")
                item_schema = str(item.get("schema") or scope_schema or "")
                seen_key = f"{item_schema.upper()}.{name.upper()}" if scope_schema is None else name.upper()
                if not name or seen_key in seen:
                    continue
                seen.add(seen_key)
                object_type = "VIEW" if item.get("view_name") else str(item.get("objtype") or "TABLE")
                label = f"{item_schema}.{name}" if include_schema and item_schema else name
                insert = label if (insert_schema if insert_schema is not None else include_schema) else name
                detail = f"{item_schema} · {object_type}" if schema_detail and item_schema else object_type
                add(label, "view" if object_type.upper() == "VIEW" else "table", detail, insert=insert, priority=2)

        # A dot is ambiguous: ``alias.`` means columns, while
        # ``database.``, ``schema.`` and ``database.schema.`` mean catalog
        # navigation. Resolve the alias case first, then the catalog cases.
        if context == "column-dot" and len(qualified_parts) == 1:
            qualifier = qualified_parts[0].upper()
            for table in from_tables:
                if table["alias"] == qualifier or table["name"].split(".")[-1] == qualifier:
                    table_name, table_schema, table_database = split_object_name(table["name"], database, schema)
                    columns = await self.schema.get_columns(table_name, table_schema, table_database)
                    for column in columns:
                        add(column.get("column_name", ""), "column", column.get("data_type", ""), priority=1)
                    return {"items": items}

        catalog_context = context == "table" or bool(qualified_parts)
        if catalog_context:
            if len(qualified_parts) == 2 and qualified_parts[1] == "" and not qualified_token:
                # database.. -> objects from all schemas in that database.
                await add_objects(None, qualified_parts[0], include_schema=False, insert_schema=False, schema_detail=True)
            elif len(qualified_parts) == 1 and not qualified_token:
                # database. -> schemas. If it is not a database, treat it as
                # schema. so ``FROM schema.`` also lists objects correctly.
                catalog_database = qualified_parts[0]
                databases = [str(item).upper() for item in await self.schema.get_databases(database)]
                if catalog_database.upper() in databases:
                    for item in await self.schema.get_schemas(catalog_database):
                        add(str(item), "schema", f"Schema in {catalog_database}", priority=1)
                else:
                    await add_objects(catalog_database, database, include_schema=False)
            elif len(qualified_parts) == 2 and not qualified_token:
                # database.schema. -> tables and views in that schema.
                await add_objects(qualified_parts[1], qualified_parts[0], include_schema=False)
            elif len(qualified_parts) == 2 and qualified_parts[1] == "":
                # database..table -> objects across schemas in that database.
                await add_objects(None, qualified_parts[0], include_schema=False, insert_schema=False, schema_detail=True)
            elif len(qualified_parts) == 2 and qualified_token:
                # database.schema.table -> table/view names in that schema.
                await add_objects(qualified_parts[1], qualified_parts[0], include_schema=False)
            elif len(qualified_parts) == 1 and qualified_token:
                # schema.table or a partially typed schema qualification.
                await add_objects(qualified_parts[0], database, include_schema=False)
            else:
                await add_objects(schema, database, include_schema=True)

            # In a table position, databases are valid first components too.
            # This makes ``FROM BAZ`` useful instead of silently returning
            # only tables from the configured database.
            if not qualified_parts:
                for item in await self.schema.get_databases(database):
                    add(str(item), "database", "Netezza database", priority=1)

        # At the start of a document, an identifier is commonly the first
        # component of a three-part Netezza name. Offer databases there too;
        # otherwise typing only ``BAZ`` has no useful catalog completion.
        if context == "default" and not qualified_parts and qualified_token:
            for item in await self.schema.get_databases(database):
                add(str(item), "database", "Netezza database", priority=1)
            for prefix_name, label, insert in SNIPPETS:
                add(prefix_name, "snippet", label, insert, priority=4)
        if context in {"column", "default"}:
            seen: set[str] = set()
            for table in from_tables[:8]:
                table_name, table_schema, table_database = split_object_name(table["name"], database, schema)
                columns = await self.schema.get_columns(table_name, table_schema, table_database)
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
