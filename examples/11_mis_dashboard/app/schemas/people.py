"""Pydantic models for the people API (advisor panels, personal views)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.schemas.report import ChartData, ColumnSpec, KpiValue


class AdvisorListEntry(BaseModel):
    code: str
    first_name: str | None = None
    last_name: str | None = None
    name: str
    role: str | None = None
    status: str | None = None
    branch_code: str = ""
    branch_name: str = ""
    city: str | None = None
    region_name: str | None = None


class AdvisorListResponse(BaseModel):
    advisors: list[AdvisorListEntry] = Field(default_factory=list)


class BranchListEntry(BaseModel):
    code: str
    name: str
    city: str | None = None
    region_name: str | None = None


class BranchListResponse(BaseModel):
    branches: list[BranchListEntry] = Field(default_factory=list)


class AdvisorPanelResponse(BaseModel):
    code: str
    info: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any] = Field(default_factory=dict)
    reporting_context: dict[str, Any] = Field(default_factory=dict)
    columns: list[ColumnSpec] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    charts: list[ChartData] = Field(default_factory=list)


class CumulativeResponse(BaseModel):
    scope: str
    month: str
    info: dict[str, Any] = Field(default_factory=dict)
    kpis: list[KpiValue] = Field(default_factory=list)
    columns: list[ColumnSpec] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    charts: list[ChartData] = Field(default_factory=list)
    reporting_context: dict[str, Any] = Field(default_factory=dict)
