"""Completion contract tests for qualified Netezza object names."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.language_service import LanguageService  # noqa: E402


class Catalog:
    async def get_databases(self, database: str | None = None) -> list[str]:
        return ["JUST_DATA"]

    async def get_schemas(self, database: str | None = None) -> list[str]:
        return ["ADMIN"]

    async def get_tables(self, schema: str | None = None, database: str | None = None) -> list[dict[str, str]]:
        return [{"schema": "ADMIN", "table_name": "DIMDATE", "objtype": "TABLE"}]

    async def get_views(self, schema: str | None = None, database: str | None = None) -> list[dict[str, str]]:
        return []

    async def get_columns(self, table: str, schema: str | None = None, database: str | None = None) -> list[dict[str, str]]:
        return []


class ColumnCatalog(Catalog):
    async def get_columns(self, table: str, schema: str | None = None, database: str | None = None) -> list[dict[str, str]]:
        assert (table, schema, database) == ("DIMDATE", None, "JUST_DATA")
        return [{"column_name": "DATE_ID", "data_type": "INTEGER"}]


def test_database_double_dot_inserts_only_object_name() -> None:
    async def run() -> None:
        sql = "SELECT * FROM JUST_DATA.."
        items = (await LanguageService(Catalog()).completion(sql, len(sql), database="JUST_DATA"))["items"]
        dimdate = next(item for item in items if item["label"] == "DIMDATE")
        assert dimdate["insertText"] == "DIMDATE"
        assert dimdate["detail"] == "ADMIN · TABLE"

    asyncio.run(run())


def test_alias_completion_resolves_database_without_schema() -> None:
    async def run() -> None:
        sql = "SELECT * FROM JUST_DATA..DIMDATE D WHERE D."
        items = (await LanguageService(ColumnCatalog()).completion(sql, len(sql), database="JUST_DATA"))["items"]
        assert any(item["label"] == "DATE_ID" for item in items)

    asyncio.run(run())
