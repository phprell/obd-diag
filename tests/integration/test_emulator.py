"""Tests gegen den ELM327-Emulator über ein virtuelles serielles Gerät (pty)."""

import threading
from collections.abc import Iterator

import pytest

from obd_diag.protocol.elm327 import Elm327
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
