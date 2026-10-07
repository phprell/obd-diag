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


def test_freeze_frame_garbage_is_elm_error() -> None:
    with pytest.raises(ElmError, match=r"^020200: "):
        read_freeze_frame(Elm327(FakeTransport({"020200": "OK"})))
