import pytest

from obd_diag.protocol.dtc_decode import decode_dtc, parse_dtc_response


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
