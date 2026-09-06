"""Write preview and short-lived confirmation tokens."""

from __future__ import annotations

import secrets
import time
from typing import Any

from app.services.sql_document import command_type, is_write_statement, split_statements


class SqlSafetyService:
    def __init__(self, ttl_seconds: int = 300) -> None:
        self.ttl = max(30, ttl_seconds)
        self._tokens: dict[str, tuple[float, str, str]] = {}

    def _purge_expired(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        for token, entry in list(self._tokens.items()):
            if entry[0] <= now:
                self._tokens.pop(token, None)

    def preview(self, sql: str, database: str | None) -> dict[str, Any]:
        now = time.time()
        self._purge_expired(now)
        statements = split_statements(sql)
        contains_write = any(is_write_statement(statement.sql) for statement in statements)
        token = secrets.token_urlsafe(24)
        expires = now + self.ttl
        key = f"{database or ''}\x00{sql}"
        self._tokens[token] = (expires, key, sql)
        return {
            "database": database,
            "readOnly": not contains_write,
            "containsWrite": contains_write,
            "previewToken": token,
            "expiresAt": expires,
            "statements": [
                {
                    "index": statement.index,
                    "startOffset": statement.start_offset,
                    "endOffset": statement.end_offset,
                    "sql": statement.sql,
                    "commandType": command_type(statement.sql),
                    "readOnly": not is_write_statement(statement.sql),
                    "warnings": (["Statement changes database state and requires confirmation."] if is_write_statement(statement.sql) else []),
                }
                for statement in statements
            ],
        }

    def validate(self, token: str | None, sql: str, database: str | None, confirmed: bool) -> bool:
        if not any(is_write_statement(statement.sql) for statement in split_statements(sql)):
            return True
        if not confirmed or not token:
            return False
        self._purge_expired()
        entry = self._tokens.pop(token, None)
        return bool(entry and entry[1] == f"{database or ''}\x00{sql}")
