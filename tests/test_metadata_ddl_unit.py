"""Unit coverage for catalog-backed DDL reconstruction helpers."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

import pytest

from nzpy_extended._ddl import build_external_table_ddl, build_synonym_ddl
from nzpy_extended._extab import ExternalTableManager
from nzpy_extended._metadata_api import ConnectionMetadataProvider
from nzpy_extended.protocol import (
    EXTERNAL_TABLE_STREAM_MARKER,
    EXTAB_SOCK_DATA,
    EXTAB_SOCK_DONE,
)
from nzpy_extended.utils import i_pack, i_unpack

pytestmark = pytest.mark.unit


def test_build_external_table_ddl_quotes_options_and_columns() -> None:
    ddl = build_external_table_ddl(
        "DB",
        "Admin",
        "External data",
        "/tmp/data's.csv",
        [{"name": "Mixed Column", "full_type": "VARCHAR(20)", "not_null": True}],
        {"delim": "|", "maxerrors": 5, "compress": "TRUE", "fillrecord": "false"},
    )

    assert 'CREATE EXTERNAL TABLE DB."Admin"."External data"' in ddl
    assert '"Mixed Column" VARCHAR(20) NOT NULL' in ddl
    assert "DATAOBJECT('/tmp/data''s.csv')" in ddl
    assert "DELIMITER '|'" in ddl
    assert "MAXERRORS 5" in ddl
    assert "COMPRESS true" in ddl
    assert "FILLRECORD false" in ddl


def test_build_external_table_ddl_rejects_missing_columns() -> None:
    with pytest.raises(ValueError, match="has no columns"):
        build_external_table_ddl("DB", "ADMIN", "EMPTY", None, [], {})


def test_build_synonym_ddl_qualifies_reference_and_escapes_comment() -> None:
    ddl = build_synonym_ddl(
        "DB", "ADMIN", "ALIAS", "TARGET", "owner's alias", "OTHER_DB", "DATA"
    )

    assert "CREATE SYNONYM DB.ADMIN.ALIAS FOR OTHER_DB.DATA.TARGET;" in ddl
    assert "COMMENT ON SYNONYM ALIAS IS 'owner''s alias';" in ddl


class _StubMetadata(ConnectionMetadataProvider):
    def __init__(self, responses: list[list[dict[str, Any]]]) -> None:
        super().__init__(object())  # type: ignore[arg-type]
        self.responses = responses
        self.queries: list[str] = []

    async def _query_dicts(self, sql: str) -> list[dict[str, Any]]:
        self.queries.append(sql)
        return self.responses.pop(0)

    async def get_current_database(self) -> str:
        return "DB"


class _BatchStubMetadata(_StubMetadata):
    def __init__(self) -> None:
        super().__init__([])
        self.batch_calls: list[tuple[str, dict[str, Any]]] = []

    async def get_tables_ddl(self, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    async def get_views_ddl(self, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    async def get_procedures_ddl(self, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    async def get_external_tables_ddl(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.batch_calls.append(("external", kwargs))
        return [
            {
                "schema": "ADMIN",
                "external_table_name": "EXT_SAMPLE",
                "ddl": "CREATE EXTERNAL TABLE DB.ADMIN.EXT_SAMPLE (...);",
                "error": None,
            }
        ]

    async def get_synonyms_ddl(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.batch_calls.append(("synonym", kwargs))
        return [
            {
                "schema": "ADMIN",
                "synonym_name": "ALIAS",
                "ddl": "CREATE SYNONYM DB.ADMIN.ALIAS FOR DB.ADMIN.TARGET;",
                "error": None,
            }
        ]


@pytest.mark.asyncio
async def test_external_table_ddl_reads_options_and_column_metadata() -> None:
    meta = _StubMetadata(
        [
            [
                {
                    "schema": "ADMIN",
                    "table_name": "EXT_TEST",
                    "data_object": "/tmp/input.csv",
                    "delim": "|",
                    "maxerrors": 7,
                    "compress": "f",
                }
            ],
            [
                {
                    "column_name": "ID",
                    "full_type": "INTEGER",
                    "not_null": "t",
                }
            ],
        ]
    )

    ddl = await meta.get_external_table_ddl("EXT_TEST", schema="ADMIN")

    assert "CREATE EXTERNAL TABLE DB.ADMIN.EXT_TEST" in ddl
    assert "ID INTEGER NOT NULL" in ddl
    assert "DELIMITER '|'" in ddl
    assert "MAXERRORS 7" in ddl
    assert "COMPRESS false" in ddl
    assert "_v_external E JOIN _v_extobject X ON E.relid = X.objid" in meta.queries[0]


@pytest.mark.asyncio
async def test_synonym_ddl_reconstructs_qualified_target() -> None:
    meta = _StubMetadata(
        [
            [
                {
                    "schema": "ADMIN",
                    "synonym_name": "ALIAS",
                    "referenced_object": "TARGET",
                    "ref_database": "OTHER_DB",
                    "ref_schema": "DATA",
                    "description": None,
                }
            ]
        ]
    )

    ddl = await meta.get_synonym_ddl("ALIAS", schema="ADMIN")

    assert ddl == "CREATE SYNONYM DB.ADMIN.ALIAS FOR OTHER_DB.DATA.TARGET;"


@pytest.mark.asyncio
async def test_procedure_ddl_requires_signature_for_overloads() -> None:
    meta = _StubMetadata(
        [[
            {"schema": "ADMIN", "proc_name": "OVERLOADED", "signature": "INTEGER"},
            {"schema": "ADMIN", "proc_name": "OVERLOADED", "signature": "VARCHAR"},
        ]]
    )

    with pytest.raises(ValueError, match="multiple overloads"):
        await meta.get_procedure_ddl("OVERLOADED", schema="ADMIN")


@pytest.mark.asyncio
async def test_export_database_ddl_includes_external_tables_and_synonyms() -> None:
    meta = _BatchStubMetadata()

    result = await meta.export_database_ddl(
        schema="ADMIN",
        external_table_pattern="EXT%",
        synonym_pattern="AL%",
    )

    assert result["object_count"] == 2
    assert "-- Object Types: TABLE, VIEW, PROCEDURE, EXTERNAL TABLE, SYNONYM" in result["ddl"]
    assert "CREATE EXTERNAL TABLE DB.ADMIN.EXT_SAMPLE" in result["ddl"]
    assert "CREATE SYNONYM DB.ADMIN.ALIAS" in result["ddl"]
    assert meta.batch_calls == [
        ("external", {"schema": "ADMIN", "table_pattern": "EXT%", "tables": None}),
        ("synonym", {"schema": "ADMIN", "synonym_pattern": "AL%", "synonyms": None}),
    ]


def test_export_database_ddl_keeps_legacy_positional_arguments() -> None:
    import inspect

    parameters = list(inspect.signature(ConnectionMetadataProvider.export_database_ddl).parameters)
    assert parameters[10:12] == ["output_path", "database"]


class _TransferConnection:
    def __init__(self, source: Any, block_size: int) -> None:
        handshake = (
            b"\x00" * 4
            + EXTERNAL_TABLE_STREAM_MARKER.encode()
            + b"\x00"
            + i_pack(1)
            + i_pack(0)
            + i_pack(block_size)
        )
        self.incoming = bytearray(handshake)
        self.outgoing = bytearray()
        self._ext_table_source = source
        self._client_encoding = "utf-8"
        self.log = logging.getLogger("test.extab")

    async def _read(self, size: int) -> bytes:
        data = bytes(self.incoming[:size])
        del self.incoming[:size]
        assert len(data) == size
        return data

    async def _write(self, data: bytes | bytearray) -> None:
        self.outgoing.extend(data)

    async def _flush(self) -> None:
        return None


def _read_sent_blocks(outgoing: bytes) -> tuple[list[bytes], int]:
    offset = 4  # client version response
    blocks: list[bytes] = []
    final_status = -1
    while offset < len(outgoing):
        status = i_unpack(outgoing, offset)[0]
        offset += 4
        if status == EXTAB_SOCK_DONE:
            final_status = status
            break
        assert status == EXTAB_SOCK_DATA
        size = i_unpack(outgoing, offset)[0]
        offset += 4
        blocks.append(outgoing[offset:offset + size])
        offset += size
    return blocks, final_status


@pytest.mark.asyncio
async def test_xfer_table_splits_oversized_generator_chunks() -> None:
    payload = b"abcdefghij"
    conn = _TransferConnection(iter([payload]), block_size=4)

    await ExternalTableManager(conn).xferTable()  # type: ignore[arg-type]

    blocks, final_status = _read_sent_blocks(conn.outgoing)
    assert blocks == [b"abcd", b"efgh", b"ij"]
    assert all(len(block) <= 4 for block in blocks)
    assert final_status == EXTAB_SOCK_DONE


@pytest.mark.asyncio
async def test_xfer_table_splits_oversized_async_generator_chunks() -> None:
    payload = b"abcdefghij"

    async def source() -> AsyncIterator[bytes]:
        yield payload

    conn = _TransferConnection(source(), block_size=4)

    await ExternalTableManager(conn).xferTable()  # type: ignore[arg-type]

    blocks, final_status = _read_sent_blocks(conn.outgoing)
    assert blocks == [b"abcd", b"efgh", b"ij"]
    assert final_status == EXTAB_SOCK_DONE
