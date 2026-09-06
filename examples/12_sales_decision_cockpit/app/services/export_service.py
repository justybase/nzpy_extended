"""XLSX/XLSB exports for the decision-oriented views."""

from __future__ import annotations

import datetime as dt
import os
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from xlspy import XlsbWriter, XlsxWriter

from app.services.cockpit_service import CockpitService

MEDIA_TYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xlsb": "application/vnd.ms-excel.sheet.binary.macroEnabled.12",
}


@dataclass
class ExportResult:
    path: str
    filename: str
    media_type: str


class ExportService:
    def __init__(self, service: CockpitService) -> None:
        self._service = service

    async def export_cockpit(self, fmt: str, **params: Any) -> ExportResult:
        self._validate_format(fmt)
        payload = await self._service.cockpit(**params)
        columns = [
            {"key": "code", "label": "Code"},
            {"key": "entity", "label": "Entity"},
            {"key": "parent", "label": "Parent"},
            {"key": "severity", "label": "Status"},
            {"key": "actual", "label": "Actual", "fmt": "eur"},
            {"key": "target", "label": "Target", "fmt": "eur"},
            {"key": "gap", "label": "Gap", "fmt": "eur"},
            {"key": "gap_pct", "label": "Gap %", "fmt": "pct"},
            {"key": "forecast_attainment", "label": "4-week pace", "fmt": "pct"},
            {"key": "pipeline_coverage", "label": "Pipeline cover", "fmt": "pct"},
            {"key": "conversion_rate", "label": "Conversion", "fmt": "pct"},
            {"key": "issue", "label": "Issue"},
            {"key": "action", "label": "Recommended action"},
        ]
        rows = [[row.get(column["key"]) for column in columns] for row in payload["exceptions"]]
        meta = self._meta("Control tower", payload["week"], payload["subtitle"], len(rows), payload["freshness"])
        path = self.write_workbook(fmt, "Control tower", columns, rows, meta)
        return ExportResult(path, f"control_tower_{payload['week']}.{fmt}", MEDIA_TYPES[fmt])

    async def export_drivers(self, fmt: str, **params: Any) -> ExportResult:
        self._validate_format(fmt)
        payload = await self._service.drivers(**params)
        columns = [
            {"key": "code", "label": "Code"},
            {"key": "label", "label": "Driver"},
            {"key": "actual", "label": "Actual"},
            {"key": "target", "label": "Target"},
            {"key": "contribution", "label": "Contribution"},
            {"key": "contribution_pct", "label": "Contribution %"},
            {"key": "leads", "label": "Leads"},
            {"key": "wins", "label": "Wins"},
            {"key": "conversion_rate", "label": "Conversion"},
            {"key": "weighted_pipeline", "label": "Weighted pipeline"},
            {"key": "cancellation_rate", "label": "Cancellation"},
        ]
        rows = [[row.get(column["key"]) for column in columns] for row in payload["drivers"]]
        meta = self._meta("Driver analysis", payload["week"], payload["title"], len(rows), payload["freshness"])
        path = self.write_workbook(fmt, "Driver analysis", columns, rows, meta)
        return ExportResult(path, f"drivers_{payload['week']}_{payload['dimension']}.{fmt}", MEDIA_TYPES[fmt])

    @staticmethod
    def _validate_format(fmt: str) -> None:
        if fmt not in MEDIA_TYPES:
            raise ValueError("format must be xlsx or xlsb")

    @staticmethod
    def _meta(title: str, week: str, subtitle: str, rows: int, freshness: dict[str, Any]) -> list[list[Any]]:
        return [
            ["Report", title],
            ["Review week", week],
            ["Context", subtitle],
            ["Rows", rows],
            ["Load ID", freshness.get("load_id")],
            ["Source watermark", freshness.get("source_watermark")],
            ["Reconciled", freshness.get("reconciled")],
            ["Generated", dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")],
            ["Method", "Exception-first sales operating review"],
        ]

    def write_workbook(
        self,
        fmt: str,
        sheet_name: str,
        columns: list[dict[str, Any]],
        rows: list[list[Any]],
        meta: list[list[Any]],
    ) -> str:
        fd, path = tempfile.mkstemp(suffix=f".{fmt}")
        os.close(fd)
        writer_cls = XlsbWriter if fmt == "xlsb" else XlsxWriter
        with writer_cls(path) as writer:
            writer.add_sheet(sheet_name)
            writer.write_sheet(self._with_header(columns, rows))
            writer.add_sheet("Report info", hidden=True)
            writer.write_sheet(meta)
        return path

    @staticmethod
    def _with_header(columns: list[dict[str, Any]], rows: list[list[Any]]) -> Iterator[list[Any]]:
        yield [column["label"] for column in columns]
        yield from rows
