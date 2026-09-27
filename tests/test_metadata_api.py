"""
test_metadata_api.py
====================
Tests for the ConnectionMetadataProvider (conn.meta) metadata API.
"""

import uuid

import pytest

import nzpy_extended as nzpy
from nzpy_extended._metadata_api import ConnectionMetadataProvider

pytestmark = pytest.mark.full


# ---------------------------------------------------------------------------
# Basic provider existence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_meta_property_exists(con):
    """conn.meta is a ConnectionMetadataProvider."""
    assert isinstance(con.meta, ConnectionMetadataProvider)
    assert con.meta._conn is con


# ---------------------------------------------------------------------------
# Schemas and databases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_schemas(con):
    schemas = await con.meta.get_schemas()
    assert isinstance(schemas, list)
    assert len(schemas) > 0
    assert all(isinstance(s, str) for s in schemas)
    # At minimum the default user schema or INFORMATION_SCHEMA should exist
    assert any(s in schemas for s in ("ADMIN", "PUBLIC", "INFORMATION_SCHEMA"))


@pytest.mark.asyncio
async def test_get_databases(con):
    dbs = await con.meta.get_databases()
    assert isinstance(dbs, list)
    assert len(dbs) > 0
    assert all(isinstance(d, str) for d in dbs)


@pytest.mark.asyncio
async def test_get_current_database(con):
    db = await con.meta.get_current_database()
    assert isinstance(db, str)
    assert len(db) > 0


@pytest.mark.asyncio
async def test_get_current_schema(con):
    schema = await con.meta.get_current_schema()
    assert isinstance(schema, str)
    assert len(schema) > 0


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_tables_all(con):
    tables = await con.meta.get_tables()
    assert isinstance(tables, list)
    for t in tables:
        assert "schema" in t
        assert "table_name" in t


@pytest.mark.asyncio
async def test_get_tables_filter_schema(con):
    tables = await con.meta.get_tables(schema="ADMIN")
    assert isinstance(tables, list)
    for t in tables:
        assert t["schema"] == "ADMIN"


@pytest.mark.asyncio
async def test_get_tables_filter_pattern(con):
    tables = await con.meta.get_tables(table_pattern="%")
    assert isinstance(tables, list)


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_views(con):
    views = await con.meta.get_views()
    assert isinstance(views, list)
    for v in views:
        assert "schema" in v
        assert "view_name" in v
        assert "owner" in v


@pytest.mark.asyncio
async def test_get_views_filter_schema(con):
    views = await con.meta.get_views(schema="DEFINITION_SCHEMA")
    for v in views:
        assert v["schema"] == "DEFINITION_SCHEMA"


# ---------------------------------------------------------------------------
# Columns
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_columns_with_any_table(con):
    """get_columns for the first table found in _v_table."""
    tables = await con.meta.get_tables()
    if not tables:
        pytest.skip("No tables found to test columns")
    t = tables[0]
    cols = await con.meta.get_columns(t["table_name"], schema=t["schema"])
    assert isinstance(cols, list)
    if len(cols) > 0:
        for c in cols:
            assert "column_name" in c
            assert "ordinal" in c
            assert "data_type" in c
            assert "nullable" in c


@pytest.mark.asyncio
async def test_get_columns_nonexistent_table(con):
    """Non-existent table returns empty list."""
    cols = await con.meta.get_columns("ZZ_NO_SUCH_TABLE_ZZZ", schema="SYSTEM")
    assert cols == []


# ---------------------------------------------------------------------------
# Distribution key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_distribution_key_temp_table(con):
    cur = con.cursor()
    await cur.execute("""
        CREATE TEMP TABLE T_META_DK_TEST (
            id INT,
            name VARCHAR(50)
        ) DISTRIBUTE ON (id)
    """)
    try:
        dk = await con.meta.get_distribution_key("T_META_DK_TEST")
        assert isinstance(dk, list)
        assert len(dk) > 0
        assert "ID" in dk
    finally:
        await cur.execute("DROP TABLE T_META_DK_TEST IF EXISTS")


@pytest.mark.asyncio
async def test_get_distribution_key_random(con):
    cur = con.cursor()
    await cur.execute("""
        CREATE TEMP TABLE T_META_DK_RAND (
            id INT
        ) DISTRIBUTE ON RANDOM
    """)
    try:
        dk = await con.meta.get_distribution_key("T_META_DK_RAND")
        assert dk == []
    finally:
        await cur.execute("DROP TABLE T_META_DK_RAND IF EXISTS")


# ---------------------------------------------------------------------------
# Table sizes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_table_sizes(con):
    sizes = await con.meta.get_table_sizes()
    assert isinstance(sizes, list)
    if len(sizes) > 0:
        for s in sizes:
            assert "schema" in s
            assert "table_name" in s


# ---------------------------------------------------------------------------
# Procedures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_procedures(con):
    procs = await con.meta.get_procedures()
    assert isinstance(procs, list)
    for p in procs:
        assert "schema" in p
        assert "proc_name" in p
        assert "owner" in p


# ---------------------------------------------------------------------------
# Sequences
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_sequences(con):
    seqs = await con.meta.get_sequences()
    assert isinstance(seqs, list)
    for s in seqs:
        assert "schema" in s
        assert "seq_name" in s


# ---------------------------------------------------------------------------
# Synonyms
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_synonyms(con):
    syns = await con.meta.get_synonyms()
    assert isinstance(syns, list)
    for s in syns:
        assert "schema" in s
        assert "synonym_name" in s


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_sessions(con):
    sessions = await con.meta.get_sessions()
    assert isinstance(sessions, list)
    assert len(sessions) >= 1  # at least our own session
    for s in sessions:
        assert "session_id" in s
        assert "username" in s


# ---------------------------------------------------------------------------
# Users and groups
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_users(con):
    users = await con.meta.get_users()
    assert isinstance(users, list)
    assert len(users) > 0
    for u in users:
        assert "username" in u


@pytest.mark.asyncio
async def test_get_groups(con):
    groups = await con.meta.get_groups()
    assert isinstance(groups, list)
    for g in groups:
        assert "groupname" in g


# ---------------------------------------------------------------------------
# Searching
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_objects(con):
    results = await con.meta.search_objects("%")
    assert isinstance(results, list)
    if len(results) > 0:
        for r in results:
            assert "object_type" in r
            assert "schema" in r
            assert "object_name" in r
            assert r["object_type"] in ("TABLE", "VIEW", "PROCEDURE")


# ---------------------------------------------------------------------------
# Column metadata on a temp table (detailed values)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_columns_detailed(con):
    cur = con.cursor()
    await cur.execute("""
        CREATE TEMP TABLE T_META_COL_TEST (
            id INTEGER NOT NULL,
            name VARCHAR(100),
            amount NUMERIC(15, 4),
            ts TIMESTAMP
        ) DISTRIBUTE ON RANDOM
    """)
    try:
        cols = await con.meta.get_columns("T_META_COL_TEST")

        assert len(cols) == 4
        assert cols[0]["column_name"] == "ID"
        assert cols[0]["ordinal"] == 1
        assert cols[0]["nullable"] == "N"

        assert cols[1]["column_name"] == "NAME"
        assert cols[1]["ordinal"] == 2
        assert cols[1]["nullable"] == "Y"

        assert cols[2]["column_name"] == "AMOUNT"
        assert cols[2]["ordinal"] == 3

        assert cols[3]["column_name"] == "TS"
        assert cols[3]["ordinal"] == 4
    finally:
        await cur.execute("DROP TABLE T_META_COL_TEST IF EXISTS")


# ---------------------------------------------------------------------------
# Query history (best-effort; depends on history being enabled)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_query_history(con):
    results = await con.meta.get_query_history(limit=5)
    assert isinstance(results, list)
    # May be empty if history collection is disabled — that's OK


@pytest.mark.asyncio
async def test_metadata_ddl_helpers_round_trip_catalog_objects(con):
    """Generate and execute DDL for objects created on a real Netezza DB."""
    cur = con.cursor()
    con.autocommit = True
    suffix = uuid.uuid4().hex[:8].upper()
    table = f"PY_META_{suffix}_T"
    view = f"PY_META_{suffix}_V"
    procedure = f"PY_META_{suffix}_P"
    synonym = f"PY_META_{suffix}_S"
    external = f"PY_META_{suffix}_E"
    schema = await con.meta.get_current_schema()
    assert schema

    create_statements = [
        (
            f"CREATE TABLE {table} (id INTEGER, name VARCHAR(30)) "
            "DISTRIBUTE ON (id)"
        ),
        f"CREATE VIEW {view} AS SELECT id, name FROM {table}",
        (
            f"CREATE OR REPLACE PROCEDURE {procedure}() RETURNS INTEGER "
            "EXECUTE AS OWNER LANGUAGE NZPLSQL AS BEGIN_PROC "
            "BEGIN RETURN 1; END; END_PROC;"
        ),
        f"CREATE SYNONYM {synonym} FOR {table}",
        (
            f"CREATE EXTERNAL TABLE {external}(id INTEGER) USING "
            f"(DATAOBJECT('/tmp/{external}.txt') REMOTESOURCE 'jdbc')"
        ),
    ]
    drop_statements = [
        f"DROP VIEW {view}",
        f"DROP PROCEDURE {procedure}()",
        f"DROP SYNONYM {synonym}",
        f"DROP TABLE {external}",
        f"DROP TABLE {table}",
    ]
    try:
        for statement in create_statements:
            await cur.execute(statement)

        table_ddl = await con.meta.get_table_ddl(table, schema=schema)
        view_ddl = await con.meta.get_view_ddl(view, schema=schema)
        procedure_ddl = await con.meta.get_procedure_ddl(procedure, schema=schema)
        synonym_ddl = await con.meta.get_synonym_ddl(synonym, schema=schema)
        external_ddl = await con.meta.get_external_table_ddl(external, schema=schema)

        assert "CREATE TABLE" in table_ddl
        assert "CREATE OR REPLACE VIEW" in view_ddl
        assert "CREATE OR REPLACE PROCEDURE" in procedure_ddl
        assert "CREATE SYNONYM" in synonym_ddl
        assert "CREATE EXTERNAL TABLE" in external_ddl

        batch = await con.meta.export_database_ddl(
            schema=schema,
            tables=[table],
            views=[view],
            procedures=[procedure],
            external_tables=[external],
            synonyms=[synonym],
        )
        assert batch["object_count"] == 5
        assert batch["errors"] == []

        for statement in drop_statements:
            await cur.execute(statement)
        for ddl in (table_ddl, view_ddl, procedure_ddl, synonym_ddl, external_ddl):
            await cur.execute(ddl)
    finally:
        for statement in drop_statements:
            try:
                await cur.execute(statement)
            except Exception:
                pass


@pytest.mark.asyncio
async def test_additional_catalog_helpers(con):
    table = f"PY_META_SEARCH_{uuid.uuid4().hex[:8].upper()}"
    cur = con.cursor()
    await cur.execute(f"CREATE TABLE {table} (id INTEGER) DISTRIBUTE ON (id)")
    try:
        schema = await con.meta.get_current_schema()
        assert schema
        functions = await con.meta.get_functions(schema=schema)
        constraints = await con.meta.get_constraints(schema=schema)
        distribution_keys = await con.meta.get_all_distribution_keys(schema=schema)
        organize_keys = await con.meta.get_organize_keys(schema=schema)
        details = await con.meta.get_object_details(schema=schema)
        matches = await con.meta.search_objects_detailed(table, schema=schema)

        assert isinstance(functions, list)
        assert isinstance(constraints, list)
        assert any(row["table_name"] == table for row in distribution_keys)
        assert isinstance(organize_keys, list)
        assert any(row["object_name"] == table for row in details)
        assert any(row["object_name"] == table for row in matches)
    finally:
        await cur.execute(f"DROP TABLE {table}")
