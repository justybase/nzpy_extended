"""Pydantic models for API responses (report payloads, meta, status)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ColumnSpec(BaseModel):
    key: str
    label: str
    fmt: str = "str"


class TableData(BaseModel):
    columns: list[ColumnSpec] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)


class ChartSeries(BaseModel):
    name: str
    data: list[Any] = Field(default_factory=list)


class ChartData(BaseModel):
    id: str
    type: str
    title: str
    labels: list[str] = Field(default_factory=list)
    series: list[ChartSeries] = Field(default_factory=list)


class KpiValue(BaseModel):
    key: str
    label: str
    value: Any = None
    fmt: str = "str"


class DimSpec(BaseModel):
    id: str
    label: str


class PeriodInfo(BaseModel):
    from_: str = Field(alias="from")
    to: str


class ReportResponse(BaseModel):
    id: str
    title: str
    subtitle: str
    period: PeriodInfo
    kpis: list[KpiValue] = Field(default_factory=list)
    synthetic: TableData
    dims: list[DimSpec] = Field(default_factory=list)
    dim: str | None = None
    analytic: TableData | None = None
    charts: list[ChartData] = Field(default_factory=list)


class DrillResponse(BaseModel):
    report_id: str
    target: str
    key: str
    period: PeriodInfo
    title: str
    columns: list[ColumnSpec] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)


class CacheTableInfo(BaseModel):
    rows: int
    loaded_at: str


class CacheInfo(BaseModel):
    ttl_seconds: int
    last_error: str | None = None
    hits: int = 0
    misses: int = 0
    tables: dict[str, CacheTableInfo] = Field(default_factory=dict)
    report_cache_size: int = 0
    report_cache_ttl: int = 0


class ReportMeta(BaseModel):
    id: str
    title: str


class MetaResponse(BaseModel):
    months: list[str] = Field(default_factory=list)
    cache: CacheInfo
    reports: list[ReportMeta] = Field(default_factory=list)


class StatusResponse(BaseModel):
    database: str
    host: str
    pool: dict[str, Any]
    cache: CacheInfo