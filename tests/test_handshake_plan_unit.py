"""Pure handshake plan: option sequences, auth payloads and downgrade rules.

The expected opcode lists and payloads mirror the Rust driver's
``handshake_common`` tests, which are checked against the live appliance.
"""

from __future__ import annotations

import base64
import hashlib

import pytest

from nzpy_extended import _handshake_plan as plan
from nzpy_extended.exceptions import InterfaceError

pytestmark = pytest.mark.unit


def _info(**overrides: object) -> plan.ClientInfo:
    values: dict[str, object] = dict(
        user=b"user",
        app_name="app",
        client_os="linux",
        client_host_name="host",
        os_user="os-user",
        remote_pid=0x01020304,
        client_type=plan.NPSCLIENT_TYPE_PYTHON,
    )
    values.update(overrides)
    return plan.ClientInfo(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("version,opcodes", [
    (2, [3, 9, 6, 8, 1000]),
    (3, [3, 9, 6, 8, 1000]),
    (4, [3, 13, 14, 15, 16, 9, 6, 8, 1000]),
    (5, [3, 9, 6, 8, 17, 1000]),
    (6, [3, 13, 14, 15, 16, 9, 6, 8, 17, 1000]),
])
def test_option_sequence_per_version_matches_reference(version: int, opcodes: list[int]) -> None:
    frames = plan.option_plan(version, _info())
    assert [frame.opcode for frame in frames] == opcodes


def test_version_six_payloads_are_in_wire_order() -> None:
    frames = plan.option_plan(6, _info())
    assert frames[0].payload == b"user\x00"
    assert frames[1].payload == b"app\x00"
    assert frames[2].payload == b"linux\x00"
    assert frames[3].payload == b"host\x00"
    assert frames[4].payload == b"os-user\x00"
    assert frames[5].payload == b"\x00\x03\x00\x05"
    assert frames[6].payload == (0x01020304).to_bytes(4, "big", signed=True)
    assert frames[7].payload == (13).to_bytes(2, "big")
    assert frames[8].payload == (1).to_bytes(2, "big")
    assert frames[9].payload == b""


def test_each_frame_but_client_done_expects_an_ack() -> None:
    frames = plan.option_plan(6, _info())
    assert [frame.ack_stage is None for frame in frames] == [False] * 9 + [True]
    assert frames[5].ack_stage == "remotePid"
    assert frames[7].ack_stage == "64bitVarlena"
    assert frames[8].ack_stage == "clientDone"


def test_pg_options_frame_sits_between_remote_pid_and_client_type() -> None:
    frames = plan.option_plan(6, _info(pg_options=b"-c x=1"))
    opcodes = [frame.opcode for frame in frames]
    assert opcodes[opcodes.index(6) + 1] == plan.HSV2_OPTIONS
    assert opcodes[opcodes.index(plan.HSV2_OPTIONS) + 1] == 8
    options = frames[opcodes.index(plan.HSV2_OPTIONS)]
    assert options.payload == b"-c x=1\x00"


def test_remote_pid_is_sent_once() -> None:
    # The driver used to send REMOTE_PID twice on the default path.
    for version in (2, 3, 4, 5, 6):
        opcodes = [frame.opcode for frame in plan.option_plan(version, _info())]
        assert opcodes.count(plan.HSV2_REMOTE_PID) == 1


@pytest.mark.parametrize("current,proposed", [(6, 6), (5, 5), (4, 5), (6, 7)])
def test_downgrade_must_be_strictly_lower_and_supported(current: int, proposed: int) -> None:
    with pytest.raises(InterfaceError):
        plan.validate_version_downgrade(current, proposed)


@pytest.mark.parametrize("current,proposed", [(6, 5), (5, 4), (4, 3), (3, 2)])
def test_strict_downgrades_are_accepted(current: int, proposed: int) -> None:
    plan.validate_version_downgrade(current, proposed)


@pytest.mark.parametrize("proposed", [0, 1, 6, 9])
def test_unsupported_proposed_versions_are_rejected(proposed: int) -> None:
    with pytest.raises(InterfaceError):
        plan.validate_version_downgrade(6, proposed)


@pytest.mark.parametrize("data,expected", [
    (b"", b""),
    (b"f", b"Zg"),
    (b"fo", b"Zm8"),
    (b"foo", b"Zm9v"),
    (b"foobar", b"Zm9vYmFy"),
])
def test_unpadded_base64_vectors(data: bytes, expected: bytes) -> None:
    assert plan.unpadded_base64(data) == expected


def test_salt_lengths_and_unknown_methods() -> None:
    assert plan.auth_salt_len(plan.AUTH_REQ_OK) == 0
    assert plan.auth_salt_len(plan.AUTH_REQ_PASSWORD) == 0
    assert plan.auth_salt_len(plan.AUTH_REQ_MD5) == 2
    assert plan.auth_salt_len(plan.AUTH_REQ_SHA256) == 2
    for unknown in (plan.AUTH_REQ_KRB4, plan.AUTH_REQ_KRB5, plan.AUTH_REQ_CRYPT, 99):
        with pytest.raises(InterfaceError, match="Unsupported authentication type"):
            plan.auth_salt_len(unknown)


def test_auth_responses_have_expected_shapes() -> None:
    assert plan.auth_response(plan.AUTH_REQ_OK, b"secret", b"") is None
    assert plan.auth_response(plan.AUTH_REQ_PASSWORD, b"secret", b"") == b"secret\x00"

    md5 = plan.auth_response(plan.AUTH_REQ_MD5, b"secret", b"\x12\x34")
    assert md5 is not None and len(md5) == 23 and md5.endswith(b"\x00")
    assert b"=" not in md5
    expected_md5 = base64.standard_b64encode(
        hashlib.md5(b"\x12\x34secret").digest()).rstrip(b"=") + b"\x00"
    assert md5 == expected_md5

    sha = plan.auth_response(plan.AUTH_REQ_SHA256, b"secret", b"\x12\x34")
    assert sha is not None and len(sha) == 44 and sha.endswith(b"\x00")
    assert b"=" not in sha
    expected_sha = base64.standard_b64encode(
        hashlib.sha256(b"\x12\x34secret").digest()).rstrip(b"=") + b"\x00"
    assert sha == expected_sha


def test_check_salt_reports_the_method_name() -> None:
    plan.check_salt(plan.AUTH_REQ_MD5, b"ab", 2)
    with pytest.raises(InterfaceError, match="malformed MD5 authentication salt"):
        plan.check_salt(plan.AUTH_REQ_MD5, b"a", 2)
    with pytest.raises(InterfaceError, match="malformed SHA256 authentication salt"):
        plan.check_salt(plan.AUTH_REQ_SHA256, b"", 2)
