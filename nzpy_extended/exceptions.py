from __future__ import annotations

from typing import TypedDict


class Warning(Exception):
    pass


class Error(Exception):
    def __str__(self) -> str:
        # A backend error carries its fields as a dict; show the message itself.
        if len(self.args) == 1 and isinstance(self.args[0], dict):
            message = self.args[0].get("M")
            if isinstance(message, str):
                return message
        return super().__str__()

    @property
    def sqlstate(self) -> str | None:
        value = self.args[0] if self.args else None
        return value.get("C") if isinstance(value, dict) else None

    @property
    def diag(self) -> dict[str, str]:
        value = self.args[0] if self.args else None
        return dict(value) if isinstance(value, dict) else {}


# Typed payload stored on Connection.error before raising DB-API exceptions.
# Keys mirror PostgreSQL ErrorResponse fields (e.g. ``C`` = SQLSTATE, ``M`` = message).
class ErrorResponseDict(TypedDict, total=False):
    C: str
    M: str
    D: str
    H: str
    P: str
    R: str
    S: str
    V: str
    W: str
    F: str
    L: str


class InterfaceError(Error):
    pass


class ConnectionClosedError(InterfaceError):
    def __init__(self, msg: str | None = None) -> None:
        super().__init__(msg if msg is not None else "connection is closed")


class DatabaseError(Error):
    pass


class DataError(DatabaseError):
    pass


class OperationalError(DatabaseError):
    pass


class IntegrityError(DatabaseError):
    pass


class InternalError(DatabaseError):
    pass


class ProgrammingError(DatabaseError):
    pass


class NotSupportedError(DatabaseError):
    pass


class ArrayContentNotSupportedError(NotSupportedError):
    pass


class ArrayContentNotHomogenousError(ProgrammingError):
    pass


class ArrayDimensionsNotConsistentError(ProgrammingError):
    pass


__all__ = [
    "Warning", "Error", "ErrorResponseDict", "InterfaceError", "ConnectionClosedError",
    "DatabaseError", "DataError", "OperationalError", "IntegrityError",
    "InternalError", "ProgrammingError", "NotSupportedError",
    "ArrayContentNotSupportedError", "ArrayContentNotHomogenousError",
    "ArrayDimensionsNotConsistentError",
]
