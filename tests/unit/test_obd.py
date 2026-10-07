import pytest

from obd_diag.protocol.elm327 import Elm327, ElmError
from obd_diag.protocol.obd import read_dtcs
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
