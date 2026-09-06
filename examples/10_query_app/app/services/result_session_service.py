"""Disk-backed result sessions with bounded page reads."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import Any
from uuid import uuid4

_SESSION_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def _numeric(type_name: str | None) -> bool:
    return any(
        token in (type_name or "").upper()
        for token in ("INT", "DECIMAL", "NUMERIC", "NUMBER", "REAL", "FLOAT", "DOUBLE")
    )


class ResultSessionManager:
    def __init__(self, root: str | Path, ttl_seconds: int = 3600, default_page_size: int = 200) -> None:
        self.root = Path(root) / "query-sessions"
        self.root.mkdir(parents=True, exist_ok=True)
        self.ttl = max(60, ttl_seconds)
        self.default_page_size = max(1, default_page_size)
        self._connections: dict[str, sqlite3.Connection] = {}
        self._locks: dict[str, threading.RLock] = {}
        self.cleanup()

    def _path(self, session_id: str) -> Path:
        if not _SESSION_ID_RE.fullmatch(session_id):
            raise KeyError("Result session not found")
        return self.root / f"{session_id}.sqlite"

    def _manifest_path(self, session_id: str) -> Path:
        if not _SESSION_ID_RE.fullmatch(session_id):
            raise KeyError("Result session not found")
        return self.root / f"{session_id}.json"

    def _conn(self, session_id: str) -> sqlite3.Connection:
        conn = self._connections.get(session_id)
        if conn is None:
            conn = sqlite3.connect(self._path(session_id), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            self._connections[session_id] = conn
            self._locks.setdefault(session_id, threading.RLock())
        return conn

    def _write_manifest(self, manifest: dict[str, Any]) -> None:
        self._manifest_path(manifest["session_id"]).write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
        )

    def create(self, query_id: str, result_set_id: str, statement_index: int, statement_count: int, columns: list[dict[str, Any]]) -> dict[str, Any]:
        session_id = uuid4().hex
        now = time.time()
        manifest = {
            "session_id": session_id,
            "result_set_id": result_set_id,
            "query_id": query_id,
            "statement_index": statement_index,
            "statement_count": statement_count,
            "columns": columns,
            "total_rows": 0,
            "rows_affected": None,
            "truncated": False,
            "message": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": now + self.ttl,
            "completed": False,
            "status": "running",
        }
        conn = sqlite3.connect(self._path(session_id), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE rows (row_index INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
        conn.commit()
        self._connections[session_id] = conn
        self._locks[session_id] = threading.RLock()
        self._write_manifest(manifest)
        return manifest

    def manifest(self, session_id: str) -> dict[str, Any]:
        path = self._manifest_path(session_id)
        if not path.exists():
            raise KeyError("Result session not found")
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if float(manifest.get("expires_at", 0)) <= time.time():
            self.delete(session_id)
            raise KeyError("Result session expired")
        manifest["expires_at"] = time.time() + self.ttl
        self._write_manifest(manifest)
        return manifest

    def append(self, session_id: str, rows: list[list[Any]]) -> int:
        manifest = self.manifest(session_id)
        if not rows:
            return int(manifest["total_rows"])
        start = int(manifest["total_rows"])
        conn = self._conn(session_id)
        with self._locks[session_id]:
            conn.executemany(
                "INSERT INTO rows(row_index, payload) VALUES (?, ?)",
                [(start + i, _json(row)) for i, row in enumerate(rows)],
            )
            conn.commit()
        manifest["total_rows"] = start + len(rows)
        manifest["expires_at"] = time.time() + self.ttl
        self._write_manifest(manifest)
        return int(manifest["total_rows"])

    def complete(self, session_id: str, *, rows_affected: int | None = None, truncated: bool = False, message: str | None = None, status: str = "complete") -> dict[str, Any]:
        manifest = self.manifest(session_id)
        manifest.update({"completed": True, "status": status, "truncated": truncated, "message": message, "expires_at": time.time() + self.ttl})
        if rows_affected is not None:
            manifest["rows_affected"] = rows_affected
        self._write_manifest(manifest)
        return manifest

    def page(self, session_id: str, *, offset: int = 0, limit: int | None = None, global_filter: str = "", column_filters: list[dict[str, Any]] | None = None, sorting: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        manifest = self.manifest(session_id)
        try:
            limit = min(5000, max(1, int(limit or self.default_page_size)))
        except (TypeError, ValueError):
            limit = self.default_page_size
        try:
            offset = max(0, int(offset))
        except (TypeError, ValueError):
            offset = 0
        column_filters = column_filters or []
        conn = self._conn(session_id)

        # Keep paging work proportional to the requested page.  Earlier
        # versions decoded the complete result into Python for every page;
        # that made a million-row result look like a client-side grid again.
        where = ["1 = 1"]
        params: list[Any] = []
        if global_filter.strip():
            where.append("instr(lower(payload), lower(?)) > 0")
            params.append(global_filter.strip())
        for item in column_filters:
            try:
                index = int(item.get("columnIndex", -1))
            except (TypeError, ValueError):
                continue
            value = str(item.get("value", "")).strip()
            if not value or index < 0 or index >= len(manifest["columns"]):
                continue
            path = f"$[{index}]"
            where.append(
                f"instr(lower(CAST(json_extract(payload, '{path}') AS TEXT)), lower(?)) > 0"
            )
            params.append(value)

        valid_sort: list[tuple[int, bool]] = []
        for item in sorting or []:
            try:
                index = int(item.get("columnIndex", -1))
            except (TypeError, ValueError):
                continue
            if 0 <= index < len(manifest["columns"]):
                valid_sort.append((index, bool(item.get("desc"))))

        order_by: list[str] = []
        for index, descending in valid_sort:
            path = f"$[{index}]"
            type_name = str(
                manifest["columns"][index].get("DataType")
                or manifest["columns"][index].get("data_type")
                or ""
            )
            expression = (
                f"CAST(json_extract(payload, '{path}') AS REAL)"
                if _numeric(type_name)
                else f"lower(CAST(json_extract(payload, '{path}') AS TEXT))"
            )
            order_by.append(f"{expression} {'DESC' if descending else 'ASC'}")
        order_by.append("row_index ASC")
        predicate = " AND ".join(where)
        with self._locks[session_id]:
            total_rows = int(
                conn.execute(
                    f"SELECT COUNT(*) FROM rows WHERE {predicate}", params
                ).fetchone()[0]
            )
            stored = conn.execute(
                f"SELECT payload FROM rows WHERE {predicate} ORDER BY {', '.join(order_by)} LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        selected = [json.loads(item["payload"]) for item in stored]
        return {
            "session_id": session_id,
            "result_set_id": manifest["result_set_id"],
            "statement_index": manifest["statement_index"],
            "columns": manifest["columns"],
            "rows": selected,
            "offset": offset,
            "limit": limit,
            "total_rows": total_rows,
            "has_more": offset + len(selected) < total_rows,
            "rows_affected": manifest.get("rows_affected"),
            "truncated": manifest.get("truncated", False),
            "message": manifest.get("message"),
        }

    def export(self, session_id: str, fmt: str, **filters: Any) -> tuple[bytes, str, str]:
        manifest = self.manifest(session_id)
        rows: list[list[Any]] = []
        offset = 0
        while True:
            page = self.page(session_id, offset=offset, limit=1000, **filters)
            rows.extend(page["rows"])
            if not page["has_more"]:
                break
            offset += len(page["rows"])
        names = [str(column.get("ColumnName") or column.get("column_name") or f"column_{i + 1}") for i, column in enumerate(manifest["columns"])]
        if fmt == "json":
            body = json.dumps([dict(zip(names, row)) for row in rows], ensure_ascii=False, indent=2, default=str).encode()
            return body, "application/json", "export.json"
        output = io.StringIO()
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(names)
        writer.writerows(rows)
        return output.getvalue().encode(), "text/csv", "export.csv"

    def delete(self, session_id: str) -> None:
        if not _SESSION_ID_RE.fullmatch(session_id):
            return
        conn = self._connections.pop(session_id, None)
        if conn is not None:
            conn.close()
        self._locks.pop(session_id, None)
        self._path(session_id).unlink(missing_ok=True)
        self._manifest_path(session_id).unlink(missing_ok=True)

    def cleanup(self) -> int:
        removed = 0
        for path in self.root.glob("*.json"):
            try:
                manifest = json.loads(path.read_text(encoding="utf-8"))
                if float(manifest.get("expires_at", 0)) <= time.time():
                    self.delete(str(manifest["session_id"]))
                    removed += 1
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                path.unlink(missing_ok=True)
        return removed

    def close_all(self) -> None:
        # Keep manifest/database files so a browser reload or process restart
        # can restore non-expired result tabs. The next startup cleanup owns
        # TTL deletion.
        for conn in list(self._connections.values()):
            conn.close()
        self._connections.clear()
        self._locks.clear()
