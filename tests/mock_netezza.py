"""Offline Netezza wire-protocol server for tests.

The server speaks the framing the driver expects: option frames acknowledged
with ``N``, the authentication exchange, ``K``/``S``/``Z`` connection
completion, then simple ``P`` queries answered by a responder. Every
ReadyForQuery is followed by four NUL bytes of appliance padding; the golden
fixtures omit that padding, so the server adds it between responses.

Responders receive the statement text and a :class:`ServerSession` and must
send the complete response, ending with :func:`ready`.
"""

from __future__ import annotations

import base64
import hashlib
import socket
import ssl
import struct
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

NUL = b"\x00"
PADDING = b"\x00" * 4
CANCEL_CODE = 80877102
CLIENT_DONE = 1000
FIXTURE_DIR = Path(__file__).parent / "data" / "wire"

Responder = Callable[[str, "ServerSession"], None]


def i32(value: int) -> bytes:
    return struct.pack(">i", value)


def message(code: bytes, payload: bytes = b"") -> bytes:
    """Backend message: type, four reserved bytes, big-endian length, payload."""
    return code + b"\x00" * 4 + i32(len(payload)) + payload


def ready() -> bytes:
    """ReadyForQuery: type plus four bytes, followed by appliance padding."""
    return b"Z" + b"\x00" * 4 + PADDING


def command_complete(tag: bytes) -> bytes:
    return message(b"C", tag + NUL)


def error_response(text: str) -> bytes:
    """ErrorResponse as a Netezza appliance sends it: plain text, no SQLSTATE."""
    return message(b"E", b"ERROR:  " + text.encode() + b"\n" + NUL)


def notice(text: str) -> bytes:
    """NoticeResponse as a Netezza appliance sends it: plain text."""
    return message(b"N", b"NOTICE:  " + text.encode() + b"\n" + NUL)


@dataclass(frozen=True)
class Column:
    name: str
    type_oid: int = 25
    type_size: int = -1
    type_modifier: int = -1


def row_description(columns: Sequence[Column]) -> bytes:
    payload = struct.pack(">H", len(columns))
    for column in columns:
        payload += column.name.encode() + NUL
        payload += struct.pack(">ihic", column.type_oid, column.type_size,
                               column.type_modifier, b"\x00")
    return message(b"T", payload)


def data_row(values: Sequence[bytes | None]) -> bytes:
    """DataRow: MSB-first presence bitmap (bit set = value present), then cells."""
    bitmap = bytearray((len(values) + 7) // 8)
    cells = b""
    for index, value in enumerate(values):
        if value is None:
            continue
        bitmap[index >> 3] |= 0x80 >> (index & 7)
        cells += i32(4 + len(value)) + value
    return message(b"D", bytes(bitmap) + cells)


def text_result(columns: Sequence[Column], rows: Sequence[Sequence[bytes | None]],
                tag: bytes | None = None) -> bytes:
    """One complete text result set, without the trailing ReadyForQuery."""
    out = row_description(columns)
    for row in rows:
        out += data_row(row)
    out += command_complete(tag or f"SELECT {len(rows)}".encode())
    return out


def read_wire_records(name: str) -> list[tuple[str, bytes]]:
    """Load ``(sql, response)`` records from a golden fixture (see its README)."""
    data = (FIXTURE_DIR / f"{name}.bin").read_bytes()
    if data[:8] != b"NZWIRE01":
        raise ValueError(f"{name}: bad fixture magic")
    offset = 8
    (count,) = struct.unpack_from("<I", data, offset)
    offset += 4
    records: list[tuple[str, bytes]] = []
    for _ in range(count):
        (sql_len,) = struct.unpack_from("<I", data, offset)
        offset += 4
        sql = data[offset:offset + sql_len].decode("utf-8")
        offset += sql_len
        (response_len,) = struct.unpack_from("<I", data, offset)
        offset += 4
        response = data[offset:offset + response_len]
        offset += response_len
        if response[-5:-4] != b"Z":
            raise ValueError(f"{name}: response is not ReadyForQuery-terminated")
        records.append((sql, response))
    if offset != len(data):
        raise ValueError(f"{name}: trailing bytes")
    return records


class ClientGone(Exception):
    """The client closed the socket."""


# How a vanished peer surfaces on Windows (WSAECONNRESET, WSAESHUTDOWN, ...).
_PEER_GONE = (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)


class ServerSession:
    """One accepted connection, as seen by a responder."""

    def __init__(self, server: MockNetezzaServer, sock: socket.socket, pid: int, key: int) -> None:
        self.server = server
        self.sock = sock
        self.pid = pid
        self.key = key
        self.cancelled = threading.Event()
        self.statements: list[str] = []

    def recv_exact(self, size: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < size:
            try:
                piece = self.sock.recv(size - len(chunks))
            except _PEER_GONE as exc:
                # Windows reports a client close (or our own shutdown) as an
                # error where POSIX returns b"".
                raise ClientGone() from exc
            if not piece:
                raise ClientGone()
            chunks.extend(piece)
        return bytes(chunks)

    def send(self, data: bytes | bytearray) -> None:
        """Send bytes, split into ``server.chunk``-sized writes when configured."""
        step = self.server.chunk or len(data) or 1
        try:
            for start in range(0, len(data), step):
                self.sock.sendall(data[start:start + step])
        except _PEER_GONE as exc:
            raise ClientGone() from exc

    def read_frame(self, head: bytes = b"") -> tuple[int, bytes]:
        """Client option frame: ``[len(4)][opcode(2)][payload]`` where len counts itself.

        ``head`` holds bytes already read from the frame, such as the first eight
        bytes consumed to tell a cancel request from a handshake frame.
        """
        if len(head) < 6:
            head += self.recv_exact(6 - len(head))
        (length,) = struct.unpack_from(">i", head, 0)
        opcode = struct.unpack_from(">h", head, 4)[0]
        remaining = length - len(head)
        if remaining < 0:
            raise AssertionError(f"frame length {length} is shorter than its header")
        body = head[6:] + (self.recv_exact(remaining) if remaining else b"")
        return opcode, body

    def read_query(self) -> str | None:
        """Simple query: ``P`` + four bytes + SQL + NUL. ``None`` on a clean close."""
        try:
            head = self.recv_exact(5)
        except ClientGone:
            return None
        if head[:1] != b"P":
            raise AssertionError(f"mock expected a simple query, got {head[:1]!r}")
        sql = bytearray()
        while True:
            byte = self.recv_exact(1)
            if byte == NUL:
                break
            sql.extend(byte)
        statement = sql.decode("utf-8")
        self.statements.append(statement)
        return statement

    def reply(self, *parts: bytes) -> None:
        self.send(b"".join(parts))

    def wait_cancel(self, timeout: float) -> bool:
        return self.cancelled.wait(timeout)


class MockNetezzaServer:
    """Threaded TCP server bound to 127.0.0.1 on an ephemeral port.

    ``auth`` is one of ``ok``, ``password``, ``md5``, ``sha256`` or ``unknown``.
    ``negotiate`` lists replies to successive CLIENT_BEGIN frames (for example
    ``(b"M5",)`` downgrades once); ``N`` is the default.
    """

    SALT = b"\x12\x34"

    def __init__(
        self,
        *,
        user: str = "admin",
        password: str = "secret",
        database: str = "JUST_DATA",
        auth: str = "password",
        negotiate: Sequence[bytes] = (),
        responder: Responder | None = None,
        chunk: int | None = None,
        tls_context: ssl.SSLContext | None = None,
        backend_pid: int = 5857,
        backend_key: int = -2_092_017_624,
    ) -> None:
        self.user = user
        self.password = password
        self.database = database
        self.auth = auth
        self.negotiate = list(negotiate)
        self.responder: Responder = responder or default_responder
        self.chunk = chunk
        self.tls_context = tls_context
        self.backend_pid = backend_pid
        self.backend_key = backend_key
        self.connections: list[list[tuple[int, bytes]]] = []
        self.cancels: list[tuple[int, int]] = []
        self.errors: list[BaseException] = []
        self.sessions: list[ServerSession] = []
        self._listener: socket.socket | None = None
        self._threads: list[threading.Thread] = []
        self._open: list[socket.socket] = []
        self._lock = threading.Lock()
        self._closing = threading.Event()
        self._accept_thread: threading.Thread | None = None

    # -- lifecycle -----------------------------------------------------------

    def __enter__(self) -> MockNetezzaServer:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @property
    def port(self) -> int:
        assert self._listener is not None, "server not started"
        return int(self._listener.getsockname()[1])

    def start(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(16)
        listener.settimeout(0.1)
        self._listener = listener
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._accept_thread.start()

    def close(self) -> None:
        self._closing.set()
        with self._lock:
            open_sockets = list(self._open)
        for sock in open_sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        if self._listener is not None:
            self._listener.close()
        if self._accept_thread is not None:
            self._accept_thread.join(timeout=5)
        for thread in self._threads:
            thread.join(timeout=5)

    def connect_kwargs(self, **overrides: Any) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "host": "127.0.0.1",
            "port": self.port,
            "user": self.user,
            "password": self.password,
            "database": self.database,
        }
        kwargs.update(overrides)
        return kwargs

    def assert_no_handler_errors(self) -> None:
        if self.errors:
            raise AssertionError(f"mock server handler failed: {self.errors[0]!r}")

    # -- accept/serve --------------------------------------------------------

    def _accept_loop(self) -> None:
        assert self._listener is not None
        while not self._closing.is_set():
            try:
                sock, _ = self._listener.accept()
            except (socket.timeout, TimeoutError):
                continue
            except OSError:
                return
            sock.settimeout(10)
            with self._lock:
                self._open.append(sock)
            thread = threading.Thread(target=self._serve, args=(sock,), daemon=True)
            self._threads.append(thread)
            thread.start()

    def _serve(self, sock: socket.socket) -> None:
        session = ServerSession(self, sock, pid=0, key=0)
        try:
            head = session.recv_exact(8)
            if struct.unpack_from(">i", head, 4)[0] == CANCEL_CODE:
                self._serve_cancel(session, head)
                return
            self.connections.append([])
            self._handshake(session, head)
            self._queries(session)
        except ClientGone:
            pass
        except BaseException as exc:  # recorded for the test to assert on
            self.errors.append(exc)
        finally:
            with self._lock:
                if sock in self._open:
                    self._open.remove(sock)
            try:
                sock.close()
            except OSError:
                pass

    def _serve_cancel(self, session: ServerSession, head: bytes) -> None:
        body = session.recv_exact(8)
        pid, key = struct.unpack(">ii", body)
        self.cancels.append((pid, key))
        for other in list(self.sessions):
            if other.pid == pid and other.key == key:
                other.cancelled.set()

    def _handshake(self, session: ServerSession, head: bytes) -> None:
        frames = self.connections[-1]
        # The first frame arrived as ``head`` (length + opcode + version word).
        opcode, payload = session.read_frame(head)
        frames.append((opcode, payload))
        negotiate = list(self.negotiate)
        while opcode == 1:
            reply = negotiate.pop(0) if negotiate else b"N"
            session.send(reply)
            if reply == b"N":
                break
            # Downgrade: the driver sends CLIENT_BEGIN again with a lower version.
            opcode, payload = session.read_frame()
            frames.append((opcode, payload))

        opcode, payload = session.read_frame()  # HSV2_DB
        frames.append((opcode, payload))
        session.send(b"N")

        opcode, payload = session.read_frame()  # HSV2_SSL_NEGOTIATE
        frames.append((opcode, payload))
        if self.tls_context is not None:
            session.send(b"S")
            opcode, payload = session.read_frame()  # HSV2_SSL_CONNECT
            frames.append((opcode, payload))
            wrapped = self.tls_context.wrap_socket(session.sock, server_side=True)
            session.sock = wrapped
            session.send(b"N")
        else:
            session.send(b"N")

        while True:
            opcode, payload = session.read_frame()
            frames.append((opcode, payload))
            if opcode == CLIENT_DONE:
                break
            session.send(b"N")

        self._authenticate(session, payload_user=self._user_from(frames))
        self.sessions.append(session)
        session.reply(
            b"K" + b"\x00" * 4 + i32(12) + i32(self.backend_pid) + i32(self.backend_key),
            message(b"S", b"client_encoding\x00UTF8\x00"),
            ready(),
        )
        session.pid = self.backend_pid
        session.key = self.backend_key

    @staticmethod
    def _user_from(frames: list[tuple[int, bytes]]) -> str:
        for opcode, payload in frames:
            if opcode == 3:  # HSV2_USER
                return payload.rstrip(NUL).decode("utf-8")
        return ""

    def _authenticate(self, session: ServerSession, payload_user: str) -> None:
        mode = self.auth
        if mode == "ok":
            session.reply(b"R" + i32(0))
            return
        if mode == "unknown":
            session.reply(b"R" + i32(99))
            return
        if mode == "password":
            session.reply(b"R" + i32(3))
            expected = self.password.encode("utf-8") + NUL
        elif mode == "md5":
            session.reply(b"R" + i32(5) + self.SALT)
            expected = self._hashed(hashlib.md5)
        elif mode == "sha256":
            session.reply(b"R" + i32(6) + self.SALT)
            expected = self._hashed(hashlib.sha256)
        else:
            raise ValueError(f"unknown auth mode {mode!r}")

        length = struct.unpack(">i", session.recv_exact(4))[0]
        received = session.recv_exact(length - 4)
        if received != expected:
            # Connection-complete errors use the plain text form the driver reads.
            session.reply(message(b"E", b"password authentication failed"))
            raise ClientGone()
        session.reply(b"R" + i32(0))
        if payload_user != self.user:
            raise AssertionError(f"driver sent user {payload_user!r}, expected {self.user!r}")

    def _hashed(self, algorithm: Callable[..., Any]) -> bytes:
        digest = algorithm(self.SALT + self.password.encode("utf-8")).digest()
        return base64.standard_b64encode(digest).rstrip(b"=") + NUL

    def _queries(self, session: ServerSession) -> None:
        while True:
            statement = session.read_query()
            if statement is None:
                return
            self.responder(statement, session)


def default_responder(sql: str, session: ServerSession) -> None:
    """Acknowledge the driver's connect-time setup statements and nothing else."""
    lowered = sql.lstrip().lower()
    if lowered.startswith(("set ", "commit", "rollback")):
        session.reply(command_complete(lowered.split()[0].upper().encode()), ready())
        return
    if lowered.startswith("select version()") or lowered.startswith("select ascii("):
        session.reply(text_result([Column("c1")], []), ready())
        return
    if lowered.startswith("select identifier_case") or lowered.startswith("select feature"):
        session.reply(text_result([Column("c1")], []), ready())
        return
    session.reply(error_response(f"mock has no response for: {sql}"), ready())


def statement_responder(responses: dict[str, bytes]) -> Responder:
    """Responder that answers listed statements verbatim (each response ends with ready)."""

    def respond(sql: str, session: ServerSession) -> None:
        if sql in responses:
            session.reply(responses[sql])
        else:
            default_responder(sql, session)

    return respond


def is_connect_setup(sql: str) -> bool:
    """Statements the driver sends while connecting, before any user statement."""
    lowered = sql.lstrip().lower()
    return lowered.startswith((
        "set ", "select version()", "select ascii(", "select feature", "select identifier_case",
    ))


def replay_responder(records: Sequence[tuple[str, bytes]]) -> Responder:
    """Serve fixture records in order; each statement must match the record's SQL.

    Connect-time setup statements are answered by :func:`default_responder`, since
    the golden fixtures start after the session is established.
    """
    queue = list(records)

    def respond(sql: str, session: ServerSession) -> None:
        if is_connect_setup(sql):
            default_responder(sql, session)
            return
        if not queue:
            raise AssertionError(f"unexpected statement: {sql!r}")
        expected_sql, response = queue.pop(0)
        if sql != expected_sql:
            raise AssertionError(f"statement {sql!r} does not match record {expected_sql!r}")
        session.send(response + PADDING)

    return respond


def make_self_signed_context(directory: Path) -> tuple[ssl.SSLContext, Path]:
    """Server TLS context with a self-signed localhost certificate (needs openssl)."""
    import shutil
    import subprocess

    openssl = shutil.which("openssl")
    if openssl is None:
        raise RuntimeError("openssl executable required for the TLS mock")
    cert, key = directory / "cert.pem", directory / "key.pem"
    subprocess.run(
        [openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
         "-keyout", str(key), "-out", str(cert), "-days", "1",
         "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost",
         "-addext", "basicConstraints=critical,CA:TRUE"],
        check=True, capture_output=True,
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    return context, cert


__all__ = [
    "Column",
    "MockNetezzaServer",
    "PADDING",
    "ServerSession",
    "CANCEL_CODE",
    "command_complete",
    "data_row",
    "default_responder",
    "error_response",
    "make_self_signed_context",
    "message",
    "notice",
    "read_wire_records",
    "ready",
    "replay_responder",
    "row_description",
    "statement_responder",
    "text_result",
]
