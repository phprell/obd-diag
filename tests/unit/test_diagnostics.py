import pytest

from obd_diag.data.dtc_catalog import DtcInfo
from obd_diag.protocol.elm327 import Elm327, ElmError, UnknownCommandError
from obd_diag.services.diagnostics import DiagnosticCode, DtcKind, scan
from tests.fakes import CAN_CAR, FakeCatalog, FakeTransport


def test_scan_can_car_with_catalog() -> None:
    catalog = FakeCatalog({"P0133": "Lambdasonde reagiert zu langsam"})
    result = scan(Elm327(FakeTransport(CAN_CAR)), catalog, "en")
    assert result.adapter == "ELM327 v1.5"
    assert result.protocol == "ISO 15765-4 (CAN 11/500)"
    assert result.voltage == 12.4
    assert not result.low_voltage
    info = DtcInfo("P0133", "Lambdasonde reagiert zu langsam")
    assert result.codes == [
        DiagnosticCode("P0133", DtcKind.STORED, info),
        DiagnosticCode("P0300", DtcKind.STORED),
        DiagnosticCode("P0171", DtcKind.STORED),
        DiagnosticCode("P0133", DtcKind.PENDING, info),
    ]
    # Jeder Code wird nur einmal nachgeschlagen
    assert catalog.lookups == [("P0133", "en"), ("P0300", "en"), ("P0171", "en")]


def test_scan_never_clears_codes() -> None:
    transport = FakeTransport(CAN_CAR)
    scan(Elm327(transport), None)
    assert "04" not in transport.sent
    assert transport.sent[-3:] == ["03", "07", "0A"]


def test_scan_legacy_car_without_mode_0a_and_voltage() -> None:
    transport = FakeTransport(
        {
            "ATZ": "ELM327 v2.1",
            "ATRV": "?",
            "0100": "BUS INIT: ...OK\r41 00 BE 1F B8 10",
            "ATDPN": "A4",
            "ATDP": "AUTO, ISO 14230-4 (KWP FAST)",
            "03": "43 03 00 00 00 00 00",
            "07": "NO DATA",
            "0A": "?",
        }
    )
    result = scan(Elm327(transport), FakeCatalog({}))
    assert result.voltage is None
    assert not result.low_voltage
    assert result.protocol == "ISO 14230-4 (KWP FAST)"
    assert result.codes == [DiagnosticCode("P0300", DtcKind.STORED)]


def test_scan_without_codes_and_low_voltage() -> None:
    responses = CAN_CAR | {"ATRV": "11.2V", "03": "4300", "07": "4700", "0A": "4A00"}
    result = scan(Elm327(FakeTransport(responses)), None)
    assert result.codes == []
    assert result.low_voltage


def test_unknown_mode_03_is_an_error() -> None:
    with pytest.raises(UnknownCommandError):
        scan(Elm327(FakeTransport(CAN_CAR | {"03": "?"})), None)


def test_scan_defaults_to_german() -> None:
    catalog = FakeCatalog({})
    scan(Elm327(FakeTransport(CAN_CAR)), catalog)
    assert {lang for _, lang in catalog.lookups} == {"de"}


def test_scan_does_not_report_no_codes_when_an_ecu_refuses() -> None:
    # Früher übersprungen: 7F 03 78 ergab „keine gespeicherten Codes“
    transport = FakeTransport({**CAN_CAR, "03": "7F0378"})
    with pytest.raises(ElmError, match="7F 03 78"):
        scan(Elm327(transport), None)
