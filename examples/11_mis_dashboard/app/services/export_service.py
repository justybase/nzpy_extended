"""
ExportService — XLSX / XLSB generation with xlspy.

Table payloads come from ReportService (i.e. from the cache), so exports
never query Netezza either. Every workbook gets a hidden "Report info" sheet
with generation metadata.
"""

from __future__ import annotations

import datetime as dt
import os
import tempfile
from dataclasses import dataclass
from typing import Any

from xlspy import XlsbWriter, XlsxWriter

from app.core.roles import SessionUser
from app.services.report_service import ReportService

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
    def __init__(self, report_service: ReportService) -> None:
        self._report_service = report_service

    # -- report tables ------------------------------------------------------

    async def _report_table(self, report_id: str, kind: str, from_: str | None,
                            to: str | None, dim: str | None,
                            user: SessionUser | None = None) -> dict[str, Any]:
        """Payload table prepared for Excel (internal 'ym' key column dropped)."""
        payload = await self._report_service.build(report_id, from_, to, dim, user)
        table_def = payload.get(kind)
        # 'None' or empty columns mark a table that is not part of this report
        # (e.g. overview has no analytic); a real table keeps its columns even
        # when the period contains no data.
        if table_def is None or not table_def["columns"]:
            raise ValueError(
                f"Report '{report_id}' has no {kind} table"
                f" (dimensions: {payload.get('dims') or '-'})"
            )
        columns = table_def["columns"]
        rows = table_def["rows"]
        if columns and columns[0].get("key") == "ym":
            columns = columns[1:]
            rows = [row[1:] for row in rows]
        return {
            "title": payload["title"],
            "kind": "Synthetic" if kind == "synthetic" else "Analytic",
            "dim": payload.get("dim"),
            "columns": columns,
            "rows": rows,
            "period": payload["period"],
        }

    async def export_report(self, report_id: str, kind: str, fmt: str,
                            from_: str | None, to: str | None,
                            dim: str | None,
                            user: SessionUser | None = None) -> ExportResult:
        if kind not in ("synthetic", "analytic"):
            raise ValueError("kind must be 'synthetic' or 'analytic'")
        if fmt not in MEDIA_TYPES:
            raise ValueError("fmt must be 'xlsx' or 'xlsb'")

        export = await self._report_table(report_id, kind, from_, to, dim, user)
        period = export["period"]
        sheet_name = self._safe_sheet_name(f"{export['kind']} - {export['title']}")
        if export["dim"] and kind == "analytic":
            sheet_name += f" by {self._safe_sheet_name(export['dim'])}"
        sheet_name = sheet_name[:31]

        meta_lines: list[list[Any]] = [
            ["Report", report_id],
            ["Title", export["title"]],
            ["Kind", export["kind"]],
            ["Dimension", export["dim"] or "-"],
            ["Period from", period["from"]],
            ["Period to", period["to"]],
            ["Rows", len(export["rows"])],
            ["Generated", dt.datetime.now().isoformat(timespec="seconds")],
            ["Data source", "cached MIS_* tables (TTLCache)"],
        ]
        path = self.write_workbook(fmt, sheet_name, export["columns"],
                                   export["rows"], meta_lines)
        filename = f"{report_id}_{kind}_{period['from']}_{period['to']}.{fmt}"
        if export["dim"]:
            filename = f"{report_id}_{kind}_{export['dim']}_{period['from']}_{period['to']}.{fmt}"
        return ExportResult(path=path, filename=filename, media_type=MEDIA_TYPES[fmt])

    # -- drill tables -------------------------------------------------------

    async def export_drill(self, report_id: str, target: str, key: str, fmt: str,
                           from_: str | None, to: str | None,
                           user: SessionUser | None = None) -> ExportResult:
        if fmt not in MEDIA_TYPES:
            raise ValueError("fmt must be 'xlsx' or 'xlsb'")
        payload = await self._report_service.drill(report_id, target, key,
                                                   from_, to, user)
        period = payload["period"]
        sheet_name = self._safe_sheet_name(f"Drill - {payload['title']}")[:31]
        meta_lines: list[list[Any]] = [
            ["Report", report_id],
            ["Drill target", target],
            ["Key", key],
            ["Period from", period["from"]],
            ["Period to", period["to"]],
            ["Rows", len(payload["rows"])],
            ["Generated", dt.datetime.now().isoformat(timespec="seconds")],
        ]
        path = self.write_workbook(fmt, sheet_name, payload["columns"],
                                   payload["rows"], meta_lines)
        filename = f"drill_{report_id}_{target}_{key}_{period['from']}_{period['to']}.{fmt}"
        return ExportResult(path=path, filename=filename, media_type=MEDIA_TYPES[fmt])

    # -- low-level ----------------------------------------------------------

    def write_workbook(self, fmt: str, sheet_name: str, columns: list[dict[str, str]],
                       rows: list[list[Any]], meta_lines: list[list[Any]]) -> str:
        fd, path = tempfile.mkstemp(suffix=f".{fmt}")
        os.close(fd)
        writer_cls = XlsbWriter if fmt == "xlsb" else XlsxWriter
        with writer_cls(path) as writer:
            writer.add_sheet(sheet_name)
            writer.write_sheet(self._rows_with_headers(columns, rows))
            writer.add_sheet("Report info", hidden=True)
            writer.write_sheet(meta_lines)
        return path

    @staticmethod
    def _rows_with_headers(columns: list[dict[str, str]], rows: list[list[Any]]) -> Any:
        yield [c["label"] for c in columns]
        for row in rows:
            yield row

    @staticmethod
    def _safe_sheet_name(name: str) -> str:
        """Sheet names must be valid Excel identifiers and well-formed XML.

        xlspy escapes sheet names in the <sheets> block but not in the
        _FilterDatabase definedName, so characters like '&' would produce an
        invalid workbook. Excel also forbids: \\ / ? * [ ] :
        """
        for bad, good in (("&", "and"), ("<", "-"), (">", "-"),
                          ('"', "'"), (":", "-"), ("*", "-"), ("?", "-"),
                          ("/", "-"), ("\\", "-"), ("[", "("), ("]", ")")):
            name = name.replace(bad, good)
        return name.strip() or "Sheet"