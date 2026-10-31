"""Pure handshake decisions: option frames, authentication payloads, downgrades.

Nothing in this module touches a socket. :class:`~nzpy_extended.handshake.SyncHandshake`
performs the reads and writes; this module decides what goes on the wire. The
option sequence mirrors the Rust driver's ``handshake_common`` module, which is
the reference the golden tests check against.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from hashlib import md5, sha256

from .exceptions import InterfaceError
from .utils import h_pack, i_pack

CP_VERSION_1 = 1
CP_VERSION_2 = 2
CP_VERSION_3 = 3
CP_VERSION_4 = 4
CP_VERSION_5 = 5
CP_VERSION_6 = 6
# Versions the server may propose during negotiation. Version 1 is never offered.
PROPOSABLE_VERSIONS: tuple[int, ...] = (CP_VERSION_2, CP_VERSION_3, CP_VERSION_4, CP_VERSION_5)

HSV2_INVALID_OPCODE = 0
HSV2_CLIENT_BEGIN = 1
HSV2_DB = 2
HSV2_USER = 3
HSV2_OPTIONS = 4
HSV2_TTY = 5
HSV2_REMOTE_PID = 6
HSV2_PRIOR_PID = 7
HSV2_CLIENT_TYPE = 8
HSV2_PROTOCOL = 9
HSV2_HOSTCASE = 10
HSV2_SSL_NEGOTIATE = 11
HSV2_SSL_CONNECT = 12
HSV2_APPNAME = 13
HSV2_CLIENT_OS = 14
HSV2_CLIENT_HOST_NAME = 15
HSV2_CLIENT_OS_USER = 16
HSV2_64BIT_VARLENA_ENABLED = 17
HSV2_CLIENT_DONE = 1000

PG_PROTOCOL_3 = 3
PG_PROTOCOL_4 = 4
PG_PROTOCOL_5 = 5

AUTH_REQ_OK = 0
AUTH_REQ_KRB4 = 1
AUTH_REQ_KRB5 = 2
AUTH_REQ_PASSWORD = 3
AUTH_REQ_CRYPT = 4
AUTH_REQ_MD5 = 5
AUTH_REQ_SHA256 = 6

NPS_CLIENT = 0
IPS_CLIENT = 1
NPSCLIENT_TYPE_PYTHON = 13

NUL = b"\x00"


@dataclass(frozen=True)
class OptionFrame:
    """One client option frame and the acknowledgement stage that follows it.

    ``ack_stage`` names the reply the server owes for this frame, or is ``None``
    when the frame is the final ``CLIENT_DONE`` and no reply is read.
    """

    opcode: int
    payload: bytes
    ack_stage: str | None


@dataclass(frozen=True)
class ClientInfo:
    user: bytes
    app_name: str
    client_os: str
    client_host_name: str
    os_user: str
    remote_pid: int
    client_type: int = NPSCLIENT_TYPE_PYTHON
    pg_options: bytes | None = None


def validate_version_downgrade(current: int, proposed: int) -> None:
    """Reject a server proposal that does not move strictly down.

    Accepting the same or a higher version would retry negotiation forever.
    """
    if proposed not in PROPOSABLE_VERSIONS:
        raise InterfaceError(f"Unsupported handshake version proposed by server: {proposed}")
    if proposed >= current:
        raise InterfaceError(
            f"Handshake version did not decrease: server proposed {proposed} after {current}")


def _cstring(opcode: int, value: bytes, stage: str) -> OptionFrame:
    return OptionFrame(opcode, value + NUL, stage)


def option_plan(version: int, info: ClientInfo) -> list[OptionFrame]:
    """Option frames for a negotiated connection-protocol version, in wire order."""
    frames = [_cstring(HSV2_USER, info.user, "user")]

    if version in (CP_VERSION_4, CP_VERSION_6):
        frames.extend([
            _cstring(HSV2_APPNAME, info.app_name.encode("utf8"), "appname"),
            _cstring(HSV2_CLIENT_OS, info.client_os.encode("utf8"), "clientOs"),
            _cstring(HSV2_CLIENT_HOST_NAME, info.client_host_name.encode("utf8"), "clientHostName"),
            _cstring(HSV2_CLIENT_OS_USER, info.os_user.encode("utf8"), "clientOsUser"),
        ])

    frames.append(OptionFrame(
        HSV2_PROTOCOL, h_pack(PG_PROTOCOL_3) + h_pack(PG_PROTOCOL_5), "remotePid"))
    frames.append(OptionFrame(HSV2_REMOTE_PID, i_pack(info.remote_pid), "clientType"))

    if info.pg_options is not None:
        frames.append(_cstring(HSV2_OPTIONS, info.pg_options, "options"))

    frames.append(OptionFrame(
        HSV2_CLIENT_TYPE, h_pack(info.client_type),
        "64bitVarlena" if version >= CP_VERSION_5 else "clientDone"))
    if version >= CP_VERSION_5:
        frames.append(OptionFrame(HSV2_64BIT_VARLENA_ENABLED, h_pack(IPS_CLIENT), "clientDone"))

    frames.append(OptionFrame(HSV2_CLIENT_DONE, b"", None))
    return frames


def auth_salt_len(request: int) -> int:
    """Salt bytes that follow an authentication request.

    Unknown methods raise before the caller reads anything else from the socket.
    """
    if request in (AUTH_REQ_OK, AUTH_REQ_PASSWORD):
        return 0
    if request in (AUTH_REQ_MD5, AUTH_REQ_SHA256):
        return 2
    raise InterfaceError(
        f"Unsupported authentication type requested by server: {request}")


def auth_method_name(request: int) -> str:
    return {AUTH_REQ_MD5: "MD5", AUTH_REQ_SHA256: "SHA256"}.get(request, str(request))


def unpadded_base64(digest: bytes) -> bytes:
    return base64.standard_b64encode(digest).rstrip(b"=")


def auth_response(request: int, password: bytes, salt: bytes) -> bytes | None:
    """Response payload, including its trailing NUL. ``None`` means no reply is due.

    ``password`` must already be validated as present for every method that
    needs it; a missing password is the caller's error to report.
    """
    if request == AUTH_REQ_OK:
        return None
    if request == AUTH_REQ_PASSWORD:
        return password + NUL
    if request == AUTH_REQ_MD5:
        return unpadded_base64(md5(salt + password).digest()) + NUL
    if request == AUTH_REQ_SHA256:
        return unpadded_base64(sha256(salt + password).digest()) + NUL
    raise InterfaceError(
        f"Unsupported authentication type requested by server: {request}")


def check_salt(request: int, salt: bytes, expected_len: int) -> None:
    if len(salt) != expected_len:
        raise InterfaceError(
            f"server sent a malformed {auth_method_name(request)} authentication salt")


__all__ = [
    "AUTH_REQ_CRYPT", "AUTH_REQ_KRB4", "AUTH_REQ_KRB5", "AUTH_REQ_MD5",
    "AUTH_REQ_OK", "AUTH_REQ_PASSWORD", "AUTH_REQ_SHA256",
    "CP_VERSION_1", "CP_VERSION_2", "CP_VERSION_3", "CP_VERSION_4",
    "CP_VERSION_5", "CP_VERSION_6", "ClientInfo", "IPS_CLIENT",
    "HSV2_64BIT_VARLENA_ENABLED", "HSV2_APPNAME", "HSV2_CLIENT_BEGIN",
    "HSV2_CLIENT_DONE", "HSV2_CLIENT_HOST_NAME", "HSV2_CLIENT_OS",
    "HSV2_CLIENT_OS_USER", "HSV2_CLIENT_TYPE", "HSV2_DB", "HSV2_HOSTCASE",
    "HSV2_INVALID_OPCODE", "HSV2_OPTIONS", "HSV2_PRIOR_PID", "HSV2_PROTOCOL",
    "HSV2_REMOTE_PID", "HSV2_SSL_CONNECT", "HSV2_SSL_NEGOTIATE", "HSV2_TTY",
    "HSV2_USER", "NPS_CLIENT", "NPSCLIENT_TYPE_PYTHON", "OptionFrame",
    "PG_PROTOCOL_3", "PG_PROTOCOL_4", "PG_PROTOCOL_5", "PROPOSABLE_VERSIONS",
    "auth_method_name", "auth_response", "auth_salt_len", "check_salt",
    "option_plan", "unpadded_base64", "validate_version_downgrade",
]
