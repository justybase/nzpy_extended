from __future__ import annotations

import csv
import os
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .core import Connection

from .exceptions import ProgrammingError
from .utils import infer_columns_from_rows


_UTF8_VARIANTS = frozenset({'UTF8', 'UTF-8', 'UTF_8', 'utf8', 'utf-8', 'utf_8'})


def _resolve_encoding(encoding: str) -> str:
    """Use utf-8-sig when user specifies UTF-8 to auto-strip BOM."""
    return 'utf-8-sig' if encoding in _UTF8_VARIANTS else encoding


def _sample_csv_rows(
    path: str,
    delimiter: str,
    has_header: bool,
    sample_size: int,
    encoding: str = 'UTF8',
) -> tuple[list[tuple[str, ...]], list[str] | None]:
    """Return (data_rows, header) from the CSV file."""
    encoding = _resolve_encoding(encoding)
    rows: list[tuple[str, ...]] = []
    header: list[str] | None = None
    with open(path, newline='', encoding=encoding) as f:
        reader = csv.reader(f, delimiter=delimiter)
        if has_header:
            try:
                header = next(reader)
            except StopIteration:
                raise ProgrammingError("CSV file is empty") from None
        for row in reader:
            rows.append(tuple(row))
            if len(rows) >= sample_size:
                break
    return rows, header


def _csv_rows(path: str, delimiter: str, has_header: bool, encoding: str) -> Iterator[tuple[Any, ...]]:
    """Yield CSV rows lazily (quoting and multi-line fields handled by csv.reader).

    An empty field is NULL, the usual CSV convention; load_data then stores it as NULL
    in every column type, text included.
    """
    encoding = _resolve_encoding(encoding)
    with open(path, newline='', encoding=encoding) as f:
        reader = csv.reader(f, delimiter=delimiter)
        if has_header:
            try:
                next(reader)
            except StopIteration:
                return
        for row in reader:
            yield tuple(None if field == '' else field for field in row)


async def load_csv(
    conn: Connection,
    table_name: str,
    csv_path: str,
    delimiter: str = ',',
    has_header: bool = True,
    sample_size: int = 1000,
    encoding: str = 'UTF8',
    create_if_missing: bool = True,
    temporary: bool = False,
    distribute_on_random: bool = True,
    escape_char: str | None = '\\',
    logdir: str | None = None,
) -> int:
    """Load a CSV file with ``Connection.load_data``.

    Column types are inferred from the first ``sample_size`` data rows. Every row is
    then validated against those types while it is streamed, so a value outside the
    sample reports its row number instead of corrupting the table.

    ``encoding`` is the Python codec of the file. Empty fields become NULL.
    """
    if not os.path.isfile(csv_path):
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    sample, header_names = _sample_csv_rows(csv_path, delimiter, has_header, sample_size, encoding)
    if not sample:
        raise ProgrammingError("No data rows found in CSV file")

    inferred = infer_columns_from_rows(sample)
    columns: list[tuple[str, str]] | None
    if header_names:
        if len(header_names) < len(inferred):
            raise ProgrammingError(
                f"CSV header has {len(header_names)} column name(s) but "
                f"data has {len(inferred)} column(s)"
            )
        columns = [(header_names[i], col_type) for i, (_, col_type) in enumerate(inferred)]
    else:
        columns = inferred
        if await conn._catalog_columns(table_name):
            # Existing table, no header: match the file to the table's column order.
            columns = None

    return await conn.load_data(
        table_name,
        _csv_rows(csv_path, delimiter, has_header, encoding),
        columns=columns,
        delimiter=delimiter,
        encoding=None,
        create_if_missing=create_if_missing,
        temporary=temporary,
        distribute_on_random=distribute_on_random,
        logdir=logdir,
        escape_char=escape_char,
    )


__all__ = [
    "load_csv",
]
