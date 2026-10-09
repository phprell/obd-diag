import pytest

from obd_diag.protocol.elm327 import Elm327, ElmError, NoDataError
from obd_diag.protocol.obd import (
    FreezeFrame,
    NegativeResponseError,
    clear_dtcs,
    read_dtcs,
    read_freeze_frame,
    read_pid,
    read_rpm,
    read_rpms,
)
from tests.fakes import FakeTransport


def test_no_data_means_no_codes() -> None:
    assert read_dtcs(Elm327(FakeTransport({"07": "NO DATA"})), 0x07, can=True) == []


def test_reads_multi_frame_from_several_ecus() -> None:
    transport = FakeTransport(
        {"03": "008\r0: 43 03 01 33 03 00\r1: 01 71 00 00 00 00 00\r4301C100"}
    )
    codes = read_dtcs(Elm327(transport), 0x03, can=True)
    assert transport.sent == ["03"]
    assert codes == ["P0133", "P0300", "P0171", "U0100"]


def test_response_pending_answered_in_the_same_reply() -> None:
    # ELM327 ab v2.1 wartet selbst auf die Antwort nach 7F 03 78 (Datenblatt S. 45)
    transport = FakeTransport({"03": "7F0378\r43010133"})
    assert read_dtcs(Elm327(transport), 0x03, can=True) == ["P0133"]
    assert transport.sent == ["03"]


def test_response_pending_reads_on_without_sending_again() -> None:
    transport = FakeTransport({"03": "7F0378"}, later={"03": ["43010133"]})
    assert read_dtcs(Elm327(transport), 0x03, can=True) == ["P0133"]
    assert transport.sent == ["03"]


def test_response_pending_of_a_second_ecu() -> None:
    transport = FakeTransport({"03": "43010133\r7F0378"}, later={"03": ["43010300"]})
    assert read_dtcs(Elm327(transport), 0x03, can=True) == ["P0133", "P0300"]


@pytest.mark.parametrize("later", [[], ["NO DATA"], ["7F0378"]])
def test_response_pending_without_answer_is_no_empty_list(later: list[str]) -> None:
    transport = FakeTransport({"03": "43010133\r7F0378"}, later={"03": later})
    with pytest.raises(NegativeResponseError, match="Antwort angekündigt") as error:
        read_dtcs(Elm327(transport), 0x03, can=True, pending_timeout=0.5)
    assert error.value.nrc == 0x78
    assert transport.sent == ["03"]  # nicht erneut angefragt


def test_response_pending_gives_up_after_the_timeout() -> None:
    transport = FakeTransport({"03": "7F0378"}, later={"03": ["43010133"]})
    with pytest.raises(NegativeResponseError):
        read_dtcs(Elm327(transport), 0x03, can=True, pending_timeout=0)
    assert transport.reads == 1


@pytest.mark.parametrize(("nrc", "text"), [(0x21, "beschäftigt"), (0x22, "Bedingungen")])
def test_refused_dtc_request_is_no_empty_list(nrc: int, text: str) -> None:
    transport = FakeTransport({"07": f"7F07{nrc:02X}"})
    with pytest.raises(NegativeResponseError, match=text):
        read_dtcs(Elm327(transport), 0x07, can=True)


def test_unsupported_mode_still_means_no_codes() -> None:
    assert read_dtcs(Elm327(FakeTransport({"0A": "7F0A11"})), 0x0A, can=True) == []


def test_legacy_protocol_with_bus_init() -> None:
    transport = FakeTransport({"03": "BUS INIT: ...OK\r43 01 33 00 00 00 00"})
    assert read_dtcs(Elm327(transport), 0x03, can=False) == ["P0133"]


def test_garbage_becomes_elm_error() -> None:
    with pytest.raises(ElmError, match=r"^03: "):
        read_dtcs(Elm327(FakeTransport({"03": "41 00 BE 3F A8 13"})), 0x03, can=True)


def test_rejects_non_dtc_mode() -> None:
    with pytest.raises(ValueError):
        read_dtcs(Elm327(FakeTransport({})), 0x04, can=True)


@pytest.mark.parametrize("response", ["44", "44\r44", "7F0478\r44", "4 4"])
def test_clear_dtcs_confirmed(response: str) -> None:
    transport = FakeTransport({"04": response})
    clear_dtcs(Elm327(transport))
    assert transport.sent == ["04"]


def test_clear_dtcs_conditions_not_correct() -> None:
    with pytest.raises(NegativeResponseError, match="Bedingungen nicht erfüllt") as info:
        clear_dtcs(Elm327(FakeTransport({"04": "44\r7F0422"})))
    assert (info.value.mode, info.value.nrc) == (0x04, 0x22)


def test_clear_dtcs_unknown_nrc() -> None:
    with pytest.raises(NegativeResponseError, match=r"unbekannter Grund \(Antwort 7F 04 99\)"):
        clear_dtcs(Elm327(FakeTransport({"04": "7F 04 99"})))


@pytest.mark.parametrize(
    ("response", "error"),
    [("NO DATA", NoDataError), ("4300", ElmError), ("OK", ElmError), ("7F0478", ElmError)],
)
def test_clear_dtcs_without_confirmation(response: str, error: type[ElmError]) -> None:
    with pytest.raises(error):
        clear_dtcs(Elm327(FakeTransport({"04": response})))


@pytest.mark.parametrize(
    ("response", "rpm"),
    [("410C0000", 0.0), ("41 0C 1A F8", 1726.0), ("410C0FA0\r410C0000", 1000.0)],
)
def test_read_rpm(response: str, rpm: float) -> None:
    transport = FakeTransport({"010C": response})
    assert read_rpm(Elm327(transport)) == rpm
    assert transport.sent == ["010C"]


@pytest.mark.parametrize("response", ["NO DATA", "7F0112", "410C", "410D00"])
def test_read_rpm_unavailable(response: str) -> None:
    assert read_rpm(Elm327(FakeTransport({"010C": response}))) is None


@pytest.mark.parametrize(
    ("response", "rpms"),
    [
        ("NO DATA", []),
        ("410C0000", [0.0]),
        ("41 0C 1A F8", [1726.0]),
        ("410C0000\r410C0FA0", [0.0, 1000.0]),  # jedes Steuergerät einzeln
        ("410C0000\r410C00", [0.0, None]),  # ein Datenbyte zu wenig
        ("7F010C12\r410C0000", [None, 0.0]),
        ("410D0000", [None]),
        ("420C0000", [None]),
    ],
)
def test_read_rpms(response: str, rpms: list[float | None]) -> None:
    transport = FakeTransport({"010C": response})
    assert read_rpms(Elm327(transport)) == rpms
    assert transport.sent == ["010C"]


def test_read_pid_garbage_is_elm_error() -> None:
    with pytest.raises(ElmError, match=r"^010C: "):
        read_pid(Elm327(FakeTransport({"010C": "OK"})), 0x0C)


def test_freeze_frame() -> None:
    responses = {
        "020200": "4202000133",
        "020400": "42 04 00 80",
        "020500": "42050073",
        "020C00": "420C001AF8",
        "020D00": "420D0032",
    }
    transport = FakeTransport(responses)
    frame = read_freeze_frame(Elm327(transport))
    assert transport.sent == list(responses)
    assert frame == FreezeFrame(
        dtc="P0133",
        raw=responses,
        values={"engine_load_pct": 50.2, "coolant_temp_c": 75, "rpm": 1726.0, "speed_kmh": 50},
    )


def test_freeze_frame_absent_values() -> None:
    responses = {
        "020200": "4202000000",  # kein Freeze Frame gespeichert
        "020400": "NO DATA",
        "020500": "7F0212",
        "020C00": "420C0012",  # zu kurz
        "020D00": "420D01FF",  # anderer Frame
    }
    frame = read_freeze_frame(Elm327(FakeTransport(responses)))
    assert frame.dtc is None
    assert frame.raw == {"020200": "4202000000", "020C00": "420C0012"}
    assert frame.values == {}


def test_freeze_frame_code_from_ecu_that_stored_it() -> None:
    # Mercedes A 180 d (W177): vier Steuergeräte antworten, nur das letzte hat einen
    # Freeze Frame (U1218); die anderen melden 00 00.
    responses = {
        "020200": "4202000000\r4202000000\r4202000000\r420200D218",
        "020400": "42040000",
        "020500": "4205004D",
        "020C00": "420C000000",
        "020D00": "420D0000",
    }
    frame = read_freeze_frame(Elm327(FakeTransport(responses)))
    assert frame.dtc == "U1218"
    assert frame.values == {
        "engine_load_pct": 0.0,
        "coolant_temp_c": 37,
        "rpm": 0.0,
        "speed_kmh": 0,
    }


def test_freeze_frame_garbage_is_elm_error() -> None:
    with pytest.raises(ElmError, match=r"^020200: "):
        read_freeze_frame(Elm327(FakeTransport({"020200": "OK"})))


# Zwei Steuergeräte senden gleichzeitig mehrteilig (wie ELM327DS S. 45): ohne Header
# vermischt (die zweite Längenzeile kommt, bevor die erste Nachricht vollständig ist),
# mit Headern (ATS0, ohne Leerzeichen) eindeutig. 7E9 trifft zuerst ein.
_MIXED_03 = "00A\r0:430401330420\r00A\r0:430401330300\r1:01010102000000\r1:C1000171000000"
_MIXED_03_HEADERS = (
    "7E9100A430401330420\r7E8100A430401330300\r7E92101010102000000\r7E821C1000171000000"
)


def test_mixed_multiframe_is_reread_with_headers() -> None:
    transport = FakeTransport({"03": _MIXED_03}, headers_on={"03": _MIXED_03_HEADERS})
    codes = read_dtcs(Elm327(transport), 0x03, can=True)
    assert transport.sent == ["03", "ATH1", "03", "ATH0"]
    # nach Steuergeräte-Adresse: erst 7E8, dann 7E9
    assert codes == ["P0133", "P0300", "U0100", "P0171", "P0133", "P0420", "P0101", "P0102"]


def test_mixed_multiframe_header_retry_no_data() -> None:
    transport = FakeTransport({"03": _MIXED_03}, headers_on={"03": "NO DATA"})
    with pytest.raises(ElmError, match="NO DATA"):
        read_dtcs(Elm327(transport), 0x03, can=True)
    assert transport.sent[-1] == "ATH0"


def test_header_retry_restores_ath0_on_error() -> None:
    transport = FakeTransport({"03": _MIXED_03}, headers_on={"03": "CAN ERROR"})
    with pytest.raises(ElmError, match="CAN ERROR"):
        read_dtcs(Elm327(transport), 0x03, can=True)
    assert transport.sent == ["03", "ATH1", "03", "ATH0"]


def test_header_retry_with_wrong_code_count_is_elm_error() -> None:
    # Mit Headern sauber getrennt, aber das Zählbyte verspricht mehr Codes als kommen
    short = "7E9100A430901330420\r7E8100A430401330300\r7E92101010102000000\r7E821C1000171000000"
    transport = FakeTransport({"03": _MIXED_03}, headers_on={"03": short})
    with pytest.raises(ElmError, match=r"^03 \(mit Headern\): "):
        read_dtcs(Elm327(transport), 0x03, can=True)
    assert transport.sent == ["03", "ATH1", "03", "ATH0"]


def test_non_hex_answer_is_not_retried_with_headers() -> None:
    transport = FakeTransport({"03": "43 01 33\rGARBAGE"})
    with pytest.raises(ElmError):
        read_dtcs(Elm327(transport), 0x03, can=True)
    assert transport.sent == ["03"]


def test_clear_dtcs_waits_for_final_answer_after_pending() -> None:
    transport = FakeTransport({"04": "7F0478"}, later={"04": ["7F0478", "44"]})
    clear_dtcs(Elm327(transport))
    assert transport.sent == ["04"]  # nichts erneut gesendet
    assert transport.reads == 3


def test_clear_dtcs_pending_then_refused() -> None:
    transport = FakeTransport({"04": "7F 04 78"}, later={"04": ["7F 04 22"]})
    with pytest.raises(NegativeResponseError) as info:
        clear_dtcs(Elm327(transport))
    assert info.value.nrc == 0x22
    assert transport.sent == ["04"]


def test_clear_dtcs_pending_then_timeout() -> None:
    transport = FakeTransport({"04": "7F0478"})
    with pytest.raises(ElmError, match="nicht bestätigt"):
        clear_dtcs(Elm327(transport))
    assert transport.sent == ["04"]


def test_clear_dtcs_pending_respects_total_timeout() -> None:
    transport = FakeTransport({"04": "7F0478"}, later={"04": ["44"]})
    with pytest.raises(ElmError, match="nicht bestätigt"):
        clear_dtcs(Elm327(transport), pending_timeout=0)
    assert transport.reads == 1  # Frist schon um: nicht weitergelesen


def test_clear_dtcs_other_ecu_still_pending_is_confirmed() -> None:
    transport = FakeTransport({"04": "44\r7F0478"})
    clear_dtcs(Elm327(transport))
    assert transport.reads == 2  # einmal nachgelesen, dann Zeit um


def test_freeze_frame_retries_without_frame_number() -> None:
    responses = {
        "020200": "NO DATA",
        "0202": "4202000133",
        "0204": "NO DATA",
        "0205": "420573",  # ohne Frame-Byte in der Antwort
        "020C": "420C001AF8",
        "020D": "7F0212",
    }
    transport = FakeTransport(responses)
    frame = read_freeze_frame(Elm327(transport))
    assert transport.sent == list(responses)
    assert frame == FreezeFrame(
        dtc="P0133",
        raw={"0202": "4202000133", "0205": "420573", "020C": "420C001AF8"},
        values={"coolant_temp_c": 75, "rpm": 1726.0},
    )


# Ohne Eintrag antwortet der Fake mit OK; für den Freeze Frame hier NO DATA.
_NO_FREEZE = {f"02{pid:02X}{frame}": "NO DATA" for pid in (2, 4, 5, 12, 13) for frame in ("", "00")}


def test_freeze_frame_retry_after_subfunction_not_supported() -> None:
    transport = FakeTransport({**_NO_FREEZE, "020200": "7F 02 12", "0202": "42 02 01 33"})
    frame = read_freeze_frame(Elm327(transport))
    assert frame.dtc == "P0133"
    assert frame.raw == {"0202": "42 02 01 33"}
    assert transport.sent == ["020200", "0202", "0204", "0205", "020C", "020D"]


@pytest.mark.parametrize("short", ["NO DATA", "?", "7F0212", "OK"])
def test_freeze_frame_retry_fails_keeps_standard_format(short: str) -> None:
    transport = FakeTransport({**_NO_FREEZE, "0202": short, "020C00": "420C001AF8"})
    frame = read_freeze_frame(Elm327(transport))
    assert transport.sent == ["020200", "0202", "020400", "020500", "020C00", "020D00"]
    assert frame == FreezeFrame(raw={"020C00": "420C001AF8"}, values={"rpm": 1726.0})


def test_freeze_frame_other_negative_answer_does_not_retry() -> None:
    transport = FakeTransport({**_NO_FREEZE, "020200": "7F0211"})
    read_freeze_frame(Elm327(transport))
    assert "0202" not in transport.sent
