"""Unit coverage for the shared ``.nzreplay.gz`` reader."""

from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nzreplay import MAGIC, NzReplayError, NzReplayFixture, load_shipped


class NzReplayReaderTests(unittest.TestCase):
    def test_dimdate_fixture_matches_manifest(self) -> None:
        fixture = load_shipped("dimdate")
        self.assertEqual(fixture.expected_rows, 3652)
        self.assertEqual(fixture.expected_columns, 19)
        self.assertEqual(fixture.query, "SELECT * FROM JUST_DATA.ADMIN.DIMDATE")
        self.assertGreaterEqual(len(fixture.segments), 2)
        self.assertTrue(fixture.handshake)
        self.assertGreater(len(fixture.last_response), 100_000)

    def test_fact200k_fixture_matches_manifest(self) -> None:
        fixture = load_shipped("fact200k")
        self.assertEqual(fixture.expected_rows, 200_000)
        self.assertEqual(fixture.expected_columns, 7)
        self.assertTrue(fixture.query.startswith("SELECT"))
        self.assertTrue(fixture.last_response)

    def test_rejects_bad_magic_and_trailing_bytes(self) -> None:
        with self.assertRaisesRegex(NzReplayError, "bad magic"):
            NzReplayFixture.parse(b"\x00\x00\x00\x00\x00\x00\x00\x00")

        with self.assertRaisesRegex(NzReplayError, "truncated header"):
            NzReplayFixture.parse(b"\x00\x00\x00\x00")

        raw = bytearray()
        raw += struct.pack("<I", MAGIC)
        raw += struct.pack("<i", 2)
        raw += b"\x00"  # empty .NET string
        raw += struct.pack("<iii", 0, 0, 2)
        with self.assertRaisesRegex(NzReplayError, "truncated segment length"):
            NzReplayFixture.parse(bytes(raw))

        raw += struct.pack("<i", 1) + b"x"
        raw += struct.pack("<i", 1) + b"y"
        fixture = NzReplayFixture.parse(bytes(raw))
        self.assertEqual(fixture.segments, (b"x", b"y"))
        self.assertEqual(fixture.handshake, b"x")
        self.assertEqual(fixture.last_response, b"y")

        raw += b"\x00"
        with self.assertRaisesRegex(NzReplayError, "trailing bytes"):
            NzReplayFixture.parse(bytes(raw))


if __name__ == "__main__":
    unittest.main()
