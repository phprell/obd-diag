"""Ende-zu-Ende: View-Model mit echtem Backend und Worker-Thread gegen den ELM327-Emulator."""

import threading
import time
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("PySide6.QtCore")
elm = pytest.importorskip("elm")
obd_message = pytest.importorskip("elm.obd_message")

from pytestqt.qtbot import QtBot  # noqa: E402

from obd_diag.data.dtc_catalog import DtcCatalog  # noqa: E402
from obd_diag.services.session import Session  # noqa: E402
from obd_diag.ui.backend import Backend, serial_backend  # noqa: E402
from obd_diag.ui.jobs import ThreadPoolRunner  # noqa: E402
from obd_diag.ui.viewmodels.diagnosis import DiagnosisViewModel  # noqa: E402

pytestmark = pytest.mark.integration


RPM_ZERO = (
    obd_message.HD(obd_message.ECU_R_ADDR_E) + obd_message.SZ("04") + obd_message.DT("41 0C 00 00")
)


@pytest.fixture
def emulator(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    # Wie in tests/integration/test_clear_emulator.py: Codes über die Modul-Listen
    # setzen, gelöschte Codes überstehen ATZ.
    monkeypatch.setattr(obd_message, "DTC_STORED", ["0133", "0300"])
    monkeypatch.setattr(obd_message, "DTC_PENDING", ["0133"])
    monkeypatch.setattr(obd_message, "DTC_PERMANENT", [])
    monkeypatch.setitem(
        obd_message.ObdMessage["car"]["CLEAR_DIAG_TC"],
        "Exec",
        'self.counters["cmd_dtc_cleared"] = self.presets["cmd_dtc_cleared"] = True',
    )
    emu = elm.Elm()
    emu.set_sorted_obd_msg("car")
    emu.port_name = emu.get_pty()
    thread = threading.Thread(target=emu.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while getattr(emu, "threadState", None) != emu.THREAD.ACTIVE:
        if time.monotonic() > deadline:
            pytest.fail("Emulator startet nicht")
        time.sleep(0.01)
    yield emu
    emu.terminate()


@pytest.fixture
def car_port(emulator: Any) -> str:
    port: str = emulator.port_name
    return port


class RecordingBackend:
    """Echtes Backend, merkt sich aber Ausnahmen der Diagnose, damit der Test sie
    mit Traceback wieder auslösen kann (im Worker werden sie zur Meldung)."""

    def __init__(self) -> None:
        self.errors: list[Exception] = []
        self._real = serial_backend()

    def diagnose(self, port: str, baud: int, online: bool, online_codes: bool) -> Session:
        try:
            return self._real.diagnose(port, baud, online, online_codes)
        except Exception as e:
            self.errors.append(e)
            raise

    def as_backend(self) -> Backend:
        return replace(self._real, diagnose=self.diagnose)


def diagnose(qtbot: QtBot, vm: DiagnosisViewModel, backend: RecordingBackend, port: str) -> None:
    vm.connectAndScan(port, 38400)
    assert vm.property("busy")
    qtbot.waitUntil(lambda: not vm.property("busy"), timeout=30000)
    if backend.errors:
        raise backend.errors[0]


@pytest.fixture
def scanned(
    qtbot: QtBot, car_port: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[DiagnosisViewModel]:
    """View-Model nach erfolgreicher Diagnose; Sicherungen landen in ``tmp_path``."""
    monkeypatch.setattr(DtcCatalog, "default", classmethod(lambda cls: None))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    runner = ThreadPoolRunner()
    backend = RecordingBackend()
    vm = DiagnosisViewModel(backend.as_backend(), runner)
    diagnose(qtbot, vm, backend, car_port)
    yield vm
    runner.wait()


def test_connect_and_scan_against_emulator(
    qtbot: QtBot, car_port: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Unabhängig davon, ob der echte Katalog gebaut ist
    monkeypatch.setattr(DtcCatalog, "default", classmethod(lambda cls: None))
    runner = ThreadPoolRunner()
    backend = RecordingBackend()
    vm = DiagnosisViewModel(backend.as_backend(), runner)
    diagnose(qtbot, vm, backend, car_port)
    runner.wait()
    assert vm.property("errorMessage") == ""
    assert vm.property("hasResult") and not vm.property("viewOnly")
    assert "ELM327" in vm.property("adapter")
    assert vm.property("protocol") == "ISO 15765-4 (CAN 11/500)"
    model = vm.property("codes")
    rows = [model.entry(i) for i in range(model.rowCount())]
    assert [(r["code"], r["kindLabel"]) for r in rows if r] == [
        ("P0133", "Gespeichert"),
        ("P0300", "Gespeichert"),
        ("P0133", "Ausstehend"),
    ]
    assert vm.property("selected")["code"] == "P0133"
    assert vm.property("catalogMissing")
    # Der Emulator beantwortet 0101 (Readiness) und 0902 (FIN)
    assert vm.property("readiness")["available"]
    assert vm.property("monitors").rowCount() > 0
    vehicle = vm.property("vehicle")
    assert vehicle["available"] and len(vehicle["vin"]) == 17
    assert vm.property("vehicleText").endswith(vehicle["vin"])


def test_missing_port_against_real_backend(qtbot: QtBot) -> None:
    runner = ThreadPoolRunner()
    vm = DiagnosisViewModel(serial_backend(), runner)
    with qtbot.waitSignal(vm.jobFailed, timeout=5000):
        vm.connectAndScan("/dev/does-not-exist", 38400)
    runner.wait()
    assert vm.property("errorMessage").startswith("Verbindung fehlgeschlagen: /dev/does-not-exist")


def test_clear_refused_while_engine_runs(qtbot: QtBot, scanned: DiagnosisViewModel) -> None:
    with qtbot.waitSignal(scanned.clearRefused, timeout=20000) as blocker:
        scanned.clearCodes()
    assert blocker.args is not None and blocker.args[0].startswith("Motor läuft")
    assert scanned.property("codeCount") == 3


def test_clear_against_emulator(
    qtbot: QtBot, emulator: Any, scanned: DiagnosisViewModel, tmp_path: Path
) -> None:
    emulator.answer["RPM"] = RPM_ZERO
    with qtbot.waitSignal(scanned.clearSucceeded, timeout=20000) as blocker:
        scanned.clearCodes()
    (backup,) = (tmp_path / "obd-diag" / "backups").iterdir()
    assert blocker.args == [str(backup)]
    assert scanned.property("notice") == f"Fehlercodes gelöscht. Sicherung: {backup}"
    assert scanned.property("hasResult") and not scanned.property("hasCodes")
    assert emulator.counters["CLEAR_DIAG_TC"] == 1
    # Nach dem Löschen neu gelesen: Readiness kommt frisch vom Steuergerät
    assert not scanned.property("busy") and scanned.property("errorMessage") == ""
    assert scanned.property("readiness")["available"]
