"""Demonstrate DDL discovery and export from a Netezza database.

This example uses ``conn.meta`` to generate SQL for tables, views, and
stored procedures. It demonstrates:

* Detailed table metadata: column types, nullability, defaults, distribution
  and organization keys, constraints, comments, and ownership.
* Complete DDL for one table, view, and user-defined procedure.
* DDL for an explicit list of objects and objects selected by a ``LIKE``
  pattern.
* A batch export to a SQL file, with a summary of objects that succeeded or
  failed.

Run it from the repository root after installing the package and its
dependencies::

    python examples/21_ddl.py

The example requires a reachable Netezza database. Configure the connection
with these environment variables before running it::

    export NZ_DEV_HOST=your-netezza-host
    export NZ_DEV_PORT=5480
    export NZ_DEV_DATABASE=your-database
    export NZ_DEV_USER=your-user
    export NZ_DEV_PASSWORD=your-password
    python examples/21_ddl.py

``NZ_DEV_DATABASE`` is preferred; ``NZ_DEV_DB`` is accepted as a legacy
fallback. The example has defaults for host, port, database, and user, but
requires ``NZ_DEV_PASSWORD`` to be set.

The script selects its demo table from the connected database, then uses
that table's schema for the default batch export. Set ``EXPORT_SCHEMA`` near
the top of the file to choose another schema. To export the whole database,
call ``show_full_export(conn, None)`` or call the metadata API directly with
``schema=None``::

    result = await conn.meta.export_database_ddl(
        schema=None,
        include_views=True,
        include_procedures=True,
        output_path="full_ddl_export.sql",
    )

The returned mapping contains the database name, the complete script in
``ddl``, the number of successfully exported objects, the number skipped,
per-object error messages, and the output path. If ``output_path`` is given,
the SQL file is written relative to the current working directory and an
existing file at that path is replaced. The generated SQL is printed for
inspection; this example does not execute it.

Catalog views are scoped to the connected database. Connect to the database
that owns the objects: view definitions and comments are only available when
the relevant catalog metadata is visible from that connection. The script
uses the first available user table, view, and non-built-in procedure for its
single-object demonstrations; view and procedure sections are skipped when
the database has no matching objects. Batch export records individual object
errors and continues with the remaining objects.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import nzpy_extended as nzpy

NZ_HOST = os.environ.get("NZ_DEV_HOST", "192.168.0.144")
NZ_PORT = int(os.environ.get("NZ_DEV_PORT", "5480"))
# Preferred NZ_DEV_DATABASE wins; legacy NZ_DEV_DB stays as a fallback.
NZ_DB = os.environ.get("NZ_DEV_DATABASE") or os.environ.get("NZ_DEV_DB", "JUST_DATA")
NZ_USER = os.environ.get("NZ_DEV_USER", "admin")
NZ_PASSWORD = os.environ.get("NZ_DEV_PASSWORD")
if not NZ_PASSWORD:
    raise RuntimeError("Set the NZ_DEV_PASSWORD environment variable before running this example")

# Optional schema override; None uses the demo table's schema in main().
EXPORT_SCHEMA: str | None = None
# Output file for the batch export demo.
EXPORT_FILE = "full_ddl_export.sql"


def print_section(title: str) -> None:
    """Print a readable section header."""
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


async def show_single_table(conn: nzpy.Connection, schema: str, table: str) -> None:
    """Fetch and print DDL for one table with all building blocks."""
    print_section(f"Single table: {schema}.{table}")

    # Low-level building blocks used by get_table_ddl internally.
    columns = await conn.meta.get_detailed_columns(table, schema=schema)
    print(f"Columns ({len(columns)}):")
    for col in columns:
        null_text = "NOT NULL" if col["not_null"] else "NULL"
        default_text = f" DEFAULT {col['default']}" if col["default"] else ""
        print(f"  {col['column_name']:30s} {col['full_type']:25s} {null_text}{default_text}")

    dist = await conn.meta.get_distribution_key(table, schema=schema)
    print(f"Distribution: {'ON (' + ', '.join(dist) + ')' if dist else 'ON RANDOM'}")

    org = await conn.meta.get_organize_columns(table, schema=schema)
    print(f"Organize: {'ON (' + ', '.join(org) + ')' if org else 'NONE'}")

    keys = await conn.meta.get_table_keys(table, schema=schema)
    print(f"Constraints ({len(keys)}):")
    for name, key in keys.items():
        print(f"  {name}: {key['type']} ON ({', '.join(key['columns'])})")

    comment = await conn.meta.get_table_comment(table, schema=schema)
    print(f"Table comment: {comment if comment else '(none)'}")

    owner = await conn.meta.get_table_owner(table, schema=schema)
    print(f"Owner: {owner if owner else '(unknown)'}")

    # Complete runnable statement.
    ddl = await conn.meta.get_table_ddl(table, schema=schema)
    print("\n-- Complete CREATE TABLE DDL:")
    print(ddl)


async def show_single_view(conn: nzpy.Connection, schema: str, view: str) -> None:
    """Fetch and print DDL for one view."""
    print_section(f"Single view: {schema}.{view}")
    ddl = await conn.meta.get_view_ddl(view, schema=schema)
    print(ddl)


async def show_single_procedure(conn: nzpy.Connection, schema: str, proc: str) -> None:
    """Fetch and print DDL for one stored procedure."""
    print_section(f"Single procedure: {schema}.{proc}")
    ddl = await conn.meta.get_procedure_ddl(proc, schema=schema)
    print(ddl)


async def show_list_mode(conn: nzpy.Connection, schema: str) -> None:
    """Build DDL for an explicit list and for a LIKE pattern."""
    print_section(f"List mode in schema {schema}")

    # Example 1: explicit list of tables (plain names or SCHEMA.TABLE).
    tables = await conn.meta.get_tables(schema=schema)
    sample = [t["table_name"] for t in tables[:3]]
    if sample:
        print(f"Explicit list: {sample}")
        results = await conn.meta.get_tables_ddl(schema=schema, tables=sample)
        for item in results:
            status = "OK" if item["error"] is None else f"FAILED: {item['error']}"
            print(f"  {item['schema']}.{item['table_name']}: {status}")
            if item["ddl"]:
                print(item["ddl"][:500] + ("..." if len(item["ddl"]) > 500 else ""))
    else:
        print("No tables found for the explicit list demo.")

    # Example 2: pattern-based selection (first letter of first table).
    if tables:
        first_letter = tables[0]["table_name"][:1]
        pattern = f"{first_letter}%"
        print(f"\nPattern selection: LIKE '{pattern}'")
        results = await conn.meta.get_tables_ddl(schema=schema, table_pattern=pattern)
        print(f"Matched tables: {len(results)}")
        for item in results[:5]:
            status = "OK" if item["error"] is None else f"FAILED: {item['error']}"
            print(f"  {item['schema']}.{item['table_name']}: {status}")


async def show_full_export(conn: nzpy.Connection, schema: str | None) -> None:
    """Export a whole schema or database to a single SQL script."""
    label = schema if schema else "whole database"
    print_section(f"Full export: {label}")

    summary = await conn.meta.export_database_ddl(
        schema=schema,
        include_views=True,
        include_procedures=True,
        output_path=EXPORT_FILE,
    )
    print(f"Database: {summary['database']}")
    print(f"Exported objects: {summary['object_count']}")
    print(f"Skipped: {summary['skipped']}")
    print(f"Errors: {len(summary['errors'])}")
    for message in summary["errors"][:10]:
        print(f"  ERROR: {message}")
    print(f"Script written to: {summary['output_path']}")
    print("\n-- Script preview (first 2000 chars):")
    print(summary["ddl"][:2000])


async def main() -> None:
    """Run single, list, and full export demos against live catalog data."""
    conn = await nzpy.connect(
        user=NZ_USER, password=NZ_PASSWORD,
        host=NZ_HOST, port=NZ_PORT, database=NZ_DB,
    )
    try:
        db = await conn.meta.get_current_database()
        schema_path = await conn.meta.get_current_schema()
        print(f"Connected to: {db} (current schema: {schema_path})")
        print("Tip: connect to the database that owns the objects.")
        print("View definitions require the same-database connection.")

        # Pick live demo targets so the example works on any database.
        tables = await conn.meta.get_tables()
        if not tables:
            print("No user tables found. Nothing to export.")
            return
        demo_schema = str(tables[0]["schema"])
        demo_table = str(tables[0]["table_name"])
        await show_single_table(conn, demo_schema, demo_table)

        views = await conn.meta.get_views()
        if views:
            await show_single_view(conn, str(views[0]["schema"]), str(views[0]["view_name"]))
        else:
            print_section("Single view: skipped (no views found)")

        procs = await conn.meta.get_procedures()
        user_procs = [p for p in procs if not p.get("builtin")]
        if user_procs:
            target = user_procs[0]
            signature = str(target.get("signature") or target["proc_name"])
            await show_single_procedure(conn, str(target["schema"]), signature)
        else:
            print_section("Single procedure: skipped (no user procedures found)")

        await show_list_mode(conn, demo_schema)

        export_schema = EXPORT_SCHEMA if EXPORT_SCHEMA else demo_schema
        await show_full_export(conn, export_schema)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
