import pytest

from obd_diag.protocol.frames import split_messages


def test_single_lines_are_messages() -> None:
    assert split_messages("4300\n43 01 01 33\n") == [
        bytes.fromhex("4300"),
        bytes.fromhex("43010133"),
    ]


def test_multi_frame_is_joined_and_truncated() -> None:
    response = "00A\n0: 43 04 01 33 03 00\n1: 01 71 C1 00 00 00 00"
    assert split_messages(response) == [bytes.fromhex("43 04 01 33 03 00 01 71 C1 00")]


def test_multi_frame_without_byte_count() -> None:
    response = "0:430301330300\n1:01710000000000\n0:430101330000\n1:00"
    assert split_messages(response) == [
        bytes.fromhex("43030133030001710000000000"),
        bytes.fromhex("43010133000000"),
    ]


def test_frame_numbers_wrap_inside_long_message() -> None:
    frames = [f"{i % 16:X}:" + "11" * (6 if i == 0 else 7) for i in range(17)]
    length = 6 + 16 * 7
    (message,) = split_messages("\n".join([f"{length:03X}", *frames]))
    assert len(message) == length


def test_two_multi_frame_messages() -> None:
    response = "008\n0:430301330300\n1:01710000000000\n008\n0:470301330300\n1:01710000000000"
    assert [m[0] for m in split_messages(response)] == [0x43, 0x47]


def test_invalid_hex_raises() -> None:
    with pytest.raises(ValueError, match="keine Hex-Daten"):
        split_messages("OK")
