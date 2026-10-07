import pytest

from obd_diag.protocol.elm327 import Elm327, ElmError
from tests.fakes import FakeTransport


def test_initialize_returns_version_and_configures(fake_transport: FakeTransport) -> None:
    elm = Elm327(fake_transport)
    assert elm.initialize() == "ELM327 v1.5"
    assert fake_transport.sent == ["ATZ", "ATE0", "ATL0", "ATS0", "ATH0", "ATSP0"]


def test_echo_is_stripped() -> None:
    transport = FakeTransport({})
    transport.responses["03"] = "03\r43 01 33 00 00 00 00"
    assert Elm327(transport).command("03") == "43 01 33 00 00 00 00"


def test_voltage(fake_transport: FakeTransport) -> None:
    assert Elm327(fake_transport).voltage() == 12.6


def test_no_data_raises() -> None:
    with pytest.raises(ElmError):
        Elm327(FakeTransport({"07": "NO DATA"})).command("07")
