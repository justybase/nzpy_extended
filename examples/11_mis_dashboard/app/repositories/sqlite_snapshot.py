"""Durable SQLite snapshots for the process-local MIS cache.

The application still serves eager tables from RAM.  This module only adds a
durable, generation-aware copy that can be restored after a process restart.
SQLite work is limited to startup, explicit refreshes and small lazy-slice
operations; normal report requests read the RAM cache.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import re
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from app.core.cache_types import DatasetVersion

TableData = tuple[list[str], list[list[Any]]]

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_FORMAT_VERSION = 1
_LAZY_TABLE = "MIS_FACT_PERFORMANCE_SNAPSHOT"
_LAZY_COLUMN = "snapshot_date"


def _quote_identifier(identifier: str) -> str:
    if _IDENTIFIER.fullmatch(identifier) is None:
        raise ValueError(f"Invalid SQLite identifier: {identifier}")
    return f'"{identifier}"'


def _physical_table_name(name: str) -> str:
    return f"data_{name}"


def _filter_value(value: Any) -> str:
    return str(value)[:10]


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _declared_type(rows: list[list[Any]], index: int) -> str:
    for row in rows:
        value = row[index]
        if value is None:
            continue
        if isinstance(value, bool) or isinstance(value, int):
            return "INTEGER"
        if isinstance(value, float):
            return "REAL"
        if isinstance(value, bytes):
            return "BLOB"
        return "TEXT"
    return "TEXT"


def _validate_table_data(name: str, columns: list[str], rows: list[list[Any]]) -> None:
    _quote_identifier(name)
    if not columns:
        raise ValueError(f"SQLite snapshot table has no columns: {name}")
    if len(set(columns)) != len(columns):
        raise ValueError(f"SQLite snapshot has duplicate columns: {name}")
    for column in columns:
        _quote_identifier(column)
    expected = len(columns)
    if any(len(row) != expected for row in rows):
        raise ValueError(f"SQLite snapshot row width mismatch: {name}")


def _create_data_table(
    conn: sqlite3.Connection,
    name: str,
    columns: list[str],
    rows: list[list[Any]],
) -> None:
    physical = _physical_table_name(name)
    definitions = ", ".join(
        f"{_quote_identifier(column)} {_declared_type(rows, index)}"
        for index, column in enumerate(columns)
    )
    conn.execute(f"CREATE TABLE {_quote_identifier(physical)} ({definitions})")
    if rows:
        placeholders = ", ".join("?" for _ in columns)
        conn.executemany(
            f"INSERT INTO {_quote_identifier(physical)} VALUES ({placeholders})",
            rows,
        )


class SQLiteSnapshotStore:
    """Read, stage and atomically activate one local SQLite snapshot file."""

    def __init__(self, path: Path | None, max_lazy_slices: int = 2048) -> None:
        self._path = path.expanduser() if path is not None else None
        self._max_lazy_slices = max_lazy_slices

    @property
    def enabled(self) -> bool:
        return self._path is not None

    @property
    def path(self) -> Path | None:
        return self._path

    async def restore(self, table_names: list[str]) -> dict[str, Any]:
        if self._path is None:
            return {"restored": False, "reason": "disabled"}
        return await asyncio.to_thread(self._restore_sync, list(table_names))

    async def stage_generation(
        self,
        tables: dict[str, TableData],
        table_names: list[str],
        generation: DatasetVersion,
    ) -> Path:
        if self._path is None:
            raise RuntimeError("SQLite snapshot store is disabled")
        return await asyncio.to_thread(
            self._stage_generation_sync,
            tables,
            list(table_names),
            generation,
        )

    async def activate(self, staged_path: Path) -> None:
        if self._path is None:
            raise RuntimeError("SQLite snapshot store is disabled")
        await asyncio.to_thread(self._activate_sync, staged_path)

    async def discard(self, staged_path: Path | None) -> None:
        if staged_path is None:
            return
        await asyncio.to_thread(staged_path.unlink, missing_ok=True)

    async def read_lazy_slice(
        self,
        generation_id: str,
        table_name: str,
        column: str,
        value: Any,
    ) -> TableData | None:
        if self._path is None:
            return None
        return await asyncio.to_thread(
            self._read_lazy_slice_sync,
            generation_id,
            table_name,
            column,
            _filter_value(value),
        )

    async def write_lazy_slice(
        self,
        generation_id: str,
        table_name: str,
        column: str,
        value: Any,
        columns: list[str],
        rows: list[list[Any]],
    ) -> bool:
        if self._path is None:
            return False
        return await asyncio.to_thread(
            self._write_lazy_slice_sync,
            generation_id,
            table_name,
            column,
            _filter_value(value),
            columns,
            rows,
        )

    def _connect(self, *, read_only: bool = False) -> sqlite3.Connection:
        if self._path is None:
            raise RuntimeError("SQLite snapshot store is disabled")
        if read_only:
            uri = f"file:{self._path.resolve()}?mode=ro"
            return sqlite3.connect(uri, uri=True)
        return sqlite3.connect(str(self._path))

    def _restore_sync(self, table_names: list[str]) -> dict[str, Any]:
        assert self._path is not None
        if not self._path.is_file():
            return {"restored": False, "reason": "missing"}

        conn: sqlite3.Connection | None = None
        try:
            conn = self._connect(read_only=True)
            integrity = conn.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or integrity[0] != "ok":
                raise ValueError(f"SQLite integrity check failed: {integrity}")

            metadata = conn.execute(
                "SELECT dataset_name, version_no, load_id, source_watermark, "
                "published_at, status, generation_id, committed_at, "
                "format_version, source_row_count, source_checksum "
                "FROM cache_metadata LIMIT 1"
            ).fetchone()
            if metadata is None:
                raise ValueError("SQLite snapshot metadata is missing")
            if int(metadata[8]) != _FORMAT_VERSION:
                raise ValueError(f"Unsupported SQLite snapshot format: {metadata[8]}")

            table_meta_rows = conn.execute(
                "SELECT table_name, row_count, columns_json, loaded_at "
                "FROM cache_table_meta"
            ).fetchall()
            table_meta = {str(row[0]): row for row in table_meta_rows}
            missing = sorted(set(table_names) - set(table_meta))
            if missing:
                raise ValueError(f"SQLite snapshot is missing tables: {', '.join(missing)}")

            tables: dict[str, TableData] = {}
            metadata_by_table: dict[str, dict[str, Any]] = {}
            for name in table_names:
                row = table_meta[name]
                columns = [str(column) for column in json.loads(str(row[2]))]
                physical = _physical_table_name(name)
                _validate_table_data(name, columns, [])
                actual = conn.execute(
                    f"SELECT * FROM {_quote_identifier(physical)} LIMIT 0"
                ).description
                actual_columns = [str(description[0]).lower() for description in actual or ()]
                if actual_columns != columns:
                    raise ValueError(f"SQLite snapshot columns changed: {name}")
                cursor = conn.execute(f"SELECT * FROM {_quote_identifier(physical)}")
                rows = [list(values) for values in cursor.fetchall()]
                if len(rows) != int(row[1]):
                    raise ValueError(f"SQLite snapshot row count changed: {name}")
                tables[name] = (columns, rows)
                metadata_by_table[name] = {
                    "rows": len(rows),
                    "loaded_at": str(row[3]),
                    "storage": "ram+sqlite",
                }

            generation: DatasetVersion | None = None
            if metadata[1] is not None and metadata[2] is not None:
                generation = DatasetVersion(
                    dataset_name=str(metadata[0]),
                    version_no=int(metadata[1]),
                    load_id=str(metadata[2]),
                    source_watermark=(
                        str(metadata[3]) if metadata[3] is not None else None
                    ),
                    published_at=str(metadata[4]),
                    status=str(metadata[5]),
                    row_count=(int(metadata[9]) if metadata[9] is not None else None),
                    checksum=(str(metadata[10]) if metadata[10] is not None else None),
                )
            return {
                "restored": True,
                "generation": generation,
                "generation_id": str(metadata[6]),
                "committed_at": str(metadata[7]),
                "tables": tables,
                "metadata": metadata_by_table,
            }
        except (OSError, sqlite3.Error, TypeError, ValueError, json.JSONDecodeError) as exc:
            return {"restored": False, "reason": str(exc), "error": str(exc)}
        finally:
            if conn is not None:
                conn.close()

    def _stage_generation_sync(
        self,
        tables: dict[str, TableData],
        table_names: list[str],
        generation: DatasetVersion,
    ) -> Path:
        assert self._path is not None
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_name(
            f".{self._path.name}.{uuid.uuid4().hex}.tmp"
        )
        conn: sqlite3.Connection | None = None
        try:
            if set(tables) != set(table_names):
                raise ValueError("SQLite snapshot table set is incomplete")
            # SQLite creates the database before we can chmod it.  Create the
            # staging file with private permissions first so a refresh or a
            # crashed process never exposes the unmasked cache to other users.
            fd = os.open(
                str(temporary),
                os.O_CREAT | os.O_EXCL | os.O_RDWR,
                0o600,
            )
            os.close(fd)
            conn = sqlite3.connect(str(temporary))
            conn.execute("PRAGMA journal_mode=DELETE")
            conn.execute("PRAGMA synchronous=FULL")
            conn.executescript(
                """
                CREATE TABLE cache_metadata (
                    dataset_name TEXT NOT NULL,
                    version_no INTEGER NOT NULL,
                    load_id TEXT NOT NULL,
                    source_watermark TEXT,
                    published_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    generation_id TEXT NOT NULL,
                    committed_at TEXT NOT NULL,
                    format_version INTEGER NOT NULL,
                    source_row_count INTEGER,
                    source_checksum TEXT
                );
                CREATE TABLE cache_table_meta (
                    table_name TEXT PRIMARY KEY,
                    row_count INTEGER NOT NULL,
                    columns_json TEXT NOT NULL,
                    loaded_at TEXT NOT NULL
                );
                CREATE TABLE cache_lazy_slices (
                    generation_id TEXT NOT NULL,
                    table_name TEXT NOT NULL,
                    filter_column TEXT NOT NULL,
                    filter_value TEXT NOT NULL,
                    columns_json TEXT NOT NULL,
                    row_count INTEGER NOT NULL,
                    loaded_at TEXT NOT NULL,
                    PRIMARY KEY (
                        generation_id, table_name, filter_column, filter_value
                    )
                );
                """
            )
            loaded_at = _now()
            conn.execute(
                "INSERT INTO cache_metadata VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    generation.dataset_name,
                    generation.version_no,
                    generation.load_id,
                    generation.source_watermark,
                    generation.published_at,
                    generation.status,
                    generation.generation_id,
                    loaded_at,
                    _FORMAT_VERSION,
                    generation.row_count,
                    generation.checksum,
                ),
            )
            for name in table_names:
                columns, rows = tables[name]
                _validate_table_data(name, columns, rows)
                _create_data_table(conn, name, columns, rows)
                conn.execute(
                    "INSERT INTO cache_table_meta VALUES (?, ?, ?, ?)",
                    (name, len(rows), json.dumps(columns), loaded_at),
                )
            conn.commit()
            integrity = conn.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or integrity[0] != "ok":
                raise ValueError(f"SQLite integrity check failed: {integrity}")
            conn.close()
            conn = None
            return temporary
        except Exception:
            if conn is not None:
                conn.close()
            temporary.unlink(missing_ok=True)
            raise

    def _activate_sync(self, staged_path: Path) -> None:
        assert self._path is not None
        if not staged_path.is_file():
            raise FileNotFoundError(staged_path)
        os.replace(staged_path, self._path)
        try:
            os.chmod(self._path, 0o600)
        except OSError:
            # Windows and restricted filesystems may not support chmod.
            pass

    def _read_lazy_slice_sync(
        self,
        generation_id: str,
        table_name: str,
        column: str,
        value: str,
    ) -> TableData | None:
        if table_name != _LAZY_TABLE or column != _LAZY_COLUMN:
            return None
        assert self._path is not None
        if not self._path.is_file():
            return None
        conn: sqlite3.Connection | None = None
        try:
            conn = self._connect(read_only=True)
            marker = conn.execute(
                "SELECT columns_json, row_count FROM cache_lazy_slices "
                "WHERE generation_id = ? AND table_name = ? "
                "AND filter_column = ? AND filter_value = ?",
                (generation_id, table_name, column, value),
            ).fetchone()
            if marker is None:
                return None
            columns = [str(item) for item in json.loads(str(marker[0]))]
            if int(marker[1]) == 0:
                return columns, []
            physical = _physical_table_name(table_name)
            cursor = conn.execute(
                f"SELECT * FROM {_quote_identifier(physical)} "
                f"WHERE {_quote_identifier(column)} = ?",
                (value,),
            )
            rows = [list(row) for row in cursor.fetchall()]
            return columns, rows
        except (OSError, sqlite3.Error, TypeError, ValueError, json.JSONDecodeError):
            return None
        finally:
            if conn is not None:
                conn.close()

    def _write_lazy_slice_sync(
        self,
        generation_id: str,
        table_name: str,
        column: str,
        value: str,
        columns: list[str],
        rows: list[list[Any]],
    ) -> bool:
        if table_name != _LAZY_TABLE or column != _LAZY_COLUMN:
            return False
        assert self._path is not None
        if not self._path.is_file():
            return False
        _validate_table_data(table_name, columns, rows)
        conn: sqlite3.Connection | None = None
        try:
            conn = self._connect()
            metadata = conn.execute(
                "SELECT generation_id FROM cache_metadata LIMIT 1"
            ).fetchone()
            if metadata is None or str(metadata[0]) != generation_id:
                return False
            existing = conn.execute(
                "SELECT 1 FROM cache_lazy_slices WHERE generation_id = ? "
                "AND table_name = ? AND filter_column = ? AND filter_value = ?",
                (generation_id, table_name, column, value),
            ).fetchone()
            if existing is not None:
                return True
            if rows:
                physical = _physical_table_name(table_name)
                table_exists = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                    (physical,),
                ).fetchone()
                if table_exists is None:
                    _create_data_table(conn, table_name, columns, rows)
                    conn.execute(
                        "CREATE INDEX idx_snapshot_date "
                        f"ON {_quote_identifier(physical)} ({_quote_identifier(column)})"
                    )
                else:
                    actual = conn.execute(
                        f"SELECT * FROM {_quote_identifier(physical)} LIMIT 0"
                    ).description
                    actual_columns = [str(item[0]).lower() for item in actual or ()]
                    if actual_columns != columns:
                        raise ValueError(f"SQLite lazy columns changed: {table_name}")
                    placeholders = ", ".join("?" for _ in columns)
                    conn.executemany(
                        f"INSERT INTO {_quote_identifier(physical)} VALUES ({placeholders})",
                        rows,
                    )
            conn.execute(
                "INSERT INTO cache_lazy_slices VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    generation_id,
                    table_name,
                    column,
                    value,
                    json.dumps(columns),
                    len(rows),
                    _now(),
                ),
            )
            evicted = conn.execute(
                "SELECT rowid, table_name, filter_column, filter_value "
                "FROM cache_lazy_slices WHERE generation_id = ? "
                "ORDER BY loaded_at DESC, rowid DESC "
                "LIMIT -1 OFFSET ?",
                (generation_id, self._max_lazy_slices),
            ).fetchall()
            for _rowid, evicted_table, evicted_column, evicted_value in evicted:
                physical = _physical_table_name(str(evicted_table))
                table_exists = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                    (physical,),
                ).fetchone()
                if table_exists is not None:
                    conn.execute(
                        f"DELETE FROM {_quote_identifier(physical)} "
                        f"WHERE {_quote_identifier(str(evicted_column))} = ?",
                        (str(evicted_value),),
                    )
            if evicted:
                conn.executemany(
                    "DELETE FROM cache_lazy_slices WHERE rowid = ?",
                    [(row[0],) for row in evicted],
                )
            conn.commit()
            return True
        except (OSError, sqlite3.Error, TypeError, ValueError, json.JSONDecodeError):
            if conn is not None:
                conn.rollback()
            raise
        finally:
            if conn is not None:
                conn.close()
