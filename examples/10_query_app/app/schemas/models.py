"""Pydantic response/request models for the query app."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class QueryResponse(BaseModel):
    query_id: str
    columns: list[dict[str, Any]] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    message: str | None = None
    elapsed_ms: float = 0.0


class CancelResponse(BaseModel):
    status: Literal["cancelling", "no_active_query"]
    query_id: str


class ImportResponse(BaseModel):
    table: str
    total_rows_in_file: int
    imported: int
    errors: list[str] | None = None


class ColumnInfo(BaseModel):
    column_name: str = ""
    data_type: str = "unknown"
    nullable: str = "Y"
    ordinal: int = 0


class TableDetail(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    schema_name: str = Field(alias="schema")
    table_name: str
    object_type: str = "TABLE"
    columns: list[ColumnInfo] = Field(default_factory=list)
    distribution_key: list[str] = Field(default_factory=list)
    size_mb: float | None = None


class SchemaCachePayload(BaseModel):
    schemas: list[str] = Field(default_factory=list)
    tables: list[dict[str, Any]] = Field(default_factory=list)
    fetched_at: str = ""
