"""The server may only read or write client files that the statement names."""

from __future__ import annotations

import logging
import struct
from pathlib import Path
from typing import Any

import pytest

from nzpy_extended._extab import ExternalTableManager, statement_file_literals
from nzpy_extended.exceptions import OperationalError
from nzpy_extended.protocol import EXTAB_SOCK_DATA, EXTAB_SOCK_DONE, EXTAB_SOCK_ERROR

pytestmark = [pytest.mark.full, pytest.mark.unit]


def be32(value: int) -> bytes:
    return struct.pack(">i", value)


class FakeConn:
    """The subset of Connection that ExternalTableManager uses."""

    def __init__(self, incoming: bytes, paths: frozenset[str]) -> None:
        self._incoming = bytearray(incoming)
        self.sent = bytearray()
        self._client_encoding = "utf8"
        self._ext_table_source: Any = None
        self._ext_table_failure: BaseException | None = None
        self._extab_paths = paths
        self.error: Any = None
        self.log = logging.getLogger("test.extab")

    async def _read(self, n: int) -> bytes:
        chunk = bytes(self._incoming[:n])
        del self._incoming[:n]
        assert len(chunk) == n, "the manager read past the server's message"
        return chunk

    async def _write(self, data: bytes | bytearray) -> None:
        self.sent += data

    async def _flush(self) -> None:
        pass


def _import_request(filename: str) -> bytes:
    """Server side of an 'l' (send me this file) request, after its header."""
    return (b"\x00" * 4 + filename.encode() + b"\x00" + be32(1)  # host version
            + be32(0) + be32(4096))  # format, block size


def test_statement_literals_are_collected_only_for_external_statements() -> None:
    sql = ("INSERT INTO T SELECT * FROM EXTERNAL '/data/in.csv' "
           "USING (REMOTESOURCE 'python' LOGDIR '/tmp/it''s')")
    assert statement_file_literals(sql) == {"/data/in.csv", "python", "/tmp/it's"}
    assert statement_file_literals("SELECT '/etc/passwd'") == frozenset()


async def test_import_of_a_file_the_statement_does_not_name_is_refused(tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"TOP-SECRET")
    conn = FakeConn(_import_request(str(secret)), frozenset({str(tmp_path / "other.csv")}))
    await ExternalTableManager(conn).xferTable()  # type: ignore[arg-type]
    assert b"TOP-SECRET" not in conn.sent
    assert be32(EXTAB_SOCK_ERROR) in conn.sent
    assert isinstance(conn.error, OperationalError)
    assert "refused" in str(conn.error)


async def test_import_of_the_named_file_is_sent(tmp_path: Path) -> None:
    data = tmp_path / "in.csv"
    data.write_bytes(b"1|a\n")
    conn = FakeConn(_import_request(str(data)), frozenset({str(data)}))
    await ExternalTableManager(conn).xferTable()  # type: ignore[arg-type]
    assert b"1|a\n" in conn.sent
    assert conn.sent.endswith(be32(EXTAB_SOCK_DONE))
    assert conn.error is None


def _log_file_blocks(payload: bytes) -> bytes:
    return be32(len(payload)) + payload + be32(0)


@pytest.mark.parametrize("name", ["../escape", "/abs/path", "sub/dir"])
async def test_log_file_names_with_paths_are_refused(tmp_path: Path, name: str) -> None:
    conn = FakeConn(_log_file_blocks(b"LOG"), frozenset({str(tmp_path)}))
    written = await ExternalTableManager(conn).getFileFromBE(str(tmp_path), name, 1)  # type: ignore[arg-type]
    assert written is False
    assert list(tmp_path.iterdir()) == []
    assert not conn._incoming, "the log data must still be consumed"


async def test_log_file_outside_the_statement_logdir_is_refused(tmp_path: Path) -> None:
    conn = FakeConn(_log_file_blocks(b"LOG"), frozenset())
    written = await ExternalTableManager(conn).getFileFromBE(str(tmp_path), "T.log", 1)  # type: ignore[arg-type]
    assert written is False
    assert list(tmp_path.iterdir()) == []
    assert not conn._incoming


async def test_log_file_in_the_statement_logdir_is_written(tmp_path: Path) -> None:
    conn = FakeConn(_log_file_blocks(b"LOG"), frozenset({str(tmp_path)}))
    written = await ExternalTableManager(conn).getFileFromBE(str(tmp_path), "T.log", 2)  # type: ignore[arg-type]
    assert written is True
    assert (tmp_path / "T.log.nzbad").read_bytes() == b"LOG"


def test_sock_data_constant_is_distinct() -> None:
    # Guards the assertions above: the error status must not equal a data status.
    assert EXTAB_SOCK_ERROR not in (EXTAB_SOCK_DATA, EXTAB_SOCK_DONE)
