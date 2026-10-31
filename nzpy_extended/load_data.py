from __future__ import annotations
from collections.abc import Iterable, AsyncIterable, Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .core import Connection


async def load_data(
    conn: Connection,
    table_name: str,
    rows: Iterable[Any] | AsyncIterable[Any],
    columns: Sequence[Any] | None = None,
    delimiter: str = '|',
    encoding: str | None = None,
    create_if_missing: bool = True,
    temporary: bool = False,
    distribute_on_random: bool = True,
    logdir: str | None = None,
    escape_char: str | None = '\\',
) -> int:
    return await conn.load_data(
        table_name=table_name,
        rows=rows,
        columns=columns,
        delimiter=delimiter,
        encoding=encoding,
        create_if_missing=create_if_missing,
        temporary=temporary,
        distribute_on_random=distribute_on_random,
        logdir=logdir,
        escape_char=escape_char,
    )
