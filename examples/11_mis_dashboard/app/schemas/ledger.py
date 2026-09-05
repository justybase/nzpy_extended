"""Pydantic models for the sales-ledger API (large analytics view)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.schemas.report import ColumnSpec


class LedgerFilters(BaseModel):
    groups: list[str] = Field(default_factory=list)
    channels: list[str] = Field(default_factory=list)
    statuses: list[str] = Field(default_factory=list)


class LedgerResponse(BaseModel):
    columns: list[ColumnSpec] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 50
    pages: int = 1
    filters: LedgerFilters = Field(default_factory=LedgerFilters)
    sort: str = "sale_date"
    dir: str = "desc"