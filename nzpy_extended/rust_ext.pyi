"""Type stubs for the Rust DBOS decoder (``nzpy_extended.rust_ext``)."""

from collections.abc import Sequence
from typing import Any

class DbosDecoder:
    def __init__(
        self,
        field_type: Sequence[int],
        field_size: Sequence[int],
        field_trueSize: Sequence[int],
        field_offset: Sequence[int],
        field_fixedSize: Sequence[int],
        field_physField: Sequence[int],
        numFields: int,
        nullsAllowed: int,
        fixedFieldsSize: int,
        numVaryingFields: int,
        char_encoding: str,
        client_encoding: str,
    ) -> None: ...
    def row(self, buf: bytes | bytearray | memoryview) -> list[Any]: ...
    def batch(self, buf: bytes | bytearray | memoryview) -> tuple[list[list[Any]], int]: ...

def process_dbos_row(
    buf: bytes | bytearray | memoryview,
    field_type: Sequence[int],
    field_size: Sequence[int],
    field_trueSize: Sequence[int],
    field_offset: Sequence[int],
    field_fixedSize: Sequence[int],
    field_physField: Sequence[int],
    numFields: int,
    nullsAllowed: int,
    fixedFieldsSize: int,
    numVaryingFields: int,
    char_encoding: str,
    client_encoding: str,
) -> list[Any]: ...
def process_dbos_batch(
    buf: bytes | bytearray | memoryview,
    field_type: Sequence[int],
    field_size: Sequence[int],
    field_trueSize: Sequence[int],
    field_offset: Sequence[int],
    field_fixedSize: Sequence[int],
    field_physField: Sequence[int],
    numFields: int,
    nullsAllowed: int,
    fixedFieldsSize: int,
    numVaryingFields: int,
    char_encoding: str,
    client_encoding: str,
) -> tuple[list[list[Any]], int]: ...
