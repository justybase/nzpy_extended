"""Synchronous startup handshake, run on a blocking socket in a worker thread.

Order: connection-protocol negotiation, database name, security level (TLS
upgrade), option frames, authentication, then connection completion. Frame
contents and authentication responses come from :mod:`._handshake_plan`; this
module moves the bytes and applies the reply rules.
"""

import logging
import socket as _socket
import ssl
from getpass import getuser
from os import getpid, path
from platform import system
from socket import gethostname
from sys import argv
from typing import Any

from ._handshake_plan import (  # noqa: F401  (re-exported protocol constants)
    AUTH_REQ_KRB4, AUTH_REQ_KRB5, AUTH_REQ_CRYPT, AUTH_REQ_MD5, AUTH_REQ_OK,
    AUTH_REQ_PASSWORD, AUTH_REQ_SHA256, CP_VERSION_1, CP_VERSION_2, CP_VERSION_3,
    CP_VERSION_4, CP_VERSION_5, CP_VERSION_6, HSV2_64BIT_VARLENA_ENABLED,
    HSV2_APPNAME, HSV2_CLIENT_BEGIN, HSV2_CLIENT_DONE, HSV2_CLIENT_HOST_NAME,
    HSV2_CLIENT_OS, HSV2_CLIENT_OS_USER, HSV2_CLIENT_TYPE, HSV2_DB, HSV2_HOSTCASE,
    HSV2_INVALID_OPCODE, HSV2_OPTIONS, HSV2_PRIOR_PID, HSV2_PROTOCOL,
    HSV2_REMOTE_PID, HSV2_SSL_CONNECT, HSV2_SSL_NEGOTIATE, HSV2_TTY, HSV2_USER,
    IPS_CLIENT, NPS_CLIENT, NPSCLIENT_TYPE_PYTHON, PG_PROTOCOL_3, PG_PROTOCOL_4,
    PG_PROTOCOL_5, ClientInfo, auth_response, auth_method_name, auth_salt_len,
    check_salt, option_plan, validate_version_downgrade,
)
from .exceptions import InterfaceError
from .protocol import (
    AUTHENTICATION_REQUEST,
    BACKEND_KEY_DATA,
    ERROR_RESPONSE,
    NOTICE_RESPONSE,
    NULL_BYTE,
    PARAMETER_STATUS,
    READY_FOR_QUERY,
)
from .utils import h_pack, i_pack, i_unpack

# Largest length accepted for a connection-complete message; real messages are tiny.
MAX_HANDSHAKE_MESSAGE = 1 << 20
SSL_OFFERED = b"S"
UNSECURED_ACK = b"N"


class _Rejected(Exception):
    """The server refused a step. ``startup`` turns this into a False result."""


class SyncHandshake:

    def __init__(
        self,
        sock: _socket.socket | ssl.SSLSocket,
        ssl_params: dict[str, Any] | None,
        log: logging.Logger
    ) -> None:
        self._hsVersion: int | None = None
        self._protocol1: int | None = None
        self._protocol2: int | None = None
        self._usock = sock
        self._sock = sock.makefile(mode="rwb")
        self.ssl_params: dict[str, Any] | None = ssl_params
        self.log: logging.Logger = log
        self.backend_pid: int | None = None
        self.backend_key: int | None = None
        self.server_client_encoding: str | None = None
        self.last_error: str = ""

        self.guardium_clientOS: str = system()
        self.guardium_clientOSUser: str = getuser()
        self.guardium_clientHostName: str = gethostname()
        self.guardium_applName: str = path.basename(argv[0])

    # -- transport -----------------------------------------------------------

    def _write(self, data: bytes | bytearray) -> None:
        self._sock.write(data)

    def _read(self, n: int) -> bytes:
        return self._sock.read(n)

    def _flush(self) -> None:
        self._sock.flush()  # pyright: ignore[reportAttributeAccessIssue]

    def _send_frame(self, opcode: int, payload: bytes = b"") -> None:
        """Option frame: length prefix, then opcode and payload."""
        body = h_pack(opcode) + payload
        self._write(i_pack(len(body) + 4))
        self._write(body)
        self._flush()

    def _send_response(self, payload: bytes) -> None:
        """Authentication response: length prefix, then the payload (no opcode)."""
        self._write(i_pack(len(payload) + 4))
        self._write(payload)
        self._flush()

    def _expect_ack(self, stage: str) -> None:
        """Every option frame except CLIENT_DONE is answered by exactly one byte."""
        reply = self._read(1)
        if reply == UNSECURED_ACK:
            return
        if reply == ERROR_RESPONSE:
            self.last_error = f"server rejected the handshake at {stage}"
            raise _Rejected()
        raise InterfaceError(
            f"Unexpected reply {reply!r} to handshake option {stage}")

    def _read_length(self) -> int:
        length = int(i_unpack(self._read(4))[0])
        if length < 0 or length > MAX_HANDSHAKE_MESSAGE:
            raise InterfaceError(f"Invalid handshake message length: {length}")
        return length

    def _read_error_text(self, length: int) -> str:
        return self._read(length).decode("utf8", errors="replace")

    # -- steps ---------------------------------------------------------------

    def startup(
        self,
        database: str | bytes | None,
        securityLevel: int,
        user: str | bytes,
        password: str | bytes | None,
        pgOptions: str | None
    ) -> object:
        try:
            if not self.conn_handshake_negotiate(None, None):
                self.log.info("Handshake negotiation unsuccessful")
                return False

            self.log.debug("Sending handshake information to server")
            if not self._send_database(database):
                return False
            if not self.conn_secure_session(securityLevel):
                self.log.warning("Error in conn_secure_session")
                return False
            self._send_options(user, pgOptions)
            if not self.conn_authenticate(password):
                self.log.warning("Error in conn_authenticate")
                return False
            if not self.conn_connection_complete():
                self.log.warning("Error in conn_connection_complete")
                return False
        except _Rejected:
            self.log.warning("Handshake rejected: %s", self.last_error)
            return False

        return self._usock

    def conn_handshake_negotiate(
        self,
        _hsVersion: int | None,
        _protocol2: int | None
    ) -> bool:
        version = CP_VERSION_6
        self.log.debug("Latest-handshake version (conn-protocol) = %s", version)

        while True:
            self.log.debug("sending version: %s", version)
            self._send_frame(HSV2_CLIENT_BEGIN, h_pack(version))
            beresp = self._read(1)
            self.log.debug("Got response: %s", beresp)

            if beresp == UNSECURED_ACK:
                self._hsVersion = version
                self._protocol1 = PG_PROTOCOL_3
                self._protocol2 = PG_PROTOCOL_5
                return True
            if beresp == b"M":
                proposed_bytes = self._read(1)
                try:
                    proposed = int(proposed_bytes)
                except ValueError:
                    proposed = 0
                try:
                    validate_version_downgrade(version, proposed)
                except InterfaceError as exc:
                    self.log.warning("%s", exc)
                    return False
                version = proposed
                continue
            if beresp == ERROR_RESPONSE:
                self.log.warning("Bad attribute value error")
                return False
            self.log.warning("Bad protocol error")
            return False

    def _send_database(self, database: str | bytes | None) -> bool:
        if isinstance(database, str):
            database = database.encode("utf8")
        db = database or b""
        if db:
            self.log.info("Database name: %s", db.decode("utf8", errors="replace"))
        self._send_frame(HSV2_DB, db + NULL_BYTE)

        beresp = self._read(1)
        self.log.info("Backend response: %r", beresp)
        if beresp == UNSECURED_ACK:
            return True
        if beresp == ERROR_RESPONSE:
            self.log.warning("Database rejected by server")
            return False
        self.log.warning("Unknown response to database selection")
        return False

    def _ssl_allow_fallback(self) -> bool:
        if isinstance(self.ssl_params, dict):
            return bool(self.ssl_params.get("ssl_allow_fallback", False))
        return False

    def _ssl_context(self) -> ssl.SSLContext:
        if not isinstance(self.ssl_params, dict):
            self.ssl_params = {}
        ca_certs = self.ssl_params.get("ca_certs")
        context = ssl.create_default_context(cafile=ca_certs)
        if self.ssl_params.get("ssl_verify", True):
            context.check_hostname = True
            context.verify_mode = ssl.CERT_REQUIRED
        else:
            self.log.warning("SSL certificate verification disabled (ssl_verify=False)")
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        return context

    def _upgrade_to_tls(self, context: ssl.SSLContext) -> None:
        server_hostname = (self.ssl_params or {}).get("server_hostname")
        self._sock.close()
        self._usock = context.wrap_socket(self._usock, server_hostname=server_hostname)
        self._sock = self._usock.makefile(mode="rwb")

    def conn_secure_session(self, securityLevel: int) -> bool:
        """Negotiate TLS. A required level (>= 2) never falls back to plaintext."""
        level = securityLevel
        while True:
            self.log.debug("Security Level requested = %s", level)
            self._send_frame(HSV2_SSL_NEGOTIATE, i_pack(level))
            reply = self._read(1)
            self.log.debug("Got response =%s", reply)

            if reply == UNSECURED_ACK:
                if securityLevel >= 2 and not (
                    securityLevel == 2 and self._ssl_allow_fallback()
                ):
                    self.log.warning(
                        "Server offered unsecured session but ssl_allow_fallback "
                        "is not enabled (requested securityLevel=%s)", securityLevel)
                    return False
                self.log.debug("Attempting unsecured session")
                return True
            if reply == ERROR_RESPONSE:
                self.log.warning("Error: connection failed")
                return False
            if reply != SSL_OFFERED:
                self.log.warning("Unexpected reply to SSL negotiation: %r", reply)
                return False

            try:
                context = self._ssl_context()
            except ssl.SSLError:
                if level == 2 and self._ssl_allow_fallback():
                    self.log.debug("Problem establishing secured session")
                    self.log.debug("Attempting unsecured session (ssl_allow_fallback=True)")
                    level = 1
                    continue
                self.log.warning("Problem establishing secured session")
                return False

            self._send_frame(HSV2_SSL_CONNECT, i_pack(level))
            try:
                self._upgrade_to_tls(context)
            except ssl.SSLError:
                self.log.warning("Problem establishing secured session")
                return False
            self.log.info("Secured Connect Success")
            # The acknowledgement for SSL_CONNECT is the first byte after the handshake TLS.
            ack = self._read(1)
            if ack == UNSECURED_ACK:
                return True
            self.log.warning("Unexpected reply after TLS upgrade: %r", ack)
            return False

    def _send_options(self, user: str | bytes, pgOptions: str | None) -> None:
        if self._hsVersion is None:
            raise InterfaceError("handshake options sent before version negotiation")
        user_bytes = user.encode("utf8") if isinstance(user, str) else user
        info = ClientInfo(
            user=user_bytes,
            app_name=self.guardium_applName,
            client_os=self.guardium_clientOS,
            client_host_name=self.guardium_clientHostName,
            os_user=self.guardium_clientOSUser,
            remote_pid=getpid(),
            pg_options=pgOptions.encode("utf8") if pgOptions is not None else None,
        )
        for frame in option_plan(self._hsVersion, info):
            self._send_frame(frame.opcode, frame.payload)
            if frame.ack_stage is not None:
                self._expect_ack(frame.ack_stage)

    def conn_authenticate(self, password: str | bytes | None) -> bool:
        if isinstance(password, str):
            password = password.encode("utf8")

        beresp = self._read(1)
        self.log.debug("Got response: %s", beresp)

        if beresp == ERROR_RESPONSE:
            self._read(4)
            self.last_error = self._read_error_text(self._read_length())
            self.log.warning("Server rejected the connection before authentication")
            return False
        if beresp != AUTHENTICATION_REQUEST:
            self.log.warning("Authentication error")
            return False

        areq = i_unpack(self._read(4))[0]
        self.log.debug("areq =%s", areq)
        salt_len = auth_salt_len(areq)
        if areq == AUTH_REQ_OK:
            self.log.info("success")
            return True

        salt = self._read(salt_len) if salt_len else b""
        check_salt(areq, salt, salt_len)
        if password is None:
            raise InterfaceError(
                f"server requesting {auth_method_name(areq)} password authentication, "
                "but no password was provided")
        response = auth_response(areq, password, salt)
        if response is not None:
            self._send_response(response)
        return True

    def conn_connection_complete(self) -> bool:
        while True:
            response = self._read(1)
            self.log.info("backend response: %r", response)

            if response == NULL_BYTE:
                # Stray padding between messages is not a frame.
                continue

            if response == AUTHENTICATION_REQUEST:
                areq = i_unpack(self._read(4))[0]
                self.log.info("backend response: %s", areq)
                if areq != AUTH_REQ_OK:
                    raise InterfaceError(
                        f"Unexpected authentication request after authentication: {areq}")
                continue

            # Every other message: four reserved bytes, then a length and payload.
            self._read(4)
            length = self._read_length()

            if response == NOTICE_RESPONSE:
                notices = self._read(length).decode("utf8", errors="replace")
                self.log.debug("Response received from backend: %s", notices)
            elif response == BACKEND_KEY_DATA:
                self.backend_pid = i_unpack(self._read(4))[0]
                self.log.debug("Backend response PID: %s", self.backend_pid)
                self.backend_key = i_unpack(self._read(4))[0]
            elif response == PARAMETER_STATUS:
                self._record_parameter_status(self._read(length))
            elif response == READY_FOR_QUERY:
                # The appliance pads ReadyForQuery during connect; the four
                # bytes just read as the length are that padding.
                self.log.info("Authentication Successful")
                return True
            elif response == ERROR_RESPONSE:
                self.last_error = self._read_error_text(length)
                self.log.warning("Error occured, server response:%s", self.last_error)
                return False
            else:
                self._read(length)
                self.log.debug("Skipping unexpected handshake message %r", response)

    def _record_parameter_status(self, data: bytes) -> None:
        pos = data.find(NULL_BYTE)
        if pos < 0:
            return
        key = data[:pos]
        value = data[pos + 1:].rstrip(NULL_BYTE)
        if key == b"client_encoding":
            self.server_client_encoding = value.decode("ascii", errors="replace").lower()
        self.log.debug("ParameterStatus: %s = %s", key, value)


__all__ = [
    "SyncHandshake", "option_plan", "ClientInfo",
    "CP_VERSION_1", "CP_VERSION_2", "CP_VERSION_3", "CP_VERSION_4",
    "CP_VERSION_5", "CP_VERSION_6",
    "HSV2_INVALID_OPCODE", "HSV2_CLIENT_BEGIN", "HSV2_DB", "HSV2_USER",
    "HSV2_OPTIONS", "HSV2_TTY", "HSV2_REMOTE_PID", "HSV2_PRIOR_PID",
    "HSV2_CLIENT_TYPE", "HSV2_PROTOCOL", "HSV2_HOSTCASE", "HSV2_SSL_NEGOTIATE",
    "HSV2_SSL_CONNECT", "HSV2_APPNAME", "HSV2_CLIENT_OS", "HSV2_CLIENT_HOST_NAME",
    "HSV2_CLIENT_OS_USER", "HSV2_64BIT_VARLENA_ENABLED", "HSV2_CLIENT_DONE",
    "PG_PROTOCOL_3", "PG_PROTOCOL_4", "PG_PROTOCOL_5",
    "AUTH_REQ_OK", "AUTH_REQ_KRB4", "AUTH_REQ_KRB5", "AUTH_REQ_PASSWORD",
    "AUTH_REQ_CRYPT", "AUTH_REQ_MD5", "AUTH_REQ_SHA256",
    "NPS_CLIENT", "IPS_CLIENT", "NPSCLIENT_TYPE_PYTHON",
]
