"""Contracts for point-in-time MIS, hierarchy, league and data quality."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.schemas.report import ChartData, ColumnSpec, KpiValue


class ComparisonValue(BaseModel):
    code: str
    label: str
    current: float | int | None = None
    reference: float | int | None = None
    delta: float | int | None = None
    delta_pct: float | None = None
    current_period: dict[str, str] = Field(default_factory=dict)
    reference_period: dict[str, str] = Field(default_factory=dict)


class ReportingContext(BaseModel):
    requested_as_of: str
    as_of: str
    data_through: str
    load_id: str
    loaded_at: str
    attribution: str
    complete: bool
    stale: bool = False
    definitions: dict[str, str] = Field(default_factory=dict)


class PerformanceResponse(BaseModel):
    reporting_context: ReportingContext
    kpis: list[KpiValue] = Field(default_factory=list)
    comparisons: dict[str, ComparisonValue] = Field(default_factory=dict)
    columns: list[ColumnSpec] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    charts: list[ChartData] = Field(default_factory=list)


class HierarchyResponse(BaseModel):
    reporting_context: ReportingContext
    regions: list[dict[str, Any]] = Field(default_factory=list)
    changes: list[dict[str, Any]] = Field(default_factory=list)


class LeagueResponse(BaseModel):
    reporting_context: ReportingContext
    level: str
    rules: dict[str, Any] = Field(default_factory=dict)
    columns: list[ColumnSpec] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    current_user_position: int | None = None


class QualityResponse(BaseModel):
    reporting_context: ReportingContext
    overall_status: str
    checks: list[dict[str, Any]] = Field(default_factory=list)
