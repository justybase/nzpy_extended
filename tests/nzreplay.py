"""Reader for the shared ``.nzreplay.gz`` protocol fixtures.

The format is produced by the C# ``tools/NzReplayCapture`` tool: a gzip
stream around a .NET ``BinaryWriter`` payload.

Layout (little-endian)::

    u32 magic 0x4E5A5250 ("NZRP"), i32 version (2),
    .NET string query (7-bit encoded byte length + UTF-8),
    i32 expected rows, i32 expected columns, i32 segment count,
    segment count x (i32 length + raw server-to-client bytes)

``segments[0]`` is the handshake response, every later segment answers one
client query packet and the last segment is the recorded ``SELECT``.
"""

from __future__ import annotations

import gzip
import os
import struct
from dataclasses import dataclass
from pathlib import Path

MAGIC = 0x4E5A5250
VERSION = 2


class NzReplayError(ValueError):
    """Raised when an ``.nzreplay.gz`` fixture is malformed."""


@dataclass(frozen=True)
class NzReplayFixture:
    """A parsed replay fixture."""

    query: str
    expected_rows: int
    expected_columns: int
    segments: tuple[bytes, ...]

    @property
    def handshake(self) -> bytes:
        """The recorded server handshake response."""
        return self.segments[0]

    @property
    def last_response(self) -> bytes:
        """The last recorded response (the user SQL result)."""
        return self.segments[-1]

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> "NzReplayFixture":
        """Loads and parses one ``.nzreplay.gz`` file."""
        with gzip.open(os.fspath(path), "rb") as stream:
            raw = stream.read()
        try:
            return cls.parse(raw)
        except NzReplayError as error:
            raise NzReplayError(f"{path}: {error}") from None

    @classmethod
    def parse(cls, raw: bytes) -> "NzReplayFixture":
        """Parses an already-decompressed fixture payload."""
        if len(raw) < 8:
            raise NzReplayError("truncated header")
        magic, version = struct.unpack_from("<Ii", raw, 0)
        if magic != MAGIC:
            raise NzReplayError(f"bad magic 0x{magic:08X}")
        if version != VERSION:
            raise NzReplayError(f"unsupported version {version}")

        query, offset = _read_dotnet_string(raw, 8)
        if offset + 12 > len(raw):
            raise NzReplayError("truncated header")
        expected_rows, expected_columns, segment_count = struct.unpack_from(
            "<iii", raw, offset
        )
        offset += 12
        if segment_count < 2:
            raise NzReplayError(
                "fixture needs a handshake and at least one response segment"
            )

        segments: list[bytes] = []
        for _ in range(segment_count):
            if offset + 4 > len(raw):
                raise NzReplayError("truncated segment length")
            (length,) = struct.unpack_from("<i", raw, offset)
            offset += 4
            if length < 0 or offset + length > len(raw):
                raise NzReplayError(f"invalid segment length {length}")
            segments.append(raw[offset : offset + length])
            offset += length
        if offset != len(raw):
            raise NzReplayError(f"{len(raw) - offset} trailing bytes")

        return cls(
            query=query,
            expected_rows=expected_rows,
            expected_columns=expected_columns,
            segments=tuple(segments),
        )


def _read_dotnet_string(raw: bytes, offset: int) -> tuple[str, int]:
    length = 0
    shift = 0
    while True:
        if offset >= len(raw):
            raise NzReplayError("truncated string length")
        byte = raw[offset]
        offset += 1
        length |= (byte & 0x7F) << shift
        if byte & 0x80 == 0:
            break
        shift += 7
        if shift > 28:
            raise NzReplayError("invalid 7-bit string length")
    if offset + length > len(raw):
        raise NzReplayError("truncated query string")
    try:
        query = raw[offset : offset + length].decode("utf-8")
    except UnicodeDecodeError:
        raise NzReplayError("query is not UTF-8") from None
    return query, offset + length


def fixture_candidates(name: str) -> list[Path]:
    """Returns the usual workspace locations for a shipped fixture."""
    file_name = f"{name}.nzreplay.gz"
    candidates: list[Path] = []
    configured = os.environ.get("NZ_REPLAY_FIXTURE_DIR")
    if configured:
        candidates.append(Path(configured) / file_name)
    tests_dir = Path(__file__).resolve().parent
    workspace = tests_dir.parents[1]
    candidates.append(tests_dir / "fixtures" / "replay" / file_name)
    candidates.append(
        workspace
        / "justybase_netezza_node_driver"
        / "tests"
        / "fixtures"
        / "replay"
        / file_name
    )
    candidates.append(
        workspace
        / "JustyBase.NetezzaDriver"
        / "src"
        / "JustyBase.NetezzaDriver.TestSupport"
        / "Fixtures"
        / file_name
    )
    return candidates


def find_fixture(name: str) -> Path | None:
    """Returns the first existing fixture path, or ``None``."""
    return next(
        (candidate for candidate in fixture_candidates(name) if candidate.is_file()),
        None,
    )


def load_shipped(name: str) -> NzReplayFixture:
    """Loads a fixture from the workspace, raising when it is missing."""
    path = find_fixture(name)
    if path is None:
        searched = ", ".join(str(item) for item in fixture_candidates(name))
        raise FileNotFoundError(
            f"replay fixture '{name}' not found in: {searched}"
        )
    return NzReplayFixture.load(path)
