"""Verbindungsabbruch mitten in der Abfrage, über den echten seriellen Transport.

Ein Adapter an einem pty beantwortet Befehle wie ``FakeTransport`` und verschwindet
nach einer festen Zahl von Befehlen:

- ``unplug``: USB-Adapter abgezogen, während das Tool auf eine Antwort wartet (Lesen
  ergibt EIO).
- ``unplug_idle``: abgezogen zwischen zwei Befehlen, gleich nach der letzten Antwort
  (Schreiben scheitert; pyserial meldet das als ``termios.error``).
- ``silent``: Adapter hängt noch am USB, antwortet aber nicht mehr (Stecker vom Auto
  ab, Bluetooth-Verbindung weg).

Erwartet: Abbruch mit verständlicher Meldung, nichts hängt, nichts wird danach
gesendet, bereits Aufgezeichnetes bleibt erhalten, keine halbe Sitzung.
"""

import contextlib
import fcntl
import os
import re
import termios
import threading
import time
import tty
from collections.abc import Iterator
from pathlib import Path

import pytest

from obd_diag import cli
from obd_diag.protocol.elm327 import Elm327
from obd_diag.services.live import LiveSample
from obd_diag.transport import TransportError, TransportTimeout
from obd_diag.ui import backend
from tests.fakes import CAN_CAR_FULL, FakeTransport
from tests.unit.test_command_guard import SCAN
from tests.unit.test_live_safety import RUNNING, SETUP

# Live mit rpm,speed: Runde 0 liest zusätzlich die Bordspannung, die folgenden nicht
FIRST_ROUND = ["ATRV", "010C", "010D"]
NEXT_ROUND = ["010C", "010D"]


def _live_commands(rounds: int) -> int:
    """Befehle bis einschließlich Runde ``rounds - 1`` (rounds < 10)."""
    return len(SETUP) + len(FIRST_ROUND) + (rounds - 1) * len(NEXT_ROUND)


class VanishingAdapter:
    """ELM327 am pty, der nach ``after`` beantworteten Befehlen verschwindet."""

    def __init__(self, responses: dict[str, str], after: int, mode: str) -> None:
        self.master, slave = os.openpty()
        tty.setraw(slave)
        self.device = os.ttyname(slave)
        self._slave = slave
        self.fake = FakeTransport(responses)
        self.after = after
        self.mode = mode
        self.received: list[str] = []  # alle Befehle, auch nach dem Verschwinden
        self.gone = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        buffer = b""
        while not self._stop.is_set():
            try:
                chunk = os.read(self.master, 256)
            except OSError:
                return
            if not chunk:
                return
            buffer += chunk
            while b"\r" in buffer:
                line, buffer = buffer.split(b"\r", 1)
                self.received.append(line.decode("ascii"))
                if len(self.received) > self.after:
                    self.gone.set()
                    if self.mode == "unplug":
                        os.close(self.master)
                        return
                    continue  # silent: liest weiter, antwortet nie
                self.fake.write(line + b"\r")
                os.write(self.master, self.fake.read_until(b">", 1.0))
                if self.mode == "unplug_idle" and len(self.received) == self.after:
                    self._wait_until_read()  # sonst ginge die letzte Antwort mit verloren
                    self.gone.set()
                    os.close(self.master)
                    return

    def _wait_until_read(self) -> None:
        """Wartet, bis das Tool alle gesendeten Bytes vom pty gelesen hat."""
        for _ in range(500):
            waiting = fcntl.ioctl(self._slave, termios.FIONREAD, b"\0\0\0\0")
            if int.from_bytes(waiting, "little") == 0:
                return
            time.sleep(0.002)
        raise AssertionError("Antwort wurde nicht gelesen")

    def close(self) -> None:
        self._stop.set()
        for fd in (self.master, self._slave):
            with contextlib.suppress(OSError):  # schon geschlossen (abgezogen)
                os.close(fd)
        self._thread.join(timeout=2)


@pytest.fixture
def adapter() -> Iterator[list[VanishingAdapter]]:
    made: list[VanishingAdapter] = []
    yield made
    for a in made:
        a.close()


def _adapter(
    made: list[VanishingAdapter], responses: dict[str, str], after: int, mode: str
) -> VanishingAdapter:
    made.append(VanishingAdapter(responses, after, mode))
    return made[-1]


@pytest.fixture(autouse=True)
def _short_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    # Statt 5 s je Befehl: der schweigende Adapter soll den Test nicht aufhalten
    monkeypatch.setattr(cli, "Elm327", lambda transport: Elm327(transport, timeout=0.5))
    monkeypatch.setattr(backend, "Elm327", lambda transport: Elm327(transport, timeout=0.5))


@pytest.fixture(autouse=True)
def _data_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("unplug", "Verbindung zu .* unterbrochen"),
        ("unplug_idle", "Verbindung zu .* unterbrochen"),
        ("silent", "keine Antwort von"),
    ],
)
def test_cli_live_aborts_and_keeps_the_recording(
    mode: str,
    message: str,
    adapter: list[VanishingAdapter],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rounds = 3
    car = _adapter(adapter, RUNNING, _live_commands(rounds), mode)
    record = tmp_path / "live.csv"
    code = cli.main(
        ["live", "--port", car.device, "--pids", "rpm,speed", "--interval", "0.1",
         "--record", str(record)]
    )  # fmt: skip
    err = capsys.readouterr().err
    assert code == 1
    assert re.search(f"Fehler: {message}", err), err
    assert car.gone.is_set()
    # Kopf und alle vollständigen Runden sind auf der Platte (jede Zeile sofort geschrieben)
    lines = record.read_text(encoding="utf-8-sig").splitlines()
    assert len(lines) == 1 + rounds
    assert lines[1].split(";")[1:3] == ["1726", "50"]
    # Nach dem Verschwinden kam höchstens noch der eine unbeantwortete Befehl
    assert len(car.received) == car.after + (mode != "unplug_idle")


@pytest.mark.parametrize("mode", ["unplug", "unplug_idle", "silent"])
def test_cli_diagnose_aborts_without_saving_half_a_session(
    mode: str, adapter: list[VanishingAdapter], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Scan beantwortet, die folgende Readiness-Abfrage (0101) nicht mehr
    car = _adapter(adapter, CAN_CAR_FULL, len(SCAN), mode)
    assert cli.main(["diagnose", "--port", car.device, "--save"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("Fehler: ") and ("unterbrochen" in err or "keine Antwort" in err), err
    assert not (tmp_path / "data" / "obd-diag" / "sessions").exists()
    assert len(car.received) == car.after + (mode != "unplug_idle")


@pytest.mark.parametrize(
    ("mode", "error"),
    [("unplug", TransportError), ("unplug_idle", TransportError), ("silent", TransportTimeout)],
)
def test_gui_backend_live_aborts_and_keeps_the_recording(
    mode: str, error: type[TransportError], adapter: list[VanishingAdapter]
) -> None:
    car = _adapter(adapter, RUNNING, _live_commands(2), mode)
    samples: list[LiveSample] = []
    started: list[Path | None] = []
    with pytest.raises(error):
        backend.live_port(
            car.device,
            38400,
            ["rpm", "speed"],
            0.1,
            True,
            on_setup=lambda setup: None,
            on_start=lambda pids, path: started.append(path),
            on_sample=samples.append,
            should_stop=lambda: False,
        )
    assert len(samples) == 2
    (path,) = started
    assert path is not None
    assert len(path.read_text(encoding="utf-8-sig").splitlines()) == 1 + 2


@pytest.mark.parametrize("mode", ["unplug", "unplug_idle", "silent"])
def test_gui_backend_diagnose_aborts(mode: str, adapter: list[VanishingAdapter]) -> None:
    car = _adapter(adapter, CAN_CAR_FULL, len(SCAN), mode)
    with pytest.raises(TransportError):
        backend.diagnose_port(car.device, 38400)
    assert len(car.received) == car.after + (mode != "unplug_idle")
