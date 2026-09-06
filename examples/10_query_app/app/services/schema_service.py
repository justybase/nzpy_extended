"""Cached, database-aware Netezza catalog service."""

from __future__ import annotations

import base64
import json
import time
from collections import OrderedDict
from typing import Any


def escape_like(value: str) -> str:
    return value.replace("'", "''")


def escape_catalog_value(value: str | None) -> str | None:
    """Escape values passed to the driver's catalog SQL builders."""
    return value.replace("'", "''") if value is not None else None


def encode_node(value: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).decode()


def decode_node(value: str) -> dict[str, Any]:
    return json.loads(base64.urlsafe_b64decode(value.encode()).decode())


class SchemaService:
    GROUPS = ("TABLE", "VIEW", "EXTERNAL TABLE", "PROCEDURE", "SEQUENCE", "SYNONYM")

    def __init__(self, pool_getter: Any, ttl_seconds: int = 300) -> None:
        self._pool_getter = pool_getter
        self._ttl = ttl_seconds
        self._cache: dict[str, tuple[float, Any]] = {}
        self._columns_lru: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
        self._columns_lru_size = 500

    async def _pool(self, database: str | None = None) -> Any:
        try:
            value = self._pool_getter(database)
        except TypeError:
            value = self._pool_getter()
        if hasattr(value, "__await__"):
            return await value
        return value

    def _get_cached(self, key: str) -> Any | None:
        hit = self._cache.get(key)
        if not hit:
            return None
        ts, value = hit
        if time.monotonic() - ts > self._ttl:
            self._cache.pop(key, None)
            return None
        return value

    def _set_cached(self, key: str, value: Any) -> None:
        self._cache[key] = (time.monotonic(), value)

    def invalidate(self, database: str | None = None, schema: str | None = None) -> None:
        if not database and not schema:
            self._cache.clear()
            self._columns_lru.clear()
            return
        wanted_database = (database or "").upper()
        wanted_schema = (schema or "").upper()

        def belongs(key: str) -> bool:
            key_database, separator, rest = key.partition(":")
            if wanted_database and key_database != wanted_database:
                return False
            if not wanted_schema:
                return True
            key_schema = rest.split(":", 1)[0].split(".", 1)[0].upper() if separator else ""
            return key_schema == wanted_schema

        for key in list(self._cache):
            if belongs(key):
                self._cache.pop(key, None)
        for key in list(self._columns_lru):
            if belongs(key):
                self._columns_lru.pop(key, None)

    async def get_databases(self, database: str | None = None) -> list[str]:
        pool = await self._pool(database)
        async with pool.connection() as conn:
            method = getattr(conn.meta, "get_databases", None)
            if callable(method):
                return await method()
        return [database] if database else []

    async def get_schemas(self, database: str | None = None) -> list[str]:
        key = f"{(database or '').upper()}:schemas"
        cached = self._get_cached(key)
        if cached is not None:
            return cached
        pool = await self._pool(database)
        async with pool.connection() as conn:
            rows = await conn.meta.get_schemas()
        self._set_cached(key, rows)
        return rows

    async def get_tables(self, schema: str | None = None, database: str | None = None) -> list[dict[str, Any]]:
        key = f"{(database or '').upper()}:{(schema or '').upper()}:tables"
        cached = self._get_cached(key)
        if cached is not None:
            return cached
        pool = await self._pool(database)
        async with pool.connection() as conn:
            rows = await conn.meta.get_tables(schema=escape_catalog_value(schema))
        self._set_cached(key, rows)
        return rows

    async def get_views(self, schema: str | None = None, database: str | None = None) -> list[dict[str, Any]]:
        key = f"{(database or '').upper()}:{(schema or '').upper()}:views"
        cached = self._get_cached(key)
        if cached is not None:
            return cached
        pool = await self._pool(database)
        async with pool.connection() as conn:
            rows = await conn.meta.get_views(schema=escape_catalog_value(schema))
        self._set_cached(key, rows)
        return rows

    async def get_procedures(self, schema: str | None = None, database: str | None = None) -> list[dict[str, Any]]:
        pool = await self._pool(database)
        async with pool.connection() as conn:
            return await conn.meta.get_procedures(schema=escape_catalog_value(schema))

    async def get_sequences(self, schema: str | None = None, database: str | None = None) -> list[dict[str, Any]]:
        pool = await self._pool(database)
        async with pool.connection() as conn:
            method = getattr(conn.meta, "get_sequences", None)
            return await method(schema=escape_catalog_value(schema)) if callable(method) else []

    async def get_synonyms(self, schema: str | None = None, database: str | None = None) -> list[dict[str, Any]]:
        pool = await self._pool(database)
        async with pool.connection() as conn:
            method = getattr(conn.meta, "get_synonyms", None)
            return await method(schema=escape_catalog_value(schema)) if callable(method) else []

    async def get_columns(self, table: str, schema: str | None = None, database: str | None = None) -> list[dict[str, Any]]:
        key = f"{(database or '').upper()}:{(schema or '').upper()}.{table.upper()}"
        if key in self._columns_lru:
            self._columns_lru.move_to_end(key)
            return self._columns_lru[key]
        pool = await self._pool(database)
        async with pool.connection() as conn:
            rows = await conn.meta.get_columns(escape_catalog_value(table) or "", schema=escape_catalog_value(schema))
        self._columns_lru[key] = rows
        if len(self._columns_lru) > self._columns_lru_size:
            self._columns_lru.popitem(last=False)
        return rows

    async def table_detail(self, table: str, schema: str | None = None, database: str | None = None) -> dict[str, Any]:
        pool = await self._pool(database)
        async with pool.connection() as conn:
            safe_table = escape_catalog_value(table) or ""
            safe_schema = escape_catalog_value(schema)
            columns = await conn.meta.get_columns(safe_table, schema=safe_schema)
            dist = await conn.meta.get_distribution_key(safe_table, schema=safe_schema)
            sizes = await conn.meta.get_table_sizes(schema=safe_schema.upper() if safe_schema else None, table_pattern=escape_like(table.upper()))
        size = next((item.get("size_mb") for item in sizes if str(item.get("table_name", "")).upper() == table.upper()), None)
        return {"database": database, "schema": (schema or "").upper(), "table_name": table.upper(), "object_type": "TABLE", "columns": columns, "distribution_key": dist, "size_mb": size}

    async def search(self, pattern: str, schema: str | None = None, database: str | None = None) -> list[dict[str, Any]]:
        safe = escape_like(pattern)
        pool = await self._pool(database)
        async with pool.connection() as conn:
            return await conn.meta.search_objects(safe, schema=schema)

    async def tree(self, parent_id: str | None, database: str | None = None) -> dict[str, Any]:
        if not parent_id:
            databases = await self.get_databases(database)
            if not databases:
                databases = [database] if database else []
            return {"nodes": [{"id": encode_node({"kind": "database", "database": item}), "kind": "database", "label": item, "database": item, "has_children": True} for item in databases]}
        parent = decode_node(parent_id)
        kind = parent.get("kind")
        db = parent.get("database")
        if kind == "database":
            return {"nodes": [{"id": encode_node({"kind": "schema", "database": db, "schema": schema}), "kind": "schema", "label": schema, "database": db, "schema": schema, "has_children": True} for schema in await self.get_schemas(db)]}
        if kind == "schema":
            return {"nodes": [{"id": encode_node({"kind": "group", "database": db, "schema": parent.get("schema"), "object_type": group}), "kind": "group", "label": f"{group.title()}s", "database": db, "schema": parent.get("schema"), "object_type": group, "has_children": True} for group in self.GROUPS]}
        if kind == "group":
            schema = parent.get("schema")
            object_type = parent.get("object_type")
            if object_type == "TABLE":
                items = await self.get_tables(schema, db)
                items = [dict(item, object_type=item.get("objtype", "TABLE")) for item in items]
            elif object_type == "VIEW":
                items = await self.get_views(schema, db)
                items = [dict(item, object_type="VIEW", object_name=item.get("view_name")) for item in items]
            elif object_type == "PROCEDURE":
                items = await self.get_procedures(schema, db)
                items = [dict(item, object_type="PROCEDURE", object_name=item.get("proc_name")) for item in items]
            elif object_type == "SEQUENCE":
                items = await self.get_sequences(schema, db)
                items = [dict(item, object_type="SEQUENCE", object_name=item.get("seq_name")) for item in items]
            elif object_type == "SYNONYM":
                items = await self.get_synonyms(schema, db)
                items = [dict(item, object_type="SYNONYM", object_name=item.get("synonym_name")) for item in items]
            else:
                items = [item for item in await self.get_tables(schema, db) if str(item.get("objtype", "")).upper() == "EXTERNAL TABLE"]
            nodes = []
            for item in items:
                name = item.get("table_name") or item.get("object_name") or item.get("view_name") or item.get("proc_name") or item.get("seq_name") or item.get("synonym_name")
                if not name:
                    continue
                nodes.append({"id": encode_node({"kind": "object", "database": db, "schema": schema, "object_name": name, "object_type": object_type}), "kind": "object", "label": name, "database": db, "schema": schema, "object_name": name, "object_type": object_type, "description": item.get("definition") or item.get("source"), "has_children": object_type in {"TABLE", "VIEW", "EXTERNAL TABLE"}})
            return {"nodes": nodes}
        if kind == "object":
            columns = await self.get_columns(parent["object_name"], parent.get("schema"), db)
            return {"nodes": [{"id": encode_node({"kind": "column", **parent, "column_name": col.get("column_name")}), "kind": "column", "label": col.get("column_name", ""), "column_type": col.get("data_type"), "has_children": False, **col} for col in columns]}
        return {"nodes": []}

    async def schema_cache(self, database: str | None = None) -> dict[str, Any]:
        schemas = await self.get_schemas(database)
        tables: list[dict[str, Any]] = []
        for schema in schemas:
            try:
                for row in await self.get_tables(schema=schema, database=database):
                    tables.append({"schema": row.get("schema", schema), "table_name": row.get("table_name", ""), "objtype": row.get("objtype", "TABLE")})
            except Exception:
                continue
        return {"schemas": schemas, "tables": tables, "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
