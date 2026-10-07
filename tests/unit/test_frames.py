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


def test_single_frame_of_other_ecu_between_frames() -> None:
    # Ohne Header darf ein Einzel-Frame eines anderen Steuergeräts zwischen den Frames
    # einer mehrteiligen Nachricht stehen; früher brach das die Nachricht auseinander.
    response = "00A\n0:430401330300\n4300\n1:01710420000000"
    assert split_messages(response) == [
        bytes.fromhex("43040133030001710420"),
        bytes.fromhex("4300"),
    ]


def test_truncated_multi_frame_raises() -> None:
    # Vgate iCar Pro mit ``018B1`` (ELMduino #285): 009 angekündigt, nur Frame 0 geliefert.
    # Früher kamen stillschweigend 6 statt 9 Bytes zurück.
    with pytest.raises(ValueError, match="unvollständig"):
        split_messages("009\n0:418B7302C902")


def test_mixed_multi_frames_of_two_ecus_raise() -> None:
    # Datenblatt ELM327DS S. 45 (09 04 von zwei Steuergeräten ohne Header); früher
    # entstanden daraus zwei Nachrichten mit vermischten Daten.
    response = (
        "013\n0: 49 04 01 35 36 30\n1: 32 38 39 34 39 41 43\n"
        "013\n0: 49 04 01 35 36 30\n2: 00 00 00 00 00 00 31\n"
        "1: 32 38 39 35 34 41 43\n2: 00 00 00 00 00 00 00"
    )
    with pytest.raises(ValueError, match="unvollständig"):
        split_messages(response)


@pytest.mark.parametrize(
    "response",
    [
        "00A\n0:430401330300\n2:01710420000000",  # Frame 1 fehlt
        "00A\n0:430401330300\n1:01710420000000\n1:01710420000000",  # doppelt
        "1:01710420000000",  # ohne Anfang
        "0:430301330300\n2:01710000000000",  # ohne Längenzeile, Lücke
    ],
)
def test_frame_sequence_errors_raise(response: str) -> None:
    with pytest.raises(ValueError, match="Frame"):
        split_messages(response)
