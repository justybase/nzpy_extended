"""Netezza DDL builders — pure formatters without database I/O.

This module ports the DDL shape used by the JustyBase VS Code Netezza
provider (``packages/designer-core``) to Python so both single-object
and batch exports produce byte-compatible output.

The functions here never touch the network. Catalog fetching lives in
``_metadata_api.ConnectionMetadataProvider`` which passes plain dicts
and lists into these builders.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, TypedDict

_SIMPLE_IDENT = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_RESERVED_IDENTIFIERS = frozenset(
    "ABORT ALL ALLOCATE ANALYSE ANALYZE AND ANY AS ASC AUTOMAINT AWSS3 AZUREBLOB BETWEEN BINARY BIT BOTH CASE CAST CHAR CHARACTER CHECK CLUSTER COALESCE COLLATE COLLATION COLUMN CONSTRAINT COPY CROSS CURRENT CURRENT_CATALOG CURRENT_DATE CURRENT_DB CURRENT_SCHEMA CURRENT_SID CURRENT_TIME CURRENT_TIMESTAMP CURRENT_USER CURRENT_USERID CURRENT_USEROID DAYSPERROW DEALLOCATE DEC DECIMAL DECODE DEFAULT DEREGISTER DESC DISTINCT DISTRIBUTE DO ELSE END EXCEPT EXCLUDE EXISTS EXPLAIN EXPRESS EXTEND EXTERNAL EXTRACT FALSE FIRST FLOAT FOLLOWING FOR FOREIGN FROM FULL FUNCTION GENSTATS GLOBAL GROUP HAVING HISTOGRAM IDENTIFIER_CASE ILIKE IN INDEX INITIALLY INNER INOUT INTERSECT INTERVAL INTO JOURNAL LEADING LEFT LIKE LIMIT LOAD LOCAL LOCK MINUS MOVE NATURAL NCHAR NEW NOCASCADE NOT NOTNULL NULL NULLS NUMERIC NVL NVL2 OFFSET OFF OLD ON ONLINE ONLY OR ORDER OTHERS OUT OUTER OVER OVERLAPS PAUSESTEPS PAUSETIME PARTITION POSITION PRECEDING PRECISION PRESERVE PRIMARY REGISTER RESET REUSE RIGHT ROWS SELECT SESSION_USER SETOF SHOW SOME TABLE TEMPORAL THEN TIES TIME TIME_TRAVEL_ENABLE TIMESTAMP TO TRAILING TRANSACTION TRIGGER TRIM TRUE UNBOUNDED UNION UNIQUE USER USING VACUUM VARCHAR VERBOSE VERSION VIEW WHEN WHERE WITH WRITE CTID OID XMIN CMIN XMAX CMAX TABLEOID ROWID DATASLICEID CREATEXID DELETEXID".split()
)


class DdlColumn(TypedDict):
    """Column descriptor used by the table DDL builder."""

    name: str
    full_type: str
    not_null: bool
    default: str | None
    description: str | None


class DdlKey(TypedDict):
    """Constraint descriptor grouped by constraint name."""

    type: str
    type_char: str
    columns: list[str]
    pk_database: str | None
    pk_schema: str | None
    pk_relation: str | None
    pk_columns: list[str]
    update_type: str
    delete_type: str


class ProcedureInfo(TypedDict):
    """Procedure descriptor used by the procedure DDL builder."""

    procedure_name: str
    procedure_signature: str
    arguments: str | None
    returns: str
    execute_as_owner: bool
    description: str | None
    procedure_source: str


class ExternalColumn(TypedDict):
    """Column descriptor used by the external-table DDL builder."""

    name: str
    full_type: str
    not_null: bool


EXTERNAL_OPTIONS: tuple[tuple[str, str, str], ...] = (
    ("DELIMITER", "delim", "string"),
    ("ENCODING", "encoding", "string"),
    ("TIMESTYLE", "timestyle", "string"),
    ("REMOTESOURCE", "remotesource", "string"),
    ("SKIPROWS", "skiprows", "number"),
    ("MAXERRORS", "maxerrors", "number"),
    ("ESCAPECHAR", "escape", "string"),
    ("DECIMALDELIM", "decimaldelim", "string"),
    ("LOGDIR", "logdir", "string"),
    ("QUOTEDVALUE", "quotedvalue", "string"),
    ("NULLVALUE", "nullvalue", "string"),
    ("CRINSTRING", "crinstring", "boolean"),
    ("TRUNCSTRING", "truncstring", "boolean"),
    ("CTRLCHARS", "ctrlchars", "boolean"),
    ("IGNOREZERO", "ignorezero", "boolean"),
    ("TIMEEXTRAZEROS", "timeextrazeros", "boolean"),
    ("Y2BASE", "y2base", "number"),
    ("FILLRECORD", "fillrecord", "boolean"),
    ("COMPRESS", "compress", "compression"),
    ("INCLUDEHEADER", "includeheader", "boolean"),
    ("LFINSTRING", "lfinstring", "boolean"),
    ("DATESTYLE", "datestyle", "string"),
    ("DATEDELIM", "datedelim", "string"),
    ("TIMEDELIM", "timedelim", "string"),
    ("BOOLSTYLE", "boolstyle", "string"),
    ("FORMAT", "format", "string"),
    ("SOCKETBUFSIZE", "socketbufsize", "number"),
    ("RECORDDELIM", "recorddelim", "string"),
    ("MAXROWS", "maxrows", "number"),
    ("REQUIREQUOTES", "requirequotes", "boolean"),
    ("RECORDLENGTH", "recordlength", "number"),
    ("DATETIMEDELIM", "datetimedelim", "string"),
    ("REJECTFILE", "rejectfile", "string"),
    ("LAYOUT", "layout", "layout"),
    ("INCLUDEZEROSECONDS", "includezeroseconds", "boolean"),
    ("MERIDIANDELIM", "meridiandelim", "string"),
)


def quote_netezza_ident(name: str) -> str:
    """Quote a Netezza identifier only when required.

    Simple uppercase identifiers (``^[A-Z_][A-Z0-9_]*$``) are returned
    unchanged. Mixed-case names or names with special characters are
    wrapped in double quotes with embedded quotes doubled.

    Netezza reserved words and non-regular names are delimited.
    """
    if not name:
        return name
    if _SIMPLE_IDENT.match(name) and name[0] != "_" and name not in _RESERVED_IDENTIFIERS:
        return name
    return '"' + name.replace('"', '""') + '"'


def _quote_sql_string(value: str) -> str:
    """Escape single quotes for COMMENT text."""
    return value.replace("'", "''")


def _layout_catalog_value(row: Mapping[str, Any], key: str) -> Any:
    return row.get(key, row.get(key.lower(), row.get(key.upper())))


def _layout_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _layout_raw_text(value: Any) -> str:
    return "" if value is None else str(value)


def is_external_layout_zone_count(value: Any) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, int):
        return value > 0
    text = str(value).strip()
    return text.isdigit() and int(text) > 0


def reconstruct_external_layout(
    catalog_layout: Any,
    zones: Sequence[Mapping[str, Any]],
    column_names: Sequence[str] | None = None,
) -> str | None:
    """Rebuild the LAYOUT clause from ordered ``_V_EXTZONES`` rows."""
    if catalog_layout is None:
        return None
    raw = _layout_text(catalog_layout)
    if not raw or raw == "0":
        return None
    if not raw.isdigit():
        return raw
    count = int(raw)
    if count <= 0:
        return None
    if len(zones) != count:
        raise ValueError(
            "Cannot reconstruct external table LAYOUT: catalog reports "
            f"{count} zones, but _V_EXTZONES returned {len(zones)}"
        )

    definitions: list[str] = []
    column_index = 0
    for index, row in enumerate(zones, 1):
        get = lambda key: _layout_text(_layout_catalog_value(row, key))
        use_type = get("usetype").upper()
        if use_type and use_type not in {"REF", "FILLER"}:
            raise ValueError(
                f"Cannot reconstruct external table LAYOUT: unsupported zone use type {use_type}"
            )
        name = _layout_raw_text(_layout_catalog_value(row, "name"))
        if column_names is not None and use_type not in {"REF", "FILLER"}:
            if column_index >= len(column_names):
                raise ValueError(
                    "Cannot reconstruct external table LAYOUT: more data zones "
                    "than external-table columns"
                )
            name = column_names[column_index]
            column_index += 1
        delimiter = _layout_raw_text(_layout_catalog_value(row, "delimiter"))
        type_name, style = get("type"), get("style")
        length, null_if = get("length"), get("nullif")
        if not length:
            raise ValueError(
                f"Cannot reconstruct external table LAYOUT: zone {index} has no length"
            )
        for field in ("around", "endian", "alignment", "modulus"):
            if get(field):
                raise ValueError(
                    f"Cannot reconstruct external table LAYOUT: zone {index} "
                    f"uses unsupported {field.upper()} metadata"
                )
        parts = [use_type]
        if name:
            parts.append(quote_netezza_ident(name))
        if type_name:
            parts.append(type_name)
        if style:
            parts.append(style)
        if delimiter:
            if not style:
                raise ValueError(
                    f"Cannot reconstruct external table LAYOUT: zone {index} has a delimiter without a style"
                )
            if "'" not in style:
                parts.append(f"'{_quote_sql_string(delimiter)}'")
        parts.append(length)
        if null_if:
            parts.append(null_if if null_if.upper().startswith("NULLIF") else f"NULLIF {null_if}")
        definitions.append(" ".join(part for part in parts if part))
    if column_names is not None and column_index != len(column_names):
        raise ValueError(
            "Cannot reconstruct external table LAYOUT: catalog has "
            f"{column_index} data zones for {len(column_names)} external-table columns"
        )
    return ", ".join(definitions)


def fix_procedure_returns(returns: str) -> str:
    """Normalize Netezza ANY-length character return types.

    The catalog stores ``CHARACTER VARYING`` for ``(ANY)`` signatures,
    while valid DDL requires the explicit ``(ANY)`` suffix.
    """
    upper = returns.strip().upper()
    if upper == "CHARACTER VARYING":
        return "CHARACTER VARYING(ANY)"
    if upper == "NATIONAL CHARACTER VARYING":
        return "NATIONAL CHARACTER VARYING(ANY)"
    if upper == "NATIONAL CHARACTER":
        return "NATIONAL CHARACTER(ANY)"
    if upper == "CHARACTER":
        return "CHARACTER(ANY)"
    return returns


def build_table_ddl(
    database: str,
    schema: str,
    table_name: str,
    columns: Sequence[DdlColumn],
    distribution_columns: Sequence[str],
    organize_columns: Sequence[str],
    keys: Mapping[str, DdlKey],
    table_comment: str | None,
) -> str:
    """Build a complete CREATE TABLE statement with constraints and comments.

    The output covers columns (type, NOT NULL, DEFAULT), DISTRIBUTE ON,
    ORGANIZE ON, primary/unique/foreign keys as ALTER TABLE statements,
    and COMMENT ON TABLE/COLUMN clauses.
    """
    if len(columns) == 0:
        return f"-- Table {database}.{schema}.{table_name} has no columns or was not found"

    qualified = (
        f"{quote_netezza_ident(database)}."
        f"{quote_netezza_ident(schema)}."
        f"{quote_netezza_ident(table_name)}"
    )

    column_lines: list[str] = []
    for column in columns:
        definition = f"    {quote_netezza_ident(column['name'])} {column['full_type']}"
        if column["not_null"]:
            definition += " NOT NULL"
        if column["default"] is not None:
            definition += f" DEFAULT {column['default']}"
        column_lines.append(definition)

    ddl_lines: list[str] = [
        f"CREATE TABLE {qualified}",
        "(",
        ",\n".join(column_lines),
    ]

    if len(distribution_columns) > 0:
        dist_list = ", ".join(quote_netezza_ident(c) for c in distribution_columns)
        ddl_lines.append(f")\nDISTRIBUTE ON ({dist_list})")
    else:
        ddl_lines.append(")\nDISTRIBUTE ON RANDOM")

    if len(organize_columns) > 0:
        org_list = ", ".join(quote_netezza_ident(c) for c in organize_columns)
        ddl_lines.append(f"ORGANIZE ON ({org_list})")

    ddl_lines.append(";")
    ddl_lines.append("")

    for key_name, key in keys.items():
        clean_key = quote_netezza_ident(key_name)
        clean_columns = [quote_netezza_ident(c) for c in key["columns"]]
        type_char = key["type_char"]
        if type_char == "f":
            pk_columns = [quote_netezza_ident(c) for c in key["pk_columns"] if c]
            pk_database = key["pk_database"]
            pk_schema = key["pk_schema"]
            pk_relation = key["pk_relation"]
            if len(pk_columns) > 0 and pk_database and pk_schema and pk_relation:
                referenced = ".".join(
                    quote_netezza_ident(part)
                    for part in (pk_database, pk_schema, pk_relation)
                )
                ddl_lines.append(
                    f"ALTER TABLE {qualified} "
                    f"ADD CONSTRAINT {clean_key} {key['type']} "
                    f"({', '.join(clean_columns)}) "
                    f"REFERENCES {referenced} "
                    f"({', '.join(pk_columns)}) "
                    f"ON DELETE {key['delete_type']} "
                    f"ON UPDATE {key['update_type']};"
                )
            else:
                ddl_lines.append(
                    f"-- WARNING: FOREIGN KEY {clean_key} skipped, "
                    f"incomplete reference in catalog data"
                )
        elif type_char in ("p", "u"):
            ddl_lines.append(
                f"ALTER TABLE {qualified} "
                f"ADD CONSTRAINT {clean_key} {key['type']} "
                f"({', '.join(clean_columns)});"
            )
        else:
            ddl_lines.append(
                f"-- WARNING: constraint {clean_key} skipped, "
                f"unknown constraint type '{type_char}'"
            )

    if table_comment:
        ddl_lines.append("")
        ddl_lines.append(
            f"COMMENT ON TABLE {qualified} IS '{_quote_sql_string(table_comment)}';"
        )

    for column in columns:
        if column["description"]:
            ddl_lines.append(
                f"COMMENT ON COLUMN {qualified}.{quote_netezza_ident(column['name'])} "
                f"IS '{_quote_sql_string(column['description'])}';"
            )

    return "\n".join(ddl_lines)


def build_view_ddl(
    database: str,
    schema: str,
    view_name: str,
    definition: str,
) -> str:
    """Build a CREATE OR REPLACE VIEW statement around catalog SQL.

    The definition column is only populated when the connection targets
    the same database that owns the view.
    """
    qualified = (
        f"{quote_netezza_ident(database)}."
        f"{quote_netezza_ident(schema)}."
        f"{quote_netezza_ident(view_name)}"
    )
    body = definition or ""
    if not body.rstrip().endswith(";"):
        body += ";"
    return f"CREATE OR REPLACE VIEW {qualified} AS\n{body}"


def build_procedure_ddl(
    database: str,
    schema: str,
    procedure: ProcedureInfo,
) -> str:
    """Build a CREATE OR REPLACE PROCEDURE statement with NZPLSQL body."""
    arguments_text = (procedure["arguments"] or "").strip()
    if not arguments_text:
        arguments_clause = "()"
    elif arguments_text.startswith("(") and arguments_text.endswith(")"):
        arguments_clause = arguments_text
    else:
        arguments_clause = f"({arguments_text})"

    qualified = (
        f"{quote_netezza_ident(database)}."
        f"{quote_netezza_ident(schema)}."
        f"{quote_netezza_ident(procedure['procedure_name'])}"
    )

    lines = [
        f"CREATE OR REPLACE PROCEDURE {qualified}{arguments_clause}",
        f"RETURNS {fix_procedure_returns(procedure['returns'])}",
        "EXECUTE AS OWNER" if procedure["execute_as_owner"] else "EXECUTE AS CALLER",
        "LANGUAGE NZPLSQL AS",
        "BEGIN_PROC",
        procedure["procedure_source"],
        "END_PROC;",
    ]

    if procedure["description"]:
        signature = procedure["procedure_signature"]
        signature_open = signature.find("(")
        if signature_open < 0:
            raise ValueError("Procedure signature is required to reconstruct its comment")
        comment_signature = signature[signature_open:]
        lines.append(
            f"COMMENT ON PROCEDURE {qualified}{comment_signature} "
            f"IS '{_quote_sql_string(procedure['description'])}';"
        )

    return "\n".join(lines)


def build_external_table_ddl(
    database: str,
    schema: str,
    table_name: str,
    data_object: str | None,
    columns: Sequence[ExternalColumn],
    options: Mapping[str, Any],
) -> str:
    """Build executable CREATE EXTERNAL TABLE DDL from catalog metadata."""
    if not columns:
        raise ValueError(f"External table {table_name} has no columns")
    qualified = ".".join(
        quote_netezza_ident(part) for part in (database, schema, table_name)
    )
    lines = [
        f"CREATE EXTERNAL TABLE {qualified}",
        "(",
        ",\n".join(
            f"    {quote_netezza_ident(column['name'])} {column['full_type']}"
            f"{' NOT NULL' if column['not_null'] else ''}"
            for column in columns
        ),
        ")",
        "USING",
        "(",
    ]
    if data_object is not None:
        lines.append(f"    DATAOBJECT('{_quote_sql_string(data_object)}')")
    for keyword, column_name, kind in EXTERNAL_OPTIONS:
        value = options.get(column_name)
        if value is None:
            continue
        if kind == "layout":
            layout = str(value).strip()
            if not layout:
                continue
            zone_definitions = layout if layout.startswith("(") and layout.endswith(")") else f"({layout})"
            rendered = zone_definitions
        elif kind == "string":
            rendered = f"'{_quote_sql_string(str(value))}'"
        elif kind == "compression":
            text = str(value).strip().lower()
            rendered = "true" if _as_bool(value) else "false" if text in {"0", "f", "false", "no", "off"} else str(value)
        elif kind == "boolean":
            rendered = "true" if _as_bool(value) else "false"
        else:
            rendered = str(value)
        lines.append(f"    {keyword} {rendered}")
    lines.append(");")
    return "\n".join(lines)


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "t", "true", "yes", "y", "on"}


def build_synonym_ddl(
    database: str,
    schema: str,
    synonym_name: str,
    reference: str,
    description: str | None,
    reference_database: str | None = None,
    reference_schema: str | None = None,
) -> str:
    """Build CREATE SYNONYM DDL and its optional catalog comment."""
    parts = _split_identifier_path(reference)
    if len(parts) == 1 and reference_database:
        parts[:0] = [reference_database, reference_schema or ""]
    elif len(parts) == 1 and reference_schema:
        parts.insert(0, reference_schema)
    elif len(parts) == 2 and reference_database:
        parts.insert(0, reference_database)
    target = ".".join(quote_netezza_ident(part) if part else "" for part in parts)
    qualified = ".".join(
        quote_netezza_ident(part) for part in (database, schema, synonym_name)
    )
    lines = [f"CREATE SYNONYM {qualified} FOR {target};"]
    if description:
        lines.append(
            f"COMMENT ON SYNONYM {qualified} "
            f"IS '{_quote_sql_string(description)}';"
        )
    return "\n".join(lines)


def _split_identifier_path(value: str) -> list[str]:
    raw_parts: list[str] = []
    current: list[str] = []
    quoted = False
    index = 0
    while index < len(value):
        char = value[index]
        if char == '"':
            if quoted and index + 1 < len(value) and value[index + 1] == '"':
                current.extend(('"', '"'))
                index += 1
            else:
                current.append(char)
                quoted = not quoted
        elif char == "." and not quoted:
            raw_parts.append("".join(current))
            current = []
        else:
            current.append(char)
        index += 1
    if quoted:
        raise ValueError(f"Invalid quoted identifier path: {value}")
    raw_parts.append("".join(current))
    parts: list[str] = []
    for raw_part in raw_parts:
        part = raw_part.strip()
        if not part.startswith('"'):
            if '"' in part:
                raise ValueError(f"Invalid quoted identifier path: {value}")
            parts.append(part)
            continue
        if len(part) < 2 or not part.endswith('"'):
            raise ValueError(f"Invalid quoted identifier path: {value}")
        identifier: list[str] = []
        index = 1
        while index < len(part) - 1:
            if part[index] == '"':
                if index + 1 >= len(part) - 1 or part[index + 1] != '"':
                    raise ValueError(f"Invalid quoted identifier path: {value}")
                identifier.append('"')
                index += 2
            else:
                identifier.append(part[index])
                index += 1
        parts.append("".join(identifier))
    has_omitted_schema = len(parts) == 3 and bool(parts[0]) and not parts[1] and bool(parts[2])
    if len(parts) > 3 or (any(not part for part in parts) and not has_omitted_schema):
        raise ValueError(f"Invalid identifier path: {value}")
    return parts


__all__ = [
    "DdlColumn",
    "DdlKey",
    "ExternalColumn",
    "EXTERNAL_OPTIONS",
    "ProcedureInfo",
    "build_external_table_ddl",
    "build_procedure_ddl",
    "build_synonym_ddl",
    "build_table_ddl",
    "build_view_ddl",
    "fix_procedure_returns",
    "is_external_layout_zone_count",
    "quote_netezza_ident",
    "reconstruct_external_layout",
]
