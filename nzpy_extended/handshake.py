import base64
import logging
import socket as _socket
import ssl
from getpass import getuser
from hashlib import md5, sha256
from os import getpid, path
from platform import system
from socket import gethostname
from sys import argv
from typing import Any

from .exceptions import InterfaceError
from .protocol import (NULL_BYTE, ERROR_RESPONSE, AUTHENTICATION_REQUEST,
                       NOTICE_RESPONSE, BACKEND_KEY_DATA, READY_FOR_QUERY,
                       PARAMETER_STATUS)
from .utils import h_pack, i_pack, i_unpack

CP_VERSION_1 = 1
CP_VERSION_2 = 2
CP_VERSION_3 = 3
CP_VERSION_4 = 4
CP_VERSION_5 = 5
CP_VERSION_6 = 6

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

    def _write(self, data: bytes | bytearray) -> None:
        self._sock.write(data)

    def _read(self, n: int) -> bytes:
        return self._sock.read(n)

    def _flush(self) -> None:
        self._sock.flush()  # pyright: ignore[reportAttributeAccessIssue]

    def startup(
        self,
        database: str | bytes | None,
        securityLevel: int,
        user: str | bytes,
        password: str | bytes | None,
        pgOptions: str | None
    ) -> object:
        if not self.conn_handshake_negotiate(self._hsVersion, self._protocol2):
            self.log.info("Handshake negotiation unsuccessful")
            return False

        self.log.debug("Sending handshake information to server")
        if not self.conn_send_handshake_info(
            database, securityLevel, self._hsVersion,
            self._protocol1, self._protocol2, user, pgOptions
        ):
            self.log.warning("Error in conn_send_handshake_info")
            return False

        if not self.conn_authenticate(password):
            self.log.warning("Error in conn_authenticate")
            return False

        if not self.conn_connection_complete():
            self.log.warning("Error in conn_connection_complete")
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
            val = bytearray(h_pack(HSV2_CLIENT_BEGIN) + h_pack(version))
            self._write(i_pack(len(val) + 4))
            self._write(val)
            self._flush()

            self.log.info("sent handshake negotiation block successfully")

            beresp = self._read(1)

            self.log.debug("Got response: %s", beresp)

            if beresp == b'N':
                self._hsVersion = version
                self._protocol2 = 0
                return True
            elif beresp == b'M':
                version_bytes = self._read(1)
                if version_bytes == b'2':
                    version = CP_VERSION_2
                elif version_bytes == b'3':
                    version = CP_VERSION_3
                elif version_bytes == b'4':
                    version = CP_VERSION_4
                elif version_bytes == b'5':
                    version = CP_VERSION_5
                else:
                    self.log.warning("Unsupported handshake version: %s", version_bytes)
                    return False
            elif beresp == b'E':
                self.log.warning("Bad attribute value error")
                return False
            else:
                self.log.warning("Bad protocol error")
                return False

    def conn_send_handshake_info(
        self,
        _database: str | bytes | None,
        securityLevel: int,
        _hsVersion: int | None,
        _protocol1: int | None,
        _protocol2: int | None,
        user: str | bytes,
        pgOptions: str | None
    ) -> bool:
        if not self.conn_send_database(_database):
            return False

        if not self.conn_secure_session(securityLevel):
            return False

        if not self.conn_set_next_dataprotocol(self._protocol1, self._protocol2):
            return False

        if self._hsVersion in (CP_VERSION_6, CP_VERSION_4):
            return self.conn_send_handshake_version4(
                self._protocol1, self._protocol2, self._hsVersion, user, pgOptions
            )
        elif self._hsVersion in (CP_VERSION_5, CP_VERSION_3, CP_VERSION_2):
            return self.conn_send_handshake_version2(
                self._protocol1, self._protocol2, self._hsVersion, user, pgOptions
            )

        return True

    def conn_send_database(self, _database: str | bytes | None) -> bool:
        db: bytes | None = None
        if _database is not None:
            if isinstance(_database, str):
                db = _database.encode('utf8')
            else:
                db = _database
            self.log.info("Database name: %s", db.decode('utf8') if db else "")

            val = bytearray(h_pack(HSV2_DB))
            if db:
                val.extend(db + NULL_BYTE)
            else:
                val.extend(NULL_BYTE)

            self._write(i_pack(len(val) + 4))
            self._write(val)
            self._flush()

        beresp = self._read(1)
        self.log.info("Backend response: %s", str(beresp, 'utf8'))
        if beresp == b'N':
            return True
        elif beresp == ERROR_RESPONSE:
            self.log.warning("ERROR_AUTHOR_BAD")
            return False
        else:
            self.log.warning("Unknown response")
            return False

    def conn_set_next_dataprotocol(
        self,
        _protocol1: int | None,
        _protocol2: int | None
    ) -> bool:
        if self._protocol2 == 0:
            self._protocol2 = PG_PROTOCOL_5
        elif _protocol2 == 5:
            self._protocol2 = PG_PROTOCOL_4
        elif _protocol2 == 4:
            self._protocol2 = PG_PROTOCOL_3
        else:
            return False

        self._protocol1 = PG_PROTOCOL_3
        self.log.debug("Connection protocol set to : %s %s",
                       self._protocol1, self._protocol2)
        return True

    def _ssl_allow_fallback(self) -> bool:
        if isinstance(self.ssl_params, dict):
            return bool(self.ssl_params.get('ssl_allow_fallback', False))
        return False

    def conn_secure_session(self, securityLevel: int) -> bool:
        information = HSV2_SSL_NEGOTIATE
        currSecLevel = securityLevel
        requested_security = securityLevel
        ssl_context: ssl.SSLContext | None = None

        while information != 0:
            opcode = information
            if information == HSV2_SSL_NEGOTIATE:
                self.log.debug("Security Level requested = %s", currSecLevel)

            if information == HSV2_SSL_CONNECT:
                pass

            val = bytearray(h_pack(opcode) + i_pack(currSecLevel))
            self._write(i_pack(len(val) + 4))
            self._write(val)
            self._flush()

            if information == HSV2_SSL_CONNECT:
                try:
                    if self.ssl_params is None:
                        ssl_context = ssl.create_default_context()
                        ssl_context.check_hostname = True
                        ssl_context.verify_mode = ssl.CERT_REQUIRED
                    else:
                        ca_certs = self.ssl_params.get('ca_certs')
                        ssl_context = ssl.create_default_context(cafile=ca_certs)
                        ssl_verify = self.ssl_params.get('ssl_verify', True)
                        if not ssl_verify:
                            self.log.warning("SSL certificate verification disabled (ssl_verify=False)")
                            ssl_context.check_hostname = False
                            ssl_context.verify_mode = ssl.CERT_NONE
                        else:
                            ssl_context.check_hostname = True
                            ssl_context.verify_mode = ssl.CERT_REQUIRED

                    self._sock.close()
                    self._usock = ssl_context.wrap_socket(
                        self._usock,
                        server_hostname=(self.ssl_params or {}).get('server_hostname'),
                    )
                    self._sock = self._usock.makefile(mode="rwb")
                    self.log.info("Secured Connect Success")
                except ssl.SSLError:
                    self.log.warning("Problem establishing secured session")
                    return False

            if information != 0:
                beresp = self._read(1)
                self.log.debug("Got response =%s", beresp)
                if beresp == b'S':
                    if not isinstance(self.ssl_params, dict):
                        self.ssl_params = {}
                    try:
                        ca_certs = self.ssl_params.get('ca_certs')
                        ssl_context = ssl.create_default_context(cafile=ca_certs)
                        ssl_verify = self.ssl_params.get('ssl_verify', True)
                        if not ssl_verify:
                            self.log.warning("SSL certificate verification disabled (ssl_verify=False)")
                            ssl_context.check_hostname = False
                            ssl_context.verify_mode = ssl.CERT_NONE
                        else:
                            ssl_context.check_hostname = True
                            ssl_context.verify_mode = ssl.CERT_REQUIRED
                        information = HSV2_SSL_CONNECT
                    except ImportError:
                        raise InterfaceError("SSL required but ssl module not available in this python installation")
                    except ssl.SSLError:
                        if currSecLevel == 2 and self._ssl_allow_fallback():
                            self.log.debug("Problem establishing secured session")
                            self.log.debug(
                                "Attempting unsecured session (ssl_allow_fallback=True)"
                            )
                            currSecLevel = 1
                            information = HSV2_SSL_NEGOTIATE
                            continue
                        self.log.warning("Problem establishing secured session")
                        return False
                elif beresp == b'N':
                    if information == HSV2_SSL_NEGOTIATE:
                        if requested_security >= 2 and not (
                            requested_security == 2 and self._ssl_allow_fallback()
                        ):
                            self.log.warning(
                                "Server offered unsecured session but ssl_allow_fallback "
                                "is not enabled (requested securityLevel=%s)",
                                requested_security,
                            )
                            return False
                        self.log.debug("Attempting unsecured session")
                    information = 0
                    return True
                elif beresp == b'E':
                    self.log.warning("Error: connection failed")
                    return False
        return True

    def conn_send_handshake_version2(
        self,
        _protocol1: int | None,
        _protocol2: int | None,
        _hsVersion: int | None,
        user: str | bytes,
        pgOptions: str | None
    ) -> bool:
        if isinstance(user, str):
            user = user.encode('utf8')

        information = HSV2_USER
        val = bytearray(h_pack(information))
        val.extend(user + NULL_BYTE)
        information = HSV2_PROTOCOL

        while information != 0:
            self._write(i_pack(len(val) + 4))
            self._write(val)
            self._flush()
            beresp = self._read(1)
            self.log.info("Backend response: %s", str(beresp, "utf8"))
            if beresp == b'N':
                if information == HSV2_PROTOCOL:
                    val = bytearray(h_pack(information) + h_pack(_protocol1) + h_pack(_protocol2))
                    information = HSV2_REMOTE_PID
                    continue
                if information == HSV2_REMOTE_PID:
                    val = bytearray(h_pack(information) + i_pack(getpid()))
                    information = HSV2_OPTIONS
                    continue
                if information == HSV2_OPTIONS:
                    if pgOptions is not None:
                        val = bytearray(h_pack(information))
                        val.extend(pgOptions.encode('utf8') + NULL_BYTE)
                    information = HSV2_CLIENT_TYPE
                    continue
                if information == HSV2_CLIENT_TYPE:
                    val = bytearray(h_pack(information) + h_pack(NPSCLIENT_TYPE_PYTHON))
                    if _hsVersion in (CP_VERSION_5, CP_VERSION_6):
                        information = HSV2_64BIT_VARLENA_ENABLED
                    else:
                        information = HSV2_CLIENT_DONE
                    continue
                if information == HSV2_64BIT_VARLENA_ENABLED:
                    val = bytearray(h_pack(information) + h_pack(IPS_CLIENT))
                    information = HSV2_CLIENT_DONE
                    continue
                if information == HSV2_CLIENT_DONE:
                    val = bytearray(h_pack(information))
                    information = 0
                    self._write(i_pack(len(val) + 4))
                    self._write(val)
                    self._flush()
                    return True
            elif beresp == ERROR_RESPONSE:
                self.log.warning("ERROR_CONN_FAIL")
                return False
        return False

    def conn_send_handshake_version4(
        self,
        _protocol1: int | None,
        _protocol2: int | None,
        _hsVersion: int | None,
        user: str | bytes,
        pgOptions: str | None
    ) -> bool:
        if isinstance(user, str):
            user = user.encode('utf8')

        information = HSV2_USER
        val = bytearray(h_pack(information))
        val.extend(user + NULL_BYTE)
        information = HSV2_APPNAME

        while information != 0:
            self._write(i_pack(len(val) + 4))
            self._write(val)
            self._flush()
            beresp = self._read(1)
            self.log.info("Backend response: %s", str(beresp, "utf8"))
            if beresp == b'N':
                if information == HSV2_APPNAME:
                    val = bytearray(h_pack(information))
                    val.extend(self.guardium_applName.encode('utf8') + NULL_BYTE)
                    self.log.debug("Appname :%s", self.guardium_applName)
                    information = HSV2_CLIENT_OS
                    continue
                if information == HSV2_CLIENT_OS:
                    val = bytearray(h_pack(information))
                    val.extend(self.guardium_clientOS.encode('utf8') + NULL_BYTE)
                    self.log.debug("Client OS :%s", self.guardium_clientOS)
                    information = HSV2_CLIENT_HOST_NAME
                    continue
                if information == HSV2_CLIENT_HOST_NAME:
                    val = bytearray(h_pack(information))
                    val.extend(self.guardium_clientHostName.encode('utf8') + NULL_BYTE)
                    self.log.debug("Client hostname :%s", self.guardium_clientHostName)
                    information = HSV2_CLIENT_OS_USER
                    continue
                if information == HSV2_CLIENT_OS_USER:
                    val = bytearray(h_pack(information))
                    val.extend(self.guardium_clientOSUser.encode('utf8') + NULL_BYTE)
                    self.log.debug("Client OS user :%s", self.guardium_clientOSUser)
                    information = HSV2_PROTOCOL
                    continue
                if information == HSV2_PROTOCOL:
                    val = bytearray(h_pack(information) + h_pack(_protocol1) + h_pack(_protocol2))
                    information = HSV2_REMOTE_PID
                    continue
                if information == HSV2_REMOTE_PID:
                    val = bytearray(h_pack(information) + i_pack(getpid()))
                    information = HSV2_OPTIONS
                    continue
                if information == HSV2_OPTIONS:
                    if pgOptions is not None:
                        val = bytearray(h_pack(information))
                        val.extend(pgOptions.encode('utf8') + NULL_BYTE)
                    information = HSV2_CLIENT_TYPE
                    continue
                if information == HSV2_CLIENT_TYPE:
                    val = bytearray(h_pack(information) + h_pack(NPSCLIENT_TYPE_PYTHON))
                    if _hsVersion in (CP_VERSION_5, CP_VERSION_6):
                        information = HSV2_64BIT_VARLENA_ENABLED
                    else:
                        information = HSV2_CLIENT_DONE
                    continue
                if information == HSV2_64BIT_VARLENA_ENABLED:
                    val = bytearray(h_pack(information) + h_pack(IPS_CLIENT))
                    information = HSV2_CLIENT_DONE
                    continue
                if information == HSV2_CLIENT_DONE:
                    val = bytearray(h_pack(information))
                    information = 0
                    self._write(i_pack(len(val) + 4))
                    self._write(val)
                    self._flush()
                    return True
            elif beresp == ERROR_RESPONSE:
                self.log.warning("ERROR_CONN_FAIL")
                return False
        return False

    def conn_authenticate(self, password: str | bytes | None) -> bool:
        if isinstance(password, str):
            password = password.encode('utf8')

        beresp = self._read(1)
        self.log.debug("Got response: %s", beresp)

        if beresp != AUTHENTICATION_REQUEST:
            self.log.warning("Authentication error")
            return False

        self.log.debug("auth got 'R' - request for password")
        areq = i_unpack(self._read(4))[0]
        self.log.debug("areq =%s", areq)

        if areq == AUTH_REQ_OK:
            self.log.info("success")
            return True

        if areq == AUTH_REQ_PASSWORD:
            self.log.info("Plain password requested")
            if password is None:
                raise InterfaceError("server requesting password authentication, but no password was provided")
            self._write(i_pack(len(password + NULL_BYTE) + 4))
            self._write(password + NULL_BYTE)
            self._flush()

        elif areq == AUTH_REQ_MD5:
            self.log.info("Password type is MD5")
            salt = self._read(2)
            self.log.debug("Salt =%s", salt)
            if password is None:
                raise InterfaceError(
                    "server requesting MD5 password authentication, "
                    "but no password was provided")
            md5encoded = md5(salt + password)
            md5pwd = base64.standard_b64encode(md5encoded.digest())
            pwd = md5pwd.rstrip(b"=")

            self._write(i_pack(len(pwd + NULL_BYTE) + 4))
            self._write(pwd + NULL_BYTE)
            self._flush()

        elif areq == AUTH_REQ_SHA256:
            self.log.info("Password type is SHA256")
            salt = self._read(2)
            self.log.debug("Salt =%s", salt)
            if password is None:
                raise InterfaceError("server requesting SHA256 password "
                                     "authentication, but no password "
                                     "was provided")
            sha256encoded = sha256(salt + password)
            sha256pwd = base64.standard_b64encode(sha256encoded.digest())
            pwd = sha256pwd.rstrip(b"=")

            self._write(i_pack(len(pwd + NULL_BYTE) + 4))
            self._write(pwd + NULL_BYTE)
            self._flush()

        elif areq == AUTH_REQ_KRB5:
            self.log.info("krb encryption requested from backend")
            raise InterfaceError("KRB5 authentication not supported")

        return True

    def conn_connection_complete(self) -> bool:
        length = 0
        while True:
            response = self._read(1)
            self.log.info("backend response: %s", str(response, 'utf8'))

            if response != AUTHENTICATION_REQUEST:
                self._read(4)
                length = i_unpack(self._read(4))[0]

            if response == AUTHENTICATION_REQUEST:
                areq = i_unpack(self._read(4))[0]
                self.log.info("backend response: %s", areq)

            if response == NOTICE_RESPONSE:
                notices = str(self._read(length), 'utf8')
                self.log.debug("Response received from backend: %s", notices)

            if response == BACKEND_KEY_DATA:
                self.backend_pid = i_unpack(self._read(4))[0]
                self.log.debug("Backend response PID: %s", self.backend_pid)

                self.backend_key = i_unpack(self._read(4))[0]

            if response == PARAMETER_STATUS:
                data = self._read(length)
                pos = data.find(NULL_BYTE)
                if pos >= 0:
                    key = data[:pos]
                    value = data[pos + 1:-1]
                    if key == b"client_encoding":
                        self.server_client_encoding = value.decode("ascii").lower()
                    self.log.debug("ParameterStatus: %s = %s", key, value)

            if response == READY_FOR_QUERY:
                self.log.info("Authentication Successful")
                return True

            if response == ERROR_RESPONSE:
                error = str(self._read(length), 'utf8')
                self.log.warning("Error occured, server response:%s", error)
                self.last_error = error
                return False


__all__ = [
    "SyncHandshake",
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
