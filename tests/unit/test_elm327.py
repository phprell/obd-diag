import pytest

from obd_diag.protocol.elm327 import (
    Elm327,
    ElmError,
    NoDataError,
    ObdProtocol,
    UnknownCommandError,
)
from tests.fakes import FakeTransport


def test_initialize_returns_version_and_configures(fake_transport: FakeTransport) -> None:
    elm = Elm327(fake_transport)
    assert elm.initialize() == "ELM327 v1.5"
    assert fake_transport.sent == ["ATZ", "ATE0", "ATL0", "ATS0", "ATH0", "ATSP0"]


def test_echo_is_stripped() -> None:
    transport = FakeTransport({"03": "03\r43 01 33 00 00 00 00"})
    assert Elm327(transport).command("03") == "43 01 33 00 00 00 00"


def test_voltage(fake_transport: FakeTransport) -> None:
    assert Elm327(fake_transport).voltage() == 12.6


def test_no_data_raises() -> None:
    with pytest.raises(NoDataError):
        Elm327(FakeTransport({"07": "NO DATA"})).command("07")
    assert issubclass(NoDataError, ElmError)


def test_query_returns_none_on_no_data() -> None:
    elm = Elm327(FakeTransport({"07": "NO DATA", "03": "4300"}))
    assert elm.query("07") is None
    assert elm.query("03") == "4300"


def test_unknown_command() -> None:
    with pytest.raises(UnknownCommandError):
        Elm327(FakeTransport({"0A": "?"})).query("0A")


def test_searching_line_is_stripped() -> None:
    elm = Elm327(FakeTransport({"0100": "SEARCHING...\r4100BE3FA813"}))
    assert elm.command("0100") == "4100BE3FA813"


@pytest.mark.parametrize("raw", ["BUS INIT: ...OK\r4100BE3FA813", "BUS INIT: ...OK4100BE3FA813"])
def test_bus_init_prefix_is_stripped(raw: str) -> None:
    assert Elm327(FakeTransport({"0100": raw})).command("0100") == "4100BE3FA813"


def test_bus_init_error_raises() -> None:
    with pytest.raises(ElmError, match="BUS INIT"):
        Elm327(FakeTransport({"0100": "BUS INIT: ...ERROR"})).command("0100")


def test_searching_then_unable_to_connect_raises() -> None:
    elm = Elm327(FakeTransport({"0100": "SEARCHING...\rUNABLE TO CONNECT"}))
    with pytest.raises(ElmError, match="UNABLE TO CONNECT"):
        elm.protocol()


def test_protocol_can_auto_detected() -> None:
    transport = FakeTransport(
        {
            "0100": "SEARCHING...\r4100BE3FA813",
            "ATDPN": "A6",
            "ATDP": "AUTO, ISO 15765-4 (CAN 11/500)",
        }
    )
    protocol = Elm327(transport).protocol()
    assert transport.sent == ["0100", "ATDPN", "ATDP"]
    assert protocol == ObdProtocol("6", "ISO 15765-4 (CAN 11/500)")
    assert protocol.is_can


@pytest.mark.parametrize(
    ("dpn", "number", "is_can"),
    [("A3", "3", False), ("5", "5", False), ("8", "8", True), ("A", "A", True), ("AB", "B", True)],
)
def test_protocol_number(dpn: str, number: str, is_can: bool) -> None:
    protocol = Elm327(FakeTransport({"0100": "4100BE3FA813", "ATDPN": dpn})).protocol()
    assert protocol.number == number
    assert protocol.is_can is is_can
