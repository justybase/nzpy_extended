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
from typing import TypedDict

_SIMPLE_IDENT = re.compile(r"^[A-Z_][A-Z0-9_]*$")


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


def quote_netezza_ident(name: str) -> str:
    """Quote a Netezza identifier only when required.

    Simple uppercase identifiers (``^[A-Z_][A-Z0-9_]*$``) are returned
    unchanged. Mixed-case names or names with special characters are
    wrapped in double quotes with embedded quotes doubled.

    Note: reserved keywords are not quoted. Avoid reserved words as
    object names or quote them manually.
    """
    if not name:
        return name
    if _SIMPLE_IDENT.match(name):
        return name
    return '"' + name.replace('"', '""') + '"'


def _quote_sql_string(value: str) -> str:
    """Escape single quotes for COMMENT text."""
    return value.replace("'", "''")


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
    body = (definition or "").strip()
    if body.endswith(";"):
        body = body[:-1].rstrip()
    return f"CREATE OR REPLACE VIEW {qualified} AS\n{body};"


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
        lines.append(
            f"COMMENT ON PROCEDURE {qualified} "
            f"IS '{_quote_sql_string(procedure['description'])}';"
        )

    return "\n".join(lines)


__all__ = [
    "DdlColumn",
    "DdlKey",
    "ProcedureInfo",
    "build_procedure_ddl",
    "build_table_ddl",
    "build_view_ddl",
    "fix_procedure_returns",
    "quote_netezza_ident",
]
