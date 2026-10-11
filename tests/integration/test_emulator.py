"""Tests gegen den ELM327-Emulator über ein virtuelles serielles Gerät (pty)."""

import threading
from collections.abc import Iterator

import pytest

from obd_diag.protocol.elm327 import Elm327
from obd_diag.services.adapter_check import check_adapter
from obd_diag.transport.serial import SerialTransport

elm = pytest.importorskip("elm")

pytestmark = pytest.mark.integration


@pytest.fixture
def emulator_port() -> Iterator[str]:
    emulator = elm.Elm()
    port: str = emulator.get_pty()
    thread = threading.Thread(target=emulator.run, daemon=True)
    thread.start()
    yield port
    emulator.terminate()


def test_initialize_against_emulator(emulator_port: str) -> None:
    with SerialTransport(emulator_port) as transport:
        version = Elm327(transport).initialize()
    assert "ELM327" in version


def test_voltage_against_emulator(emulator_port: str) -> None:
    with SerialTransport(emulator_port) as transport:
        elm327 = Elm327(transport)
        elm327.initialize()
        assert 9.0 < elm327.voltage() < 16.0


def test_adapter_check_against_emulator(emulator_port: str) -> None:
    """Über den echten seriellen Transport; ``AT@1`` mit „@“ geht durchs Leitungsformat.
    Der Emulator kennt ``ATRD`` und ``ATAMC`` nicht, wirkt also wie ein Nachbau."""
    with SerialTransport(emulator_port) as transport:
        report = check_adapter(Elm327(transport))
    assert report.version is not None
    assert report.description == "OBDII to RS232 Interpreter"
    assert report.reply("ATIGN") in ("ON", "OFF")
    assert report.reply("ATRD") is None and report.reply("ATAMC") is None
    assert report.command_set == "1.1"
    assert report.voltage is not None and 9.0 < report.voltage < 16.0
