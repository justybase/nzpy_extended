"""Public response models for the decision cockpit."""

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


class Series(BaseModel):
    name: str
    data: list[Any] = Field(default_factory=list)


class ChartData(BaseModel):
    id: str
    type: str
    title: str
    labels: list[str] = Field(default_factory=list)
    series: list[Series] = Field(default_factory=list)


class Headline(BaseModel):
    key: str
    label: str
    value: Any = None
    fmt: str = "str"
    detail: str | None = None
    status: str = "neutral"


class ExceptionRow(BaseModel):
    code: str
    entity: str
    parent: str
    actual: float
    target: float
    gap: float
    gap_pct: float
    attainment: float
    forecast_attainment: float
    pipeline_coverage: float | None = None
    conversion_rate: float | None = None
    cancellation_rate: float | None = None
    severity: str
    issue: str
    action: str


class DriverRow(BaseModel):
    code: str
    label: str
    actual: float
    target: float
    contribution: float
    contribution_pct: float
    leads: int
    wins: int
    conversion_rate: float | None = None
    weighted_pipeline: float
    cancellation_rate: float | None = None


class MetaResponse(BaseModel):
    weeks: list[str] = Field(default_factory=list)
    latest_week: str | None = None
    regions: list[dict[str, str]] = Field(default_factory=list)
    units: list[dict[str, str]] = Field(default_factory=list)
    metrics: list[dict[str, str]] = Field(default_factory=list)
    dimensions: list[dict[str, str]] = Field(default_factory=list)
    cache: dict[str, Any] = Field(default_factory=dict)


class CockpitResponse(BaseModel):
    week: str
    lookback: int
    scope: str
    region: str | None = None
    unit: str | None = None
    metric: str
    subtitle: str
    headlines: list[Headline] = Field(default_factory=list)
    status_counts: dict[str, int] = Field(default_factory=dict)
    exceptions: list[ExceptionRow] = Field(default_factory=list)
    narrative: list[dict[str, str]] = Field(default_factory=list)
    charts: list[ChartData] = Field(default_factory=list)
    freshness: dict[str, Any] = Field(default_factory=dict)


class DriversResponse(BaseModel):
    week: str
    lookback: int
    scope: str
    region: str | None = None
    unit: str | None = None
    metric: str
    dimension: str
    title: str
    total_actual: float
    total_target: float
    total_gap: float
    bridge: list[dict[str, Any]] = Field(default_factory=list)
    drivers: list[DriverRow] = Field(default_factory=list)
    charts: list[ChartData] = Field(default_factory=list)
    freshness: dict[str, Any] = Field(default_factory=dict)


class ReviewPackResponse(BaseModel):
    week: str
    title: str
    subtitle: str
    decision: dict[str, str]
    evidence: list[dict[str, str]] = Field(default_factory=list)
    recommended_actions: list[dict[str, str]] = Field(default_factory=list)
    exceptions: list[ExceptionRow] = Field(default_factory=list)
    freshness: dict[str, Any] = Field(default_factory=dict)
