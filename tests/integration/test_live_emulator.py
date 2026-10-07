"""Live-Daten gegen den ELM327-Emulator (Szenario ``car``) über ein pty.

Braucht die echte PID-Tabelle (``protocol.pids``); solange sie fehlt, übersprungen.
"""

import threading
import time
from collections.abc import Iterator

import pytest

from obd_diag.protocol import pids
from obd_diag.protocol.elm327 import Elm327, is_read_only
from obd_diag.services.live import LiveSample, prepare_live, run_live, select_pids
from obd_diag.transport.serial import SerialTransport

elm = pytest.importorskip("elm")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not pids.PIDS, reason="protocol.pids noch nicht implementiert"),
]


@pytest.fixture
def car_port() -> Iterator[str]:
    emulator = elm.Elm()
    emulator.set_sorted_obd_msg("car")
    port: str = emulator.get_pty()
    thread = threading.Thread(target=emulator.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while getattr(emulator, "threadState", None) != emulator.THREAD.ACTIVE:
        if time.monotonic() > deadline:
            pytest.fail("Emulator startet nicht")
        time.sleep(0.01)
    yield port
    emulator.terminate()


class RecordingSerial(SerialTransport):
    """Merkt sich jeden gesendeten Befehl."""

    sent: list[str]

    def write(self, data: bytes) -> None:
        self.sent.append(data.decode("ascii").strip())
        super().write(data)


def test_live_against_emulator(car_port: str) -> None:
    samples: list[LiveSample] = []
    transport = RecordingSerial(car_port)
    transport.sent = []
    with transport:
        elm327 = Elm327(transport)
        setup = prepare_live(elm327)
        assert "ELM327" in setup.adapter
        keys = [spec.key for spec in setup.available]
        assert "rpm" in keys
        assert "speed" in keys
        selected = select_pids(setup, None)
        count = run_live(
            elm327,
            selected,
            on_sample=samples.append,
            should_stop=lambda: False,
            interval=0.1,
            max_samples=3,
        )
    assert count == 3
    for sample in samples:
        assert sample.voltage is not None and 9.0 < sample.voltage < 16.0
        assert sample.values["rpm"] is not None
        for spec in selected:
            value = sample.values[spec.key]
            assert value is None or spec.minimum <= value <= spec.maximum, spec.key
    assert transport.sent
    assert all(is_read_only(c) for c in transport.sent), transport.sent
