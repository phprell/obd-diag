"""View-Model mit vollständigen Sitzungen: Readiness, Freeze Frame, Fahrzeug, Speichern,
Öffnen und Export."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtCore")

from pytestqt.qtbot import QtBot

from obd_diag.protocol.obd import FreezeFrame
from obd_diag.services.clear import ClearResult
from obd_diag.services.diagnostics import DiagnosticCode, DtcKind, ScanResult
from obd_diag.services.session import load_session
from obd_diag.services.vehicle import VinInfo
from obd_diag.transport import TransportError
from obd_diag.ui.jobs import ThreadPoolRunner
from obd_diag.ui.viewmodels.diagnosis import DiagnosisViewModel, local_path
from obd_diag.ui.viewmodels.session_parts import (
    freeze_frame_entry,
    number_text,
    readiness_entry,
    vehicle_entry,
)
from tests.samples import READINESS, full_session, minimal_session
from tests.ui.conftest import FakeBackend, SyncRunner


def _vm(backend: FakeBackend) -> DiagnosisViewModel:
    return DiagnosisViewModel(backend.as_backend(), SyncRunner())


@pytest.fixture
def full() -> DiagnosisViewModel:
    vm = _vm(FakeBackend(full_session()))
    vm.connectAndScan("/dev/ttyUSB0", 38400)
    return vm


def test_full_session_parts(full: DiagnosisViewModel) -> None:
    assert full.property("codeCount") == 5
    assert full.property("vehicleText") == "Volkswagen · WVWZZZ1KZ6W123456"
    vehicle = full.property("vehicle")
    assert vehicle["available"] and vehicle["vin"] == "WVWZZZ1KZ6W123456"
    facts = {f["label"]: f["value"] for f in vehicle["facts"]}
    assert facts["Hersteller"] == "Volkswagen"
    assert facts["Land"] == "Deutschland"
    assert facts["Modelljahr"] == "2006 (ohne Gewähr)"
    assert facts["Prüfziffer"] == "nicht vorgeschrieben"
    assert vehicle["online"] == [
        {"label": "Modell", "value": "Golf"},
        {"label": "Zylinder", "value": "4"},
    ]

    readiness = full.property("readiness")
    assert readiness["available"] and not readiness["ready"]
    assert readiness["readyLabel"] == "Nicht AU-bereit"
    assert readiness["summary"] == "2 Tests nicht abgeschlossen: Katalysator, Lambdasonde."
    assert readiness["milOn"] and readiness["dtcCount"] == 2
    assert (readiness["completeCount"], readiness["supportedCount"]) == (5, 7)
    monitors = full.property("monitors")
    rows = [monitors.entry(i) for i in range(monitors.rowCount())]
    assert len(rows) == 10
    assert rows[0] == {
        "key": "misfire",
        "name": "Verbrennungsaussetzer",
        "state": "complete",
        "stateLabel": "abgeschlossen",
    }
    labels = {r["key"]: r["stateLabel"] for r in rows if r}
    assert labels["catalyst"] == "nicht abgeschlossen"
    assert labels["egr"] == "nicht unterstützt"

    frame = full.property("freezeFrame")
    assert frame["available"] and not frame["empty"]
    assert frame["dtc"] == "P0300"
    assert frame["dtcTitle"].startswith("Zufällige/mehrfache")
    assert [(r["label"], r["value"], r["unit"]) for r in frame["rows"]] == [
        ("Motorlast", "34,5", "%"),
        ("Kühlmitteltemperatur", "87", "°C"),
        ("Drehzahl", "2.140", "1/min"),
        ("Geschwindigkeit", "63", "km/h"),
    ]
    assert full.property("createdText") == "07.10.2026, 14:32"
    assert full.property("reportBaseName") == "obd-bericht-20261007-1432"


def test_minimal_session_parts_are_unavailable() -> None:
    vm = _vm(FakeBackend(minimal_session()))
    assert vm.property("readiness") == {"available": False}
    assert vm.property("vehicle") == {"available": False}
    assert not vm.property("freezeFrame")["available"]
    vm.connectAndScan("/dev/ttyUSB0", 38400)
    assert vm.property("hasResult") and not vm.property("hasCodes")
    assert vm.property("readiness") == {"available": False}
    assert vm.property("vehicle") == {"available": False}
    assert vm.property("vehicleText") == ""
    assert vm.property("monitors").rowCount() == 0
    frame = vm.property("freezeFrame")
    assert not frame["available"] and frame["rows"] == []


def test_entries_edge_cases() -> None:
    done = tuple(m for m in READINESS.monitors if m.key not in {"catalyst", "oxygen_sensor"})
    ready = readiness_entry(
        replace(READINESS, mil_on=False, compression_ignition=True, monitors=done)
    )
    assert ready["ready"] and ready["readyLabel"] == "AU-bereit"
    assert ready["summary"] == "Alle 5 unterstützten Tests abgeschlossen."
    assert ready["engineLabel"] == "Diesel (Selbstzünder)" and ready["milLabel"] == "aus"
    empty = freeze_frame_entry(FreezeFrame())
    assert empty["available"] and empty["empty"]
    # Code ohne Katalogeintrag: kein Titel, unbekannter Schlüssel bleibt lesbar
    frame = freeze_frame_entry(FreezeFrame("P1234", {}, {"maf": 3.25}), [])
    assert frame["dtcTitle"] == "" and frame["rows"][0]["label"] == "maf"
    assert frame["rows"][0]["value"] == "3,25"
    vin = vehicle_entry(VinInfo("1HGCM82633A004352", True, False, "1HG"))
    facts = {f["label"]: f["value"] for f in vin["facts"]}
    assert facts["Prüfziffer"] == "stimmt nicht" and facts["Hersteller"] == "unbekannt"
    assert vin["checksumOk"] is False and vin["online"] == []
    assert number_text(1234.5, 1) == "1.234,5"


def test_online_vin_lookup_setting_is_passed_and_persisted() -> None:
    backend = FakeBackend(full_session())
    vm = _vm(backend)
    assert vm.property("onlineVinLookup") is False  # Standard: aus
    vm.connectAndScan("/dev/ttyUSB0", 38400)
    vm.setProperty("onlineVinLookup", True)
    vm.connectAndScan("/dev/ttyUSB0", 38400)
    assert backend.online_flags == [False, True]
    # Neues View-Model liest die Einstellung wieder
    assert _vm(FakeBackend()).property("onlineVinLookup") is True
    vm.setProperty("onlineVinLookup", False)
    assert _vm(FakeBackend()).property("onlineVinLookup") is False


def test_clear_rereads_the_diagnosis(qtbot: QtBot) -> None:
    backend = FakeBackend(full_session())
    runner = ThreadPoolRunner()
    vm = DiagnosisViewModel(backend.as_backend(), runner)
    with qtbot.waitSignal(vm.scanFinished, timeout=5000):
        vm.connectAndScan("/dev/ttyUSB0", 38400)
    assert vm.property("freezeFrame")["dtc"] == "P0300"
    with qtbot.waitSignal(vm.clearSucceeded, timeout=5000):
        vm.clearCodes()
        assert vm.property("busy")
    runner.wait()
    assert [c[0] for c in backend.calls] == ["diagnose", "clear", "diagnose"]
    assert not vm.property("busy")
    assert vm.property("notice") == "Fehlercodes gelöscht. Sicherung: /tmp/backup.json"
    # Frisch gelesen: alle unterstützten Monitore offen, Freeze Frame weg, FIN bleibt
    readiness = vm.property("readiness")
    assert not readiness["ready"] and readiness["completeCount"] == 0
    assert vm.property("freezeFrame")["available"]
    assert vm.property("freezeFrame")["empty"]
    assert vm.property("vehicle")["vin"] == "WVWZZZ1KZ6W123456"
    assert not vm.property("hasCodes")


def test_failed_rediagnosis_keeps_after_scan_and_drops_stale_parts() -> None:
    backend = FakeBackend(full_session())
    after = ScanResult("ELM327 v1.5", "CAN", 12.2, [DiagnosticCode("P0420", DtcKind.PERMANENT)])
    backend.clear_result = ClearResult(Path("/tmp/b.json"), backend.session.scan, after)
    original = backend.clear

    def clear_then_unplug(port: str, baud: int) -> ClearResult:
        result = original(port, baud)
        backend.diagnose_error = TransportError("keine Antwort")
        return result

    backend.clear = clear_then_unplug  # type: ignore[method-assign]
    vm = _vm(backend)
    vm.connectAndScan("/dev/ttyUSB0", 38400)
    vm.clearCodes()
    assert not vm.property("busy")
    assert "Sicherung: /tmp/b.json" in vm.property("notice")
    assert "1 permanente(r) Code(s) bleiben" in vm.property("notice")
    assert "nicht neu lesen" in vm.property("errorMessage")
    assert "Verbindung fehlgeschlagen: keine Antwort" in vm.property("errorMessage")
    assert vm.property("codeCount") == 1 and vm.property("voltageText") == "12,2 V"
    assert vm.property("readiness") == {"available": False}
    assert not vm.property("freezeFrame")["available"]
    assert vm.property("vehicle")["vin"] == "WVWZZZ1KZ6W123456"


def test_save_and_open_session(qtbot: QtBot, full: DiagnosisViewModel, tmp_path: Path) -> None:
    assert full.property("canSave")
    with qtbot.waitSignal(full.sessionSaved) as blocker:
        full.saveSession()
    assert blocker.args is not None
    path = Path(blocker.args[0])
    assert path.is_file() and path.name == "session-20261007-143205.json"
    assert json.loads(path.read_text(encoding="utf-8"))["format"] == "obd-diag-session"
    assert load_session(path) == full_session()
    assert full.property("notice") == f"Sitzung gespeichert: {path}"
    assert full.property("sessionPath") == str(path)
    assert not full.property("canSave")  # nicht doppelt speichern
    full.saveSession()
    assert len(list(path.parent.iterdir())) == 1

    vm = _vm(FakeBackend())
    with qtbot.waitSignal(vm.sessionOpened):
        vm.openSession(path.as_uri())  # wie vom Dateidialog: file:///…
    assert vm.property("viewOnly") and vm.property("hasResult")
    assert vm.property("codeCount") == 5
    assert vm.property("vehicleText") == "Volkswagen · WVWZZZ1KZ6W123456"
    assert vm.property("readiness")["readyLabel"] == "Nicht AU-bereit"
    assert "07.10.2026, 14:32 geöffnet (nur ansehen)" in vm.property("notice")
    # Nur ansehen: nicht löschen, nicht erneut speichern, aber exportieren
    assert vm.property("uniqueCodes") and not vm.property("canClear")
    assert not vm.property("canSave") and vm.property("canExport")
    assert vm.property("connectedPort") == ""
    vm.clearCodes()
    assert vm.property("notice").startswith("Gespeicherte Sitzung")
    # Neue Diagnose beendet die Ansicht
    vm.connectAndScan("/dev/ttyUSB0", 38400)
    assert not vm.property("viewOnly") and vm.property("canClear")


def test_open_session_errors(tmp_path: Path) -> None:
    vm = _vm(FakeBackend())
    vm.openSession(str(tmp_path / "fehlt.json"))
    assert vm.property("errorMessage").startswith("Sitzung ließ sich nicht öffnen: ")
    assert "fehlt.json" in vm.property("errorMessage")
    bad = tmp_path / "fremd.json"
    bad.write_text('{"format": "etwas-anderes"}', encoding="utf-8")
    vm.openSession(str(bad))
    assert "Keine obd-diag-Diagnosesitzung" in vm.property("errorMessage")
    assert not vm.property("hasResult")


def test_save_error_goes_to_banner(
    full: DiagnosisViewModel, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocker = tmp_path / "datei"
    blocker.write_text("", encoding="utf-8")
    monkeypatch.setenv("XDG_DATA_HOME", str(blocker))  # Verzeichnis lässt sich nicht anlegen
    full.saveSession()
    assert full.property("errorMessage").startswith("Sitzung ließ sich nicht speichern: ")
    assert full.property("sessionPath") == ""


def test_export_pdf_and_csv_in_worker(qtbot: QtBot, tmp_path: Path) -> None:
    backend = FakeBackend(full_session())
    runner = ThreadPoolRunner()
    vm = DiagnosisViewModel(backend.as_backend(), runner)
    with qtbot.waitSignal(vm.scanFinished, timeout=5000):
        vm.connectAndScan("/dev/ttyUSB0", 38400)
    target = tmp_path / "obd-bericht-20261007-1432.pdf"
    with qtbot.waitSignal(vm.exportFinished, timeout=30000) as blocker:
        vm.exportPdf(target.as_uri())
        assert vm.property("busy") and not vm.property("canExport")
        assert vm.property("busyText") == "Schreibe PDF-Bericht …"
    runner.wait()
    assert blocker.args == [str(target)]
    assert target.read_bytes().startswith(b"%PDF")
    assert vm.property("notice") == f"PDF-Bericht gespeichert: {target}"

    # Ohne Endung: wird ergänzt
    with qtbot.waitSignal(vm.exportFinished, timeout=10000):
        vm.exportCsv(str(tmp_path / "codes"))
    runner.wait()
    csv = tmp_path / "codes.csv"
    assert csv.read_bytes().startswith(b"\xef\xbb\xbfCode;Art;")
    assert [kind for kind, _ in backend.exports] == ["pdf", "csv"]


def test_export_error_goes_to_banner(tmp_path: Path) -> None:
    vm = _vm(FakeBackend(minimal_session()))
    vm.exportPdf(str(tmp_path / "x.pdf"))  # noch keine Sitzung: nichts
    assert not (tmp_path / "x.pdf").exists() and vm.property("errorMessage") == ""
    vm.connectAndScan("/dev/ttyUSB0", 38400)
    vm.exportPdf(str(tmp_path / "fehlt" / "x.pdf"))
    message = vm.property("errorMessage")
    assert message.startswith("PDF-Bericht ließ sich nicht schreiben: ")
    assert not vm.property("busy")


def test_export_from_opened_session(tmp_path: Path) -> None:
    session_file = FakeBackend(full_session()).as_backend().save_session(full_session())
    vm = _vm(FakeBackend())
    vm.openSession(str(session_file))
    vm.exportPdf(str(tmp_path / "bericht.pdf"))
    assert (tmp_path / "bericht.pdf").read_bytes().startswith(b"%PDF")


def test_local_path() -> None:
    assert local_path("file:///tmp/a%20b.pdf") == Path("/tmp/a b.pdf")
    assert local_path("/tmp/x.pdf") == Path("/tmp/x.pdf")
    assert local_path("~/x.pdf") == Path.home() / "x.pdf"
