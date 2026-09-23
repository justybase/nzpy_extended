"""
Netezza metadata API — async catalog queries.

Provides ``ConnectionMetadataProvider`` which executes queries against
Netezza system catalog views (``_v_table``, ``_v_view``, ``_v_relation_column``,
``_v_procedure``, ``_v_schema``, etc.) and returns structured results.

Usage from a connection::

    meta = conn.meta
    tables = await meta.get_tables(schema="ADMIN")
    cols = await meta.get_columns("MY_TABLE", schema="ADMIN")

The connection **must** be connected to the target database; catalog views
are database-scoped.  Running metadata queries on ``SYSTEM`` will only
show system objects.

Column names in ``_v_*`` views vary by Netezza version; the queries here
have been tested against NPS 11.2.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ._ddl import (
    DdlColumn,
    DdlKey,
    ProcedureInfo,
    build_procedure_ddl,
    build_table_ddl,
    build_view_ddl,
)

if TYPE_CHECKING:
    from .core import Connection


def _escape_literal(value: str) -> str:
    """Escape a string for use inside a single-quoted SQL literal."""
    return value.replace("'", "''")


def _is_missing_relation_error(exc: Exception) -> bool:
    """Check whether an error means an optional catalog view is absent.

    PostgreSQL-compatible backends report an undefined relation with SQLSTATE
    42P01. Match that condition specifically so a missing column or another
    query error is never mistaken for an absent optional catalog view.
    """
    pending = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if getattr(current, "sqlstate", None) == "42P01":
            return True
        for arg in getattr(current, "args", ()):
            if isinstance(arg, Exception):
                pending.append(arg)
            elif isinstance(arg, dict) and arg.get("C") == "42P01":
                return True
    return False


def _split_qualified_details(
    name: str,
) -> tuple[str | None, str, bool, bool]:
    """Split an optional SCHEMA.OBJECT name into (schema, object).

    Dots inside double-quoted identifiers and inside parentheses
    (procedure signatures with typed defaults) do not act as separators.
    Surrounding double quotes are stripped with doubled quotes unescaped.
    The final two values report whether the schema and object were quoted.
    Names with more than three dot-separated parts are rejected.
    """
    parts: list[str] = []
    current: list[str] = []
    in_quotes = False
    paren_depth = 0
    index = 0
    while index < len(name):
        char = name[index]
        if char == '"':
            if in_quotes and index + 1 < len(name) and name[index + 1] == '"':
                current.append('"')
                index += 2
                continue
            in_quotes = not in_quotes
            current.append(char)
            index += 1
            continue
        if not in_quotes and char == "(":
            paren_depth += 1
            current.append(char)
            index += 1
            continue
        if not in_quotes and char == ")" and paren_depth > 0:
            paren_depth -= 1
            current.append(char)
            index += 1
            continue
        if not in_quotes and paren_depth == 0 and char == ".":
            parts.append("".join(current))
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
    parts.append("".join(current))

    def _unquote(part: str) -> tuple[str, bool]:
        text = part.strip()
        if text.startswith('"'):
            index = 1
            quoted: list[str] = []
            while index < len(text):
                if text[index] == '"':
                    if index + 1 < len(text) and text[index + 1] == '"':
                        quoted.append('"')
                        index += 2
                        continue
                    suffix = text[index + 1:]
                    if not suffix or suffix.startswith("("):
                        return "".join(quoted) + suffix, True
                    break
                quoted.append(text[index])
                index += 1
        return text, False

    cleaned = [_unquote(part) for part in parts]
    if len(cleaned) == 1:
        return None, cleaned[0][0], False, cleaned[0][1]
    if len(cleaned) == 2:
        schema, schema_quoted = cleaned[0]
        object_name, object_quoted = cleaned[1]
        return schema or None, object_name, schema_quoted, object_quoted
    if len(cleaned) == 3:
        schema, schema_quoted = cleaned[1]
        object_name, object_quoted = cleaned[2]
        return schema or None, object_name, schema_quoted, object_quoted
    raise ValueError(f"Invalid qualified object name: {name!r}")


def _split_qualified(name: str) -> tuple[str | None, str]:
    """Split an optional SCHEMA.OBJECT name, stripping identifier quotes."""
    schema, object_name, _, _ = _split_qualified_details(name)
    return schema, object_name


def _normalize_identifier(value: str, quoted: bool = False) -> str:
    """Fold unquoted SQL identifiers to uppercase, retaining quoted case."""
    return value if quoted else value.upper()


def _normalize_simple_identifier(value: str) -> str:
    """Normalize one identifier supplied through a standalone parameter."""
    schema, identifier, _, quoted = _split_qualified_details(value)
    if schema is not None:
        raise ValueError(f"Expected a single identifier, got {value!r}")
    return _normalize_identifier(identifier, quoted)


def _normalize_object_name(
    name: str,
    schema: str | None = None,
) -> tuple[str | None, str]:
    """Normalize a possibly qualified object and an optional schema."""
    qualified_schema, object_name, schema_quoted, object_quoted = (
        _split_qualified_details(name)
    )
    if qualified_schema is not None:
        schema = _normalize_identifier(qualified_schema, schema_quoted)
    elif schema is not None:
        schema = _normalize_simple_identifier(schema)
    return schema, _normalize_identifier(object_name, object_quoted)


def _quote_identifier(value: str) -> str:
    """Quote an identifier when forwarding a canonical catalog name."""
    return '"' + value.replace('"', '""') + '"'


def _to_bool(value: Any) -> bool:
    """Normalize Netezza boolean representations to Python bool."""
    if value is True or value is False:
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    return text in ("1", "t", "true", "yes", "y", "on")


def _to_opt_str(value: Any) -> str | None:
    """Return stripped string or None for NULL/empty catalog values."""
    if value is None:
        return None
    text = str(value).strip()
    if text == "":
        return None
    return text


class ConnectionMetadataProvider:
    """Async metadata queries against a Netezza connection.

    .. attribute:: _conn

        The underlying async ``Connection``.  Set once during construction.
    """

    __slots__ = ("_conn",)

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    # ── helpers ─────────────────────────────────────────────────────────────

    async def _query(self, sql: str) -> list[tuple[Any, ...]]:
        """Execute *sql* and return all rows as a list of tuples."""
        cur = self._conn.cursor()
        try:
            await cur.execute(sql)
            return await cur.fetchall()
        finally:
            await cur.close()

    async def _query_dicts(self, sql: str) -> list[dict[str, Any]]:
        """Execute *sql* and return rows as dicts keyed by lowercased column name."""
        cur = self._conn.cursor()
        try:
            await cur.execute(sql)
            desc = cur.description
            if desc is None:
                return []
            col_names = [d[0].lower() for d in desc]
            rows = await cur.fetchall()
            return [dict(zip(col_names, row)) for row in rows]
        finally:
            await cur.close()

    # ── schemas / databases ─────────────────────────────────────────────────

    async def get_schemas(self) -> list[str]:
        """Return all schema names in the current database."""
        rows = await self._query(
            "SELECT schema FROM _v_schema ORDER BY schema"
        )
        return [r[0] for r in rows]

    async def get_databases(self) -> list[str]:
        """Return all database names visible to the current user."""
        rows = await self._query(
            "SELECT database FROM _v_database ORDER BY database"
        )
        return [r[0] for r in rows]

    async def get_current_database(self) -> str | None:
        """Return the name of the currently connected database."""
        rows = await self._query("SELECT current_catalog")
        return rows[0][0] if rows else None

    async def get_current_schema(self) -> str | None:
        """Return the current schema search path."""
        rows = await self._query("SELECT current_schema")
        return rows[0][0] if rows else None

    # ── tables ──────────────────────────────────────────────────────────────

    async def get_tables(
        self,
        schema: str | None = None,
        table_pattern: str | None = None,
        include_system: bool = False,
    ) -> list[dict[str, Any]]:
        """List tables with owner and type information.

        :param schema:          Filter by schema name (case-sensitive).
        :param table_pattern:   LIKE pattern for table name (e.g. ``'MY%'``).
        :param include_system:  When ``False`` (default), excludes system
                                schemas and system tables.
        :returns:               List of dicts with keys: ``schema``, ``table_name``,
                                ``owner``, ``objtype``, ``objid``, ``row_count``.
        """
        conditions = ["tablename IS NOT NULL"]
        if schema is not None:
            conditions.append(f"schema = '{schema}'")
        if table_pattern is not None:
            conditions.append(f"tablename LIKE '{table_pattern}'")
        if not include_system:
            conditions.append(
                "schema NOT IN ('DEFINITION_SCHEMA', 'INZA', 'NZ_QUERY_HISTORY')"
            )
            conditions.append("objtype <> 'SYSTEM_TABLE'")
        where = " AND ".join(conditions)
        return await self._query_dicts(
            f"SELECT schema, tablename AS table_name, owner, "
            f"objtype, objid, reltuples AS row_count "
            f"FROM _v_table WHERE {where} ORDER BY schema, tablename"
        )

    # ── views ───────────────────────────────────────────────────────────────

    async def get_views(
        self,
        schema: str | None = None,
        view_pattern: str | None = None,
    ) -> list[dict[str, Any]]:
        """List views with owner and definition.

        :param schema:        Filter by schema name.
        :param view_pattern:  LIKE pattern for view name.
        :returns:             List of dicts with keys: ``schema``, ``view_name``,
                              ``owner``, ``objid``, ``definition``.
        """
        conditions = ["viewname IS NOT NULL"]
        if schema is not None:
            conditions.append(f"schema = '{schema}'")
        if view_pattern is not None:
            conditions.append(f"viewname LIKE '{view_pattern}'")
        where = " AND ".join(conditions)
        return await self._query_dicts(
            f"SELECT schema, viewname AS view_name, owner, objid, definition "
            f"FROM _v_view WHERE {where} ORDER BY schema, viewname"
        )

    # ── columns ─────────────────────────────────────────────────────────────

    async def get_columns(
        self,
        table_name: str,
        schema: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return column metadata for a table or view.

        :param table_name:  Table or view name (required).
        :param schema:      Schema name.  If ``None``, the search path is used.
        :returns:           List of dicts with keys: ``column_name``, ``ordinal``,
                            ``data_type``, ``nullable``, ``objid``.
        """
        schema, table_name = _normalize_object_name(table_name, schema)

        conditions = [f"name = '{_escape_literal(table_name)}'"]
        if schema is not None:
            conditions.append(f"schema = '{_escape_literal(schema)}'")
        where = " AND ".join(conditions)

        return await self._query_dicts(
            f"SELECT attname AS column_name, attnum AS ordinal, "
            f"format_type AS data_type, "
            f"CASE WHEN attnotnull THEN 'N' ELSE 'Y' END AS nullable, "
            f"objid "
            f"FROM _v_relation_column "
            f"WHERE {where} ORDER BY attnum"
        )

    # ── distribution key ────────────────────────────────────────────────────

    async def get_distribution_key(
        self,
        table_name: str,
        schema: str | None = None,
    ) -> list[str]:
        """Return the distribution key column name(s) for a table.

        Returns an empty list if the table is distributed randomly
        (no rows in ``_v_table_dist_map`` match).

        :param table_name:  Table name.
        :param schema:      Schema name.
        """
        schema, table_name = _normalize_object_name(table_name, schema)

        conditions = [f"tablename = '{_escape_literal(table_name)}'"]
        if schema is not None:
            conditions.append(f"schema = '{_escape_literal(schema)}'")
        where = " AND ".join(conditions)

        rows = await self._query(
            f"SELECT attname "
            f"FROM _v_table_dist_map "
            f"WHERE {where} "
            f"ORDER BY distattnum"
        )
        return [r[0] for r in rows]

    # ── table sizes / storage stats ─────────────────────────────────────────

    async def get_table_sizes(
        self,
        schema: str | None = None,
        table_pattern: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return storage statistics for tables.

        Uses ``_v_table_storage_stat`` for allocated/used bytes and
        ``_v_table.reltuples`` for estimated row count.

        :param schema:          Filter by schema name.
        :param table_pattern:   LIKE pattern for table name.
        :returns:               List of dicts with keys: ``schema``, ``table_name``,
                                ``used_bytes``, ``allocated_bytes``, ``size_mb``,
                                ``skew``.
        """
        conditions = ["tablename IS NOT NULL"]
        if schema is not None:
            conditions.append(f"schema = '{schema}'")
        if table_pattern is not None:
            conditions.append(f"tablename LIKE '{table_pattern}'")
        where = " AND ".join(conditions)

        return await self._query_dicts(
            f"SELECT schema, tablename AS table_name, "
            f"used_bytes, allocated_bytes, "
            f"(used_bytes / 1048576)::BIGINT AS size_mb, "
            f"skew "
            f"FROM _v_table_storage_stat "
            f"WHERE {where} ORDER BY used_bytes DESC"
        )

    # ── procedures ──────────────────────────────────────────────────────────

    async def get_procedures(
        self,
        schema: str | None = None,
        proc_pattern: str | None = None,
    ) -> list[dict[str, Any]]:
        """List stored procedures.

        :param schema:        Filter by schema name.
        :param proc_pattern:  LIKE pattern for procedure name.
        :returns:             List of dicts with keys: ``schema``, ``proc_name``,
                              ``owner``, ``objid``, ``signature``, ``returns``,
                              ``builtin``, ``source``.
        """
        conditions = ["procedure IS NOT NULL"]
        if schema is not None:
            conditions.append(f"schema = '{schema}'")
        if proc_pattern is not None:
            conditions.append(f"procedure LIKE '{proc_pattern}'")
        where = " AND ".join(conditions)

        return await self._query_dicts(
            f"SELECT schema, procedure AS proc_name, owner, objid, "
            f"proceduresignature AS signature, returns, "
            f"builtin, proceduresource AS source "
            f"FROM _v_procedure WHERE {where} ORDER BY schema, procedure"
        )

    # ── sequences ───────────────────────────────────────────────────────────

    async def get_sequences(
        self,
        schema: str | None = None,
    ) -> list[dict[str, Any]]:
        """List sequences.

        :param schema:  Filter by schema name.
        :returns:       List of dicts with keys: ``schema``, ``seq_name``,
                        ``owner``, ``objid``.
        """
        conditions = ["seqname IS NOT NULL"]
        if schema is not None:
            conditions.append(f"schema = '{schema}'")
        where = " AND ".join(conditions)
        return await self._query_dicts(
            f"SELECT schema, seqname AS seq_name, owner, objid "
            f"FROM _v_sequence WHERE {where} ORDER BY schema, seqname"
        )

    # ── synonyms ────────────────────────────────────────────────────────────

    async def get_synonyms(
        self,
        schema: str | None = None,
    ) -> list[dict[str, Any]]:
        """List synonyms.

        :param schema:  Filter by schema name.
        :returns:       List of dicts with keys: ``schema``, ``synonym_name``,
                        ``ref_database``, ``ref_schema``, ``referenced_object``,
                        ``owner``, ``objid``.
        """
        conditions = ["synonym_name IS NOT NULL"]
        if schema is not None:
            conditions.append(f"schema = '{schema}'")
        where = " AND ".join(conditions)
        return await self._query_dicts(
            f"SELECT schema, synonym_name, refdatabase AS ref_database, "
            f"refschema AS ref_schema, refobjname AS referenced_object, "
            f"owner, objid "
            f"FROM _v_synonym WHERE {where} ORDER BY schema, synonym_name"
        )

    # ── sessions ────────────────────────────────────────────────────────────

    async def get_sessions(self) -> list[dict[str, Any]]:
        """Return active database sessions."""
        return await self._query_dicts(
            "SELECT id AS session_id, username, dbname AS database_name, "
            "conntime, priority, status, type AS client_type, "
            "client_os_username "
            "FROM _v_session ORDER BY conntime DESC"
        )

    # ── users ───────────────────────────────────────────────────────────────

    async def get_users(self) -> list[dict[str, Any]]:
        """List database users."""
        return await self._query_dicts(
            "SELECT username, objid FROM _v_user ORDER BY username"
        )

    # ── groups ──────────────────────────────────────────────────────────────

    async def get_groups(self) -> list[dict[str, Any]]:
        """List database groups."""
        return await self._query_dicts(
            "SELECT groupname, objid FROM _v_group ORDER BY groupname"
        )

    # ── query history ───────────────────────────────────────────────────────

    async def get_query_history(
        self,
        limit: int = 100,
        username: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return recent query history entries.

        Requires the history collection to be enabled on the database.

        :param limit:     Maximum number of rows to return.
        :param username:  Optional filter by user.
        """
        conditions = ["1=1"]
        if username is not None:
            conditions.append(f"qh_user = '{username}'")
        where = " AND ".join(conditions)
        return await self._query_dicts(
            f"SELECT qh_sessionid AS session_id, qh_user AS username, "
            f"qh_database AS database_name, "
            f"qh_sql AS query_text, qh_tsubmit AS submit_time, "
            f"qh_tstart AS start_time, qh_resrows AS result_rows "
            f"FROM _v_qryhist "
            f"WHERE {where} "
            f"ORDER BY qh_tsubmit DESC LIMIT {int(limit)}"
        )

    # ── generic search ──────────────────────────────────────────────────────

    async def search_objects(
        self,
        name_pattern: str,
        schema: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search for objects (tables, views, procedures) by name pattern.

        :param name_pattern:  LIKE pattern to match against object name.
        :param schema:        Optional schema filter.
        :returns:             List of dicts with keys: ``object_type``,
                              ``schema``, ``object_name``, ``owner``, ``objid``.
        """
        results: list[dict[str, Any]] = []

        tables = await self.get_tables(schema=schema, table_pattern=name_pattern)
        for t in tables:
            results.append({
                "object_type": "TABLE",
                "schema": t["schema"],
                "object_name": t["table_name"],
                "owner": t.get("owner"),
                "objid": t.get("objid"),
            })

        views = await self.get_views(schema=schema, view_pattern=name_pattern)
        for v in views:
            results.append({
                "object_type": "VIEW",
                "schema": v["schema"],
                "object_name": v["view_name"],
                "owner": v.get("owner"),
                "objid": v.get("objid"),
            })

        procs = await self.get_procedures(schema=schema, proc_pattern=name_pattern)
        for p in procs:
            results.append({
                "object_type": "PROCEDURE",
                "schema": p["schema"],
                "object_name": p["proc_name"],
                "owner": p.get("owner"),
                "objid": p.get("objid"),
            })

        return results

    # ── DDL helpers ─────────────────────────────────────────────────────────

    async def get_detailed_columns(
        self,
        table_name: str,
        schema: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return full column descriptors needed for CREATE TABLE.

        Unlike :meth:`get_columns`, this includes the complete type text,
        NOT NULL flag, DEFAULT expression, and column comment.

        :param table_name:  Table or view name, optionally SCHEMA.TABLE.
        :param schema:      Schema name. Qualified names take precedence.
        :returns:           List of dicts with keys: ``column_name``,
                            ``ordinal``, ``full_type``, ``not_null``,
                            ``default``, ``description``.
        """
        schema, table_name = _normalize_object_name(table_name, schema)

        conditions = [f"name = '{_escape_literal(table_name)}'"]
        if schema is not None:
            conditions.append(f"schema = '{_escape_literal(schema)}'")
        where = " AND ".join(conditions)

        rows = await self._query_dicts(
            "SELECT schema, attname AS column_name, attnum AS ordinal, "
            "format_type AS full_type, attnotnull AS not_null_raw, "
            "coldefault AS default_value, description "
            "FROM _v_relation_column "
            f"WHERE {where} ORDER BY attnum"
        )
        if schema is None:
            matching_schemas = sorted({str(row.get("schema")) for row in rows})
            if len(matching_schemas) > 1:
                raise ValueError(
                    f"Table {table_name} exists in several schemas "
                    f"({', '.join(matching_schemas)}); pass schema explicitly"
                )
        result: list[dict[str, Any]] = []
        for row in rows:
            result.append({
                "column_name": row.get("column_name"),
                "ordinal": row.get("ordinal"),
                "full_type": row.get("full_type"),
                "not_null": _to_bool(row.get("not_null_raw")),
                "default": _to_opt_str(row.get("default_value")),
                "description": _to_opt_str(row.get("description")),
            })
        return result

    async def get_organize_columns(
        self,
        table_name: str,
        schema: str | None = None,
    ) -> list[str]:
        """Return ORGANIZE ON column names for a table.

        Returns an empty list when the table has no organization or when
        the catalog view is unavailable on the server version.
        """
        schema, table_name = _normalize_object_name(table_name, schema)

        conditions = [f"tablename = '{_escape_literal(table_name)}'"]
        if schema is not None:
            conditions.append(f"schema = '{_escape_literal(schema)}'")
        where = " AND ".join(conditions)

        try:
            rows = await self._query(
                "SELECT attname "
                "FROM _v_table_organize_column "
                f"WHERE {where} "
                "ORDER BY orgseqno"
            )
        except Exception as exc:
            if _is_missing_relation_error(exc):
                return []
            raise
        return [str(r[0]) for r in rows]

    async def get_table_keys(
        self,
        table_name: str,
        schema: str | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Return primary/unique/foreign key constraints grouped by name.

        :returns: Mapping of constraint name to dict with keys: ``type``,
                  ``type_char``, ``columns``, ``pk_database``,
                  ``pk_schema``, ``pk_relation``, ``pk_columns``,
                  ``update_type``, ``delete_type``.
        """
        schema, table_name = _normalize_object_name(table_name, schema)

        conditions = [f"relation = '{_escape_literal(table_name)}'"]
        if schema is not None:
            conditions.append(f"schema = '{_escape_literal(schema)}'")
        where = " AND ".join(conditions)

        try:
            rows = await self._query_dicts(
                "SELECT constraintname, contype, attname, "
                "pkdatabase, pkschema, pkrelation, pkattname, "
                "updt_type, del_type "
                "FROM _v_relation_keydata "
                f"WHERE {where} ORDER BY constraintname, conseq"
            )
        except Exception as exc:
            if _is_missing_relation_error(exc):
                return {}
            raise

        type_map = {"p": "PRIMARY KEY", "f": "FOREIGN KEY", "u": "UNIQUE"}
        keys: dict[str, dict[str, Any]] = {}
        for row in rows:
            key_name = str(row.get("constraintname"))
            if key_name not in keys:
                contype = str(row.get("contype") or "")
                keys[key_name] = {
                    "type": type_map.get(contype, "UNKNOWN"),
                    "type_char": contype,
                    "columns": [],
                    "pk_database": _to_opt_str(row.get("pkdatabase")),
                    "pk_schema": _to_opt_str(row.get("pkschema")),
                    "pk_relation": _to_opt_str(row.get("pkrelation")),
                    "pk_columns": [],
                    "update_type": str(row.get("updt_type") or "NO ACTION"),
                    "delete_type": str(row.get("del_type") or "NO ACTION"),
                }
            attname = _to_opt_str(row.get("attname"))
            if attname:
                keys[key_name]["columns"].append(attname)
            pkattname = _to_opt_str(row.get("pkattname"))
            if pkattname:
                keys[key_name]["pk_columns"].append(pkattname)
        return keys

    async def get_table_comment(
        self,
        table_name: str,
        schema: str | None = None,
    ) -> str | None:
        """Return the COMMENT ON TABLE text or None when not set."""
        schema, table_name = _normalize_object_name(table_name, schema)

        conditions = [f"objname = '{_escape_literal(table_name)}'"]
        if schema is not None:
            conditions.append(f"schema = '{_escape_literal(schema)}'")
        base_where = " AND ".join(conditions)

        for extra in (" AND objtype = 'TABLE'", ""):
            try:
                rows = await self._query(
                    "SELECT description FROM _v_object_data "
                    f"WHERE {base_where}{extra}"
                )
            except Exception as exc:
                if _is_missing_relation_error(exc):
                    return None
                raise
            for row in rows:
                comment = _to_opt_str(row[0]) if len(row) > 0 else None
                if comment:
                    return comment
        return None

    async def get_table_owner(
        self,
        table_name: str,
        schema: str | None = None,
    ) -> str | None:
        """Return the table owner or None when the table is not found."""
        schema, table_name = _normalize_object_name(table_name, schema)

        conditions = [f"tablename = '{_escape_literal(table_name)}'"]
        if schema is not None:
            conditions.append(f"schema = '{_escape_literal(schema)}'")
        where = " AND ".join(conditions)

        try:
            rows = await self._query(
                f"SELECT owner FROM _v_table WHERE {where}"
            )
        except Exception as exc:
            if _is_missing_relation_error(exc):
                return None
            raise
        if rows:
            return _to_opt_str(rows[0][0])
        return None

    # ── single-object DDL ───────────────────────────────────────────────────

    async def get_table_ddl(
        self,
        table_name: str,
        schema: str | None = None,
        database: str | None = None,
    ) -> str:
        """Build complete CREATE TABLE DDL for one table.

        The output includes columns with types, NOT NULL, and DEFAULT,
        DISTRIBUTE ON, ORGANIZE ON, constraints, and comments.

        The connection must target the database that owns the table.
        Pass ``schema`` explicitly when several schemas contain the same
        table name.
        """
        schema_upper, table_upper = _normalize_object_name(table_name, schema)

        if schema_upper is None:
            rows = await self._query_dicts(
                "SELECT schema, tablename AS table_name FROM _v_table "
                f"WHERE tablename = '{_escape_literal(table_upper)}' "
                "ORDER BY schema"
            )
            exact = [
                r for r in rows
                if str(r.get("table_name", "")) == table_upper
            ]
            if len(exact) > 1:
                schemas = ", ".join(str(r.get("schema")) for r in exact)
                raise ValueError(
                    f"Table {table_upper} exists in several schemas "
                    f"({schemas}); pass schema explicitly"
                )
            if exact:
                schema_upper = str(exact[0]["schema"])
                table_upper = str(exact[0]["table_name"])

        db_resolved = database if database is not None else await self.get_current_database()
        if db_resolved is None:
            db_resolved = "UNKNOWN"
        schema_display = schema_upper if schema_upper is not None else "UNKNOWN"

        quoted_table = _quote_identifier(table_upper)
        quoted_schema = (
            _quote_identifier(schema_upper) if schema_upper is not None else None
        )
        columns_rows = await self.get_detailed_columns(
            quoted_table, schema=quoted_schema
        )
        if not columns_rows:
            where = f"{schema_display}.{table_upper}"
            raise ValueError(f"Table {where} not found")
        columns: list[DdlColumn] = [
            {
                "name": str(c.get("column_name")),
                "full_type": str(c.get("full_type")),
                "not_null": bool(c.get("not_null")),
                "default": c.get("default"),
                "description": c.get("description"),
            }
            for c in columns_rows
        ]

        dist = await self.get_distribution_key(quoted_table, schema=quoted_schema)
        org = await self.get_organize_columns(quoted_table, schema=quoted_schema)
        raw_keys = await self.get_table_keys(quoted_table, schema=quoted_schema)
        keys: dict[str, DdlKey] = {
            name: {
                "type": str(info["type"]),
                "type_char": str(info["type_char"]),
                "columns": [str(c) for c in info["columns"]],
                "pk_database": info["pk_database"],
                "pk_schema": info["pk_schema"],
                "pk_relation": info["pk_relation"],
                "pk_columns": [str(c) for c in info["pk_columns"]],
                "update_type": str(info["update_type"]),
                "delete_type": str(info["delete_type"]),
            }
            for name, info in raw_keys.items()
        }
        comment = await self.get_table_comment(quoted_table, schema=quoted_schema)

        return build_table_ddl(
            db_resolved, schema_display, table_upper,
            columns, dist, org, keys, comment,
        )

    async def get_view_ddl(
        self,
        view_name: str,
        schema: str | None = None,
        database: str | None = None,
    ) -> str:
        """Build CREATE OR REPLACE VIEW DDL for one view.

        The view definition is only available when connected to the same
        database that owns the view.
        """
        schema, view_name = _normalize_object_name(view_name, schema)
        conditions = [f"viewname = '{_escape_literal(view_name)}'"]
        if schema is not None:
            conditions.append(f"schema = '{_escape_literal(schema)}'")
        where = " AND ".join(conditions)

        rows = await self._query_dicts(
            "SELECT schema, viewname AS view_name, owner, objid, definition "
            f"FROM _v_view WHERE {where} ORDER BY schema, viewname"
        )
        exact = [
            row for row in rows
            if str(row.get("view_name", "")) == view_name
        ]
        if len(exact) > 1:
            schemas = ", ".join(str(r.get("schema")) for r in exact)
            raise ValueError(
                f"View {view_name} exists in several schemas "
                f"({schemas}); pass schema explicitly"
            )
        if not exact:
            where_label = f"{schema}.{view_name}" if schema else view_name
            raise ValueError(f"View {where_label} not found")
        match = exact[0]

        db_resolved = database if database is not None else await self.get_current_database()
        if db_resolved is None:
            db_resolved = "UNKNOWN"
        definition = str(match.get("definition") or "").strip()
        if not definition:
            where_label = f"{match['schema']}.{match['view_name']}"
            raise ValueError(
                f"View {where_label} has no definition. Connect to the "
                "database that owns the view to read its SQL text"
            )
        return build_view_ddl(
            db_resolved, str(match["schema"]), str(match["view_name"]), definition
        )

    async def get_procedure_ddl(
        self,
        proc_name: str,
        schema: str | None = None,
        database: str | None = None,
    ) -> str:
        """Build CREATE OR REPLACE PROCEDURE DDL for one procedure.

        ``proc_name`` accepts either a bare name (``MY_PROC``) or a full
        signature (``MY_PROC(INTEGER, VARCHAR(10))``). Overloaded names
        resolve to the first matching signature in sorted order.
        """
        qual_schema, qual_proc, schema_quoted, proc_quoted = (
            _split_qualified_details(proc_name)
        )
        if qual_schema is not None:
            schema = _normalize_identifier(qual_schema, schema_quoted)
        elif schema is not None:
            schema = _normalize_simple_identifier(schema)
        proc_upper = _normalize_identifier(qual_proc, proc_quoted)
        is_signature = "(" in proc_upper
        conditions = ["procedure IS NOT NULL"]
        if schema is not None:
            conditions.append(f"schema = '{_escape_literal(schema)}'")
        if is_signature:
            conditions.append(f"proceduresignature = '{_escape_literal(proc_upper)}'")
        else:
            conditions.append(f"procedure = '{_escape_literal(proc_upper)}'")
        where = " AND ".join(conditions)

        rows = await self._query_dicts(
            "SELECT schema, procedure AS proc_name, "
            "proceduresignature AS signature, "
            "arguments, returns, executedasowner, description, "
            "proceduresource AS source "
            "FROM _v_procedure "
            f"WHERE {where} ORDER BY proceduresignature"
        )
        if not rows:
            where_label = f"{schema}.{proc_upper}" if schema else proc_upper
            raise ValueError(f"Procedure {where_label} not found")
        row = rows[0]

        raw_owner_flag = row.get("executedasowner")
        execute_as_owner = True if raw_owner_flag is None else _to_bool(raw_owner_flag)
        info: ProcedureInfo = {
            "procedure_name": str(row.get("proc_name")),
            "procedure_signature": str(row.get("signature") or ""),
            "arguments": _to_opt_str(row.get("arguments")),
            "returns": str(row.get("returns") or "INTEGER"),
            "execute_as_owner": execute_as_owner,
            "description": _to_opt_str(row.get("description")),
            "procedure_source": str(row.get("source") or ""),
        }

        db_resolved = database if database is not None else await self.get_current_database()
        if db_resolved is None:
            db_resolved = "UNKNOWN"
        schema_display = (
            schema if schema is not None else str(row.get("schema") or "UNKNOWN")
        )
        # Prefer the canonical schema from the catalog row when available.
        catalog_schema = _to_opt_str(row.get("schema"))
        if catalog_schema:
            schema_display = catalog_schema

        return build_procedure_ddl(db_resolved, schema_display, info)

    # ── list and batch DDL ──────────────────────────────────────────────────

    async def get_tables_ddl(
        self,
        schema: str | None = None,
        table_pattern: str | None = None,
        tables: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Build DDL for a list of tables or for a filtered table set.

        Provide either an explicit ``tables`` list (plain names or
        SCHEMA.TABLE entries) or a ``schema`` / ``table_pattern`` filter.
        Every entry continues past individual failures; the failing DDL
        is empty and the message is stored in ``error``.
        """
        targets: list[tuple[str | None, str]] = []
        if tables is not None:
            for entry in tables:
                entry_schema, entry_table = _normalize_object_name(
                    str(entry), schema
                )
                targets.append((entry_schema, entry_table))
        else:
            schema_filter = (
                _normalize_simple_identifier(schema) if schema is not None else None
            )
            for row in await self.get_tables(
                schema=schema_filter, table_pattern=table_pattern
            ):
                targets.append((str(row["schema"]), str(row["table_name"])))

        results: list[dict[str, Any]] = []
        for target_schema, target_table in targets:
            label_schema = target_schema if target_schema else "UNKNOWN"
            try:
                ddl = await self.get_table_ddl(
                    _quote_identifier(target_table),
                    schema=(
                        _quote_identifier(target_schema)
                        if target_schema is not None else None
                    ),
                )
                results.append({
                    "schema": label_schema,
                    "table_name": target_table,
                    "ddl": ddl,
                    "error": None,
                })
            except Exception as exc:
                results.append({
                    "schema": label_schema,
                    "table_name": target_table,
                    "ddl": "",
                    "error": str(exc),
                })
        return results

    async def get_views_ddl(
        self,
        schema: str | None = None,
        view_pattern: str | None = None,
        views: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Build DDL for a list of views or for a filtered view set."""
        targets: list[tuple[str | None, str]] = []
        if views is not None:
            for entry in views:
                entry_schema, entry_view = _normalize_object_name(
                    str(entry), schema
                )
                targets.append((entry_schema, entry_view))
        else:
            schema_filter = (
                _normalize_simple_identifier(schema) if schema is not None else None
            )
            for row in await self.get_views(
                schema=schema_filter, view_pattern=view_pattern
            ):
                targets.append((str(row["schema"]), str(row["view_name"])))

        results: list[dict[str, Any]] = []
        for target_schema, target_view in targets:
            label_schema = target_schema if target_schema else "UNKNOWN"
            try:
                ddl = await self.get_view_ddl(
                    _quote_identifier(target_view),
                    schema=(
                        _quote_identifier(target_schema)
                        if target_schema is not None else None
                    ),
                )
                results.append({
                    "schema": label_schema,
                    "view_name": target_view,
                    "ddl": ddl,
                    "error": None,
                })
            except Exception as exc:
                results.append({
                    "schema": label_schema,
                    "view_name": target_view,
                    "ddl": "",
                    "error": str(exc),
                })
        return results

    async def get_procedures_ddl(
        self,
        schema: str | None = None,
        proc_pattern: str | None = None,
        procedures: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Build DDL for a list of procedures or a filtered procedure set."""
        targets: list[tuple[str | None, str]] = []
        if procedures is not None:
            for entry in procedures:
                entry_schema, entry_proc = _normalize_object_name(
                    str(entry), schema
                )
                targets.append((entry_schema, entry_proc))
        else:
            schema_filter = (
                _normalize_simple_identifier(schema) if schema is not None else None
            )
            for row in await self.get_procedures(
                schema=schema_filter, proc_pattern=proc_pattern
            ):
                if _to_bool(row.get("builtin")):
                    continue
                targets.append((
                    str(row["schema"]),
                    str(row["signature"] or row["proc_name"]),
                ))

        results: list[dict[str, Any]] = []
        for target_schema, target_proc in targets:
            label_schema = target_schema if target_schema else "UNKNOWN"
            try:
                ddl = await self.get_procedure_ddl(
                    _quote_identifier(target_proc),
                    schema=(
                        _quote_identifier(target_schema)
                        if target_schema is not None else None
                    ),
                )
                results.append({
                    "schema": label_schema,
                    "proc_name": target_proc,
                    "ddl": ddl,
                    "error": None,
                })
            except Exception as exc:
                results.append({
                    "schema": label_schema,
                    "proc_name": target_proc,
                    "ddl": "",
                    "error": str(exc),
                })
        return results

    async def export_database_ddl(
        self,
        schema: str | None = None,
        table_pattern: str | None = None,
        view_pattern: str | None = None,
        proc_pattern: str | None = None,
        tables: Sequence[str] | None = None,
        views: Sequence[str] | None = None,
        procedures: Sequence[str] | None = None,
        include_views: bool = True,
        include_procedures: bool = True,
        output_path: str | Path | None = None,
        database: str | None = None,
    ) -> dict[str, Any]:
        """Export DDL for a schema or for the whole current database.

        This is the batch counterpart of the single-object getters. It
        collects tables and optionally views and procedures into one
        script with header, per-type sections, and a footer summary.
        Individual object failures are recorded and do not abort the run.
        When ``output_path`` is given the script is written to that file.
        """
        db_resolved = database if database is not None else await self.get_current_database()
        if db_resolved is None:
            db_resolved = "UNKNOWN"

        errors: list[str] = []
        parts: list[str] = []
        object_count = 0
        skipped = 0

        parts.append("-- ============================================")
        parts.append("-- Batch DDL Export")
        parts.append(f"-- Database: {db_resolved}")
        if schema is not None:
            parts.append(f"-- Schema: {_normalize_simple_identifier(schema)}")
        else:
            parts.append("-- Schema: ALL")
        included = ["TABLE"]
        if include_views:
            included.append("VIEW")
        if include_procedures:
            included.append("PROCEDURE")
        parts.append(f"-- Object Types: {', '.join(included)}")
        parts.append("-- ============================================")
        parts.append("")

        table_results = await self.get_tables_ddl(
            schema=schema, table_pattern=table_pattern, tables=tables
        )
        if table_results:
            parts.append("-- ----------------------------------------")
            parts.append(f"-- TABLES ({len(table_results)})")
            parts.append("-- ----------------------------------------")
            parts.append("")
            for item in table_results:
                label = f"{db_resolved}.{item['schema']}.{item['table_name']}"
                if item["error"] is None and item["ddl"]:
                    parts.append(f"-- TABLE: {label}")
                    parts.append(str(item["ddl"]))
                    parts.append("")
                    object_count += 1
                else:
                    errors.append(f"Table {label}: {item['error']}")
                    skipped += 1

        if include_views:
            view_results = await self.get_views_ddl(
                schema=schema, view_pattern=view_pattern, views=views
            )
            if view_results:
                parts.append("-- ----------------------------------------")
                parts.append(f"-- VIEWS ({len(view_results)})")
                parts.append("-- ----------------------------------------")
                parts.append("")
                for item in view_results:
                    label = f"{db_resolved}.{item['schema']}.{item['view_name']}"
                    if item["error"] is None and item["ddl"]:
                        parts.append(f"-- VIEW: {label}")
                        parts.append(str(item["ddl"]))
                        parts.append("")
                        object_count += 1
                    else:
                        errors.append(f"View {label}: {item['error']}")
                        skipped += 1

        if include_procedures:
            proc_results = await self.get_procedures_ddl(
                schema=schema, proc_pattern=proc_pattern, procedures=procedures
            )
            if proc_results:
                parts.append("-- ----------------------------------------")
                parts.append(f"-- PROCEDURES ({len(proc_results)})")
                parts.append("-- ----------------------------------------")
                parts.append("")
                for item in proc_results:
                    label = f"{db_resolved}.{item['schema']}.{item['proc_name']}"
                    if item["error"] is None and item["ddl"]:
                        parts.append(f"-- PROCEDURE: {label}")
                        parts.append(str(item["ddl"]))
                        parts.append("")
                        object_count += 1
                    else:
                        errors.append(f"Procedure {label}: {item['error']}")
                        skipped += 1

        written_path: str | None = None
        write_error: str | None = None

        def _render_script() -> str:
            script_parts = parts.copy()
            script_parts.extend([
                "-- ============================================",
                "-- End of Batch DDL Export",
                f"-- Total objects: {object_count}",
            ])
            if skipped > 0:
                script_parts.append(f"-- Skipped: {skipped}")
            if errors:
                script_parts.append(f"-- Errors: {len(errors)}")
            script_parts.append("-- ============================================")
            if write_error is not None:
                script_parts.append(f"-- WARNING: {write_error}")
                script_parts.append("-- ============================================")

            if errors:
                report = [
                    "-- ============================================",
                    "-- ERROR REPORT",
                    "-- The following errors occurred during generation:",
                ]
                report.extend(f"-- {message}" for message in errors)
                report.extend(["-- ============================================", ""])
                # The header is always exactly 7 lines (indexes 0-6).
                script_parts[7:7] = report

            return "\n".join(script_parts)

        script = _render_script()
        if output_path is not None:
            target = Path(output_path)
            try:
                if target.parent != Path("."):
                    target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(script, encoding="utf-8")
                written_path = str(target)
            except OSError as exc:
                write_error = f"Cannot write {target}: {exc}"
                errors.append(write_error)
                skipped += 1
                script = _render_script()

        return {
            "database": db_resolved,
            "ddl": script,
            "object_count": object_count,
            "skipped": skipped,
            "errors": errors,
            "output_path": written_path,
        }
