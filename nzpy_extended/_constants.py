from typing import Final

DEFAULT_BUFFER_SIZE: Final[int] = 262_144
# Upper bound of the startup/TLS/authentication exchange when neither
# ``connect_timeout`` nor ``timeout`` is given.
DEFAULT_HANDSHAKE_TIMEOUT: Final[float] = 60.0
