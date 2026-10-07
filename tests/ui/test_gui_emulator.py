"""Ende-zu-Ende: View-Model mit echtem Backend und Worker-Thread gegen den ELM327-Emulator."""

import threading
import time
from collections.abc import Iterator

import pytest

pytest.importorskip("PySide6.QtCore")
elm = pytest.importorskip("elm")
obd_message = pytest.importorskip("elm.obd_message")

from pytestqt.qtbot import QtBot  # noqa: E402

from obd_diag.data.dtc_catalog import DtcCatalog  # noqa: E402
from obd_diag.ui.backend import serial_backend  # noqa: E402
from obd_diag.ui.jobs import ThreadPoolRunner  # noqa: E402
from obd_diag.ui.viewmodels.diagnosis import DiagnosisViewModel  # noqa: E402

pytestmark = pytest.mark.integration


@pytest.fixture
def car_port(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    # Wie in tests/integration/test_scan_emulator.py: Codes über die Modul-Listen setzen
    monkeypatch.setattr(obd_message, "DTC_STORED", ["0133", "0300"])
    monkeypatch.setattr(obd_message, "DTC_PENDING", ["0133"])
    monkeypatch.setattr(obd_message, "DTC_PERMANENT", [])
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


def test_connect_and_scan_against_emulator(
    qtbot: QtBot, car_port: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Unabhängig davon, ob der echte Katalog gebaut ist
    monkeypatch.setattr(DtcCatalog, "default", classmethod(lambda cls: None))
    runner = ThreadPoolRunner()
    vm = DiagnosisViewModel(serial_backend(), runner)
    with qtbot.waitSignal(vm.scanFinished, timeout=20000):
        vm.connectAndScan(car_port, 38400)
        assert vm.property("busy")
    runner.wait()
    assert vm.property("errorMessage") == ""
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


def test_missing_port_against_real_backend(qtbot: QtBot) -> None:
    runner = ThreadPoolRunner()
    vm = DiagnosisViewModel(serial_backend(), runner)
    with qtbot.waitSignal(vm.jobFailed, timeout=5000):
        vm.connectAndScan("/dev/does-not-exist", 38400)
    runner.wait()
    assert vm.property("errorMessage").startswith("Verbindung fehlgeschlagen: /dev/does-not-exist")
