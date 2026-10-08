import pytest

from obd_diag.protocol.dtc_decode import NegativeDtcResponse, decode_dtc, parse_dtc_response


@pytest.mark.parametrize(
    ("high", "low", "code"),
    [
        (0x01, 0x33, "P0133"),
        (0x03, 0x00, "P0300"),
        (0x41, 0x23, "C0123"),
        (0x9A, 0xBC, "B1ABC"),
        (0xC1, 0x00, "U0100"),
    ],
)
def test_decode_dtc(high: int, low: int, code: str) -> None:
    assert decode_dtc(high, low) == code


def test_legacy_response_skips_padding() -> None:
    assert parse_dtc_response("43 01 33 00 00 00 00") == ["P0133"]


def test_legacy_response_multiple_ecus() -> None:
    response = "43 01 33 03 00 00 00\n43 C1 00 00 00 00 00"
    assert parse_dtc_response(response) == ["P0133", "P0300", "U0100"]


def test_can_response_uses_count_byte() -> None:
    assert parse_dtc_response("43 02 01 33 03 00", can=True) == ["P0133", "P0300"]


def test_pending_codes_mode_07() -> None:
    assert parse_dtc_response("4701330000000000", mode=0x07) == ["P0133"]


def test_wrong_mode_raises() -> None:
    with pytest.raises(ValueError):
        parse_dtc_response("41 00 BE 3F A8 13")


def test_can_multi_frame_with_byte_count() -> None:
    response = "008\n0: 43 03 01 33 03 00\n1: 01 71 00 00 00 00 00"
    assert parse_dtc_response(response, can=True) == ["P0133", "P0300", "P0171"]


def test_can_multi_frame_without_spaces_and_second_ecu() -> None:
    response = "008\n0:430301330300\n1:01710000000000\n4301C100"
    assert parse_dtc_response(response, can=True) == ["P0133", "P0300", "P0171", "U0100"]


def test_can_ignores_padding_after_counted_codes() -> None:
    # Füllbyte wie bei Renault (AndrOBD #283) darf nicht als Code gelesen werden
    assert parse_dtc_response("43 01 01 33 AA", can=True) == ["P0133"]
    assert parse_dtc_response("47 01 04 20 55 55", mode=0x07, can=True) == ["P0420"]


def test_can_fewer_codes_than_counted_raises() -> None:
    with pytest.raises(ValueError, match="unvollständig"):
        parse_dtc_response("43 03 01 33 03 00", can=True)


def test_can_no_codes() -> None:
    assert parse_dtc_response("4A00", mode=0x0A, can=True) == []


def test_negative_response_is_skipped() -> None:
    assert parse_dtc_response("7F0A11", mode=0x0A, can=True) == []
    assert parse_dtc_response("7F0311\n43010133", can=True) == ["P0133"]


@pytest.mark.parametrize("nrc", [0x10, 0x21, 0x22, 0x31, 0x33, 0x78])
def test_other_negative_responses_are_not_read_as_no_codes(nrc: int) -> None:
    # Beschäftigt, Bedingungen nicht erfüllt, Antwort folgt …: der Fehlerspeicher ist
    # unbekannt, nicht leer
    with pytest.raises(NegativeDtcResponse) as error:
        parse_dtc_response(f"43010133\n7F03{nrc:02X}", can=True)
    assert (error.value.mode, error.value.nrc) == (0x03, nrc)


def test_negative_response_without_reason_is_not_skipped() -> None:
    with pytest.raises(NegativeDtcResponse):
        parse_dtc_response("7F03", can=True)
