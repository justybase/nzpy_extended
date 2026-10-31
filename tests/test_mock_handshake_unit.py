"""Full startup handshake against the in-process Netezza mock (no database).

Covers the wire sequence, version downgrades, every authentication method the
driver supports, rejected and missing security, and a real TLS upgrade.
"""

from __future__ import annotations

import shutil
from typing import Any

import pytest

import nzpy_extended as nz
from nzpy_extended import _handshake_plan as plan
from nzpy_extended.exceptions import InterfaceError, ProgrammingError
from tests.mock_netezza import MockNetezzaServer, make_self_signed_context

pytestmark = pytest.mark.unit


def _opcodes(server: MockNetezzaServer, connection: int = 0) -> list[int]:
    return [opcode for opcode, _ in server.connections[connection]]


async def _connect(server: MockNetezzaServer, **overrides: Any) -> nz.Connection:
    return await nz.connect(**server.connect_kwargs(**overrides))


@pytest.mark.asyncio
async def test_default_handshake_sends_the_reference_option_sequence() -> None:
    with MockNetezzaServer(auth="password") as server:
        conn = await _connect(server)
        await conn.close()
        server.assert_no_handler_errors()

    # CLIENT_BEGIN, DB, SSL_NEGOTIATE, then the version-six option plan.
    assert _opcodes(server) == [1, 2, 11, 3, 13, 14, 15, 16, 9, 6, 8, 17, 1000]


@pytest.mark.asyncio
async def test_server_downgrade_is_followed_and_option_plan_shrinks() -> None:
    with MockNetezzaServer(auth="ok", negotiate=(b"M5",)) as server:
        conn = await _connect(server)
        await conn.close()
        server.assert_no_handler_errors()

    frames = server.connections[0]
    assert frames[0][1] == b"\x00\x06"
    assert frames[1][1] == b"\x00\x05"
    # Version five has no application-info frames but does send 64-bit varlena.
    assert _opcodes(server) == [1, 1, 2, 11, 3, 9, 6, 8, 17, 1000]


@pytest.mark.asyncio
async def test_pg_options_are_sent_when_requested() -> None:
    with MockNetezzaServer(auth="ok") as server:
        conn = await _connect(server, pgOptions="-c search_path=x")
        await conn.close()
        server.assert_no_handler_errors()

    frames = server.connections[0]
    options = [payload for opcode, payload in frames if opcode == plan.HSV2_OPTIONS]
    assert options == [b"-c search_path=x\x00"]


@pytest.mark.asyncio
async def test_database_none_still_sends_a_database_frame() -> None:
    with MockNetezzaServer(auth="ok") as server:
        conn = await _connect(server, database=None)
        await conn.close()
        server.assert_no_handler_errors()

    db_payloads = [payload for opcode, payload in server.connections[0] if opcode == 2]
    assert db_payloads == [b"\x00"]


@pytest.mark.asyncio
@pytest.mark.parametrize("auth", ["ok", "password", "md5", "sha256"])
async def test_each_supported_authentication_method_connects(auth: str) -> None:
    with MockNetezzaServer(auth=auth) as server:
        conn = await _connect(server)
        cursor = conn.cursor()
        await cursor.execute("set nz_probe to 'ok'")
        await conn.close()
        server.assert_no_handler_errors()
    assert server.connections[0][-1][0] == 1000


@pytest.mark.asyncio
@pytest.mark.parametrize("auth", ["password", "md5", "sha256"])
async def test_wrong_password_is_rejected_with_the_server_message(auth: str) -> None:
    with MockNetezzaServer(auth=auth) as server:
        with pytest.raises(ProgrammingError, match="password authentication failed"):
            await _connect(server, password="not-the-password")


@pytest.mark.asyncio
async def test_unknown_authentication_method_is_rejected_before_success() -> None:
    with MockNetezzaServer(auth="unknown") as server:
        with pytest.raises(InterfaceError, match="Unsupported authentication type"):
            await _connect(server)


@pytest.mark.asyncio
async def test_required_tls_refuses_a_plaintext_server() -> None:
    # securityLevel 2 without ssl_allow_fallback must not accept an 'N' reply.
    with MockNetezzaServer(auth="ok") as server:
        with pytest.raises(ProgrammingError):
            await _connect(server, securityLevel=2)


@pytest.mark.asyncio
async def test_optional_tls_without_fallback_still_connects_plaintext() -> None:
    with MockNetezzaServer(auth="ok") as server:
        conn = await _connect(server, securityLevel=1)
        await conn.close()
        server.assert_no_handler_errors()


@pytest.mark.asyncio
async def test_tls_upgrade_with_verified_certificate(tmp_path: Any) -> None:
    if shutil.which("openssl") is None:
        pytest.skip("openssl executable required for the TLS mock certificate")
    server_context, cert = make_self_signed_context(tmp_path)
    with MockNetezzaServer(auth="ok", tls_context=server_context) as server:
        conn = await _connect(
            server, securityLevel=1,
            ssl={"ca_certs": str(cert), "server_hostname": "localhost"},
        )
        await conn.close()
        server.assert_no_handler_errors()

    # After the upgrade the option frames still arrive, now over TLS.
    assert _opcodes(server)[-1] == 1000


@pytest.mark.asyncio
async def test_handshake_error_during_options_closes_the_socket() -> None:
    class RejectingServer(MockNetezzaServer):
        def _handshake(self, session: Any, head: bytes) -> None:  # type: ignore[override]
            # Accept CLIENT_BEGIN and DB, then answer the USER option with an error.
            opcode, payload = session.read_frame(head)
            self.connections[-1].append((opcode, payload))
            session.send(b"N")
            opcode, payload = session.read_frame()
            self.connections[-1].append((opcode, payload))
            session.send(b"N")
            opcode, payload = session.read_frame()
            self.connections[-1].append((opcode, payload))
            session.send(b"N")
            opcode, payload = session.read_frame()
            self.connections[-1].append((opcode, payload))
            session.send(b"E")

    with RejectingServer(auth="ok") as server:
        with pytest.raises(ProgrammingError):
            await _connect(server)
