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
    delta: Any = None
    delta_fmt: str | None = None
    delta_label: str | None = None
    delta_pct: float | None = None
    target: Any = None
    target_fmt: str | None = None
    status: str = "neutral"


class Insight(BaseModel):
    severity: str = "neutral"
    title: str
    detail: str
    entity: str | None = None
    action: str | None = None


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
    comparison: PeriodInfo | None = None
    kpis: list[KpiValue] = Field(default_factory=list)
    insights: list[Insight] = Field(default_factory=list)
    freshness: dict[str, Any] = Field(default_factory=dict)
    reporting_context: dict[str, Any] = Field(default_factory=dict)
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
    reporting_context: dict[str, Any] = Field(default_factory=dict)
    columns: list[ColumnSpec] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)


class CacheTableInfo(BaseModel):
    rows: int
    loaded_at: str


class CacheInfo(BaseModel):
    ttl_seconds: int
    cache_mode: str = "ttl"
    last_error: str | None = None
    hits: int = 0
    misses: int = 0
    tables: dict[str, CacheTableInfo] = Field(default_factory=dict)
    report_cache_size: int = 0
    report_cache_ttl: int = 0
    dataset_name: str | None = None
    dataset_version: int | None = None
    load_id: str | None = None
    source_watermark: str | None = None
    published_at: str | None = None
    refreshed_version: int | None = None
    control_checked_at: str | None = None
    control_check_ok: bool = False
    control_error: str | None = None
    cache_age_seconds: int | None = None
    unconfirmed_age_seconds: int | None = None
    max_unconfirmed_seconds: int = 86_400
    stale: bool = False
    last_refresh_at: str | None = None
    last_refresh_reason: str | None = None
    refresh_error: str | None = None


class ReportMeta(BaseModel):
    id: str
    title: str


class MetaResponse(BaseModel):
    months: list[str] = Field(default_factory=list)
    snapshot_dates: list[str] = Field(default_factory=list)
    latest_snapshot: str | None = None
    cache: CacheInfo
    reports: list[ReportMeta] = Field(default_factory=list)


class StatusResponse(BaseModel):
    database: str
    host: str
    pool: dict[str, Any]
    cache: CacheInfo
