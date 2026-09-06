"""CSV export / import helpers (no HTTP here, easy to unit-test)."""

from __future__ import annotations

import csv
import io
from typing import Any


def rows_to_csv(
    columns: list[dict[str, Any]], rows: list[list[Any]], delimiter: str = ","
) -> io.StringIO:
    output = io.StringIO()
    writer = csv.writer(output, delimiter=delimiter, lineterminator="\n")
    writer.writerow([col.get("ColumnName", "?") for col in columns])
    for row in rows:
        writer.writerow([str(v) if v is not None else "" for v in row])
    output.seek(0)
    return output


def csv_to_rows(data: str, delimiter: str = ",") -> list[list[str | None]]:
    reader = csv.reader(io.StringIO(data), delimiter=delimiter)
    return [
        [cell if cell.strip() != "" else None for cell in row] for row in reader
    ]


def coerce_delimiter(delimiter: str | None) -> str:
    if not delimiter:
        return ","
    return delimiter[0]


def header_columns(header: list[str | None]) -> list[tuple[str, str]]:
    return [((c or f"col{i+1}").replace('"', "").strip() or f"col{i+1}",
             "VARCHAR(32000)") for i, c in enumerate(header)]
