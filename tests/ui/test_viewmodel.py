import threading
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtCore")

from pytestqt.qtbot import QtBot

from obd_diag.protocol.elm327 import ElmError, NoConnectionError
from obd_diag.services.clear import ClearRefused, ClearResult
from obd_diag.services.diagnostics import DiagnosticCode, DtcKind, ScanResult
from obd_diag.services.session import Session
from obd_diag.transport import TransportError
from obd_diag.transport.discovery import PortInfo
from obd_diag.ui.jobs import ThreadPoolRunner
from obd_diag.ui.viewmodels.codes import CodeListModel, cost_text
from obd_diag.ui.viewmodels.diagnosis import DiagnosisViewModel
from tests.ui.conftest import P0420, SCAN, FakeBackend, SyncRunner


def _vm(backend: FakeBackend) -> DiagnosisViewModel:
    return DiagnosisViewModel(backend.as_backend(), SyncRunner())


def test_scan_runs_off_the_gui_thread(qtbot: QtBot, fake_backend: FakeBackend) -> None:
    threads: list[int] = []
    original = fake_backend.diagnose

    def diagnose(port: str, baud: int, online_vin_lookup: bool, online_dtc_lookup: bool) -> Session:
        threads.append(threading.get_ident())
        return original(port, baud, online_vin_lookup, online_dtc_lookup)

    fake_backend.diagnose = diagnose  # type: ignore[method-assign]
    runner = ThreadPoolRunner()
    vm = DiagnosisViewModel(fake_backend.as_backend(), runner)
    with qtbot.waitSignal(vm.scanFinished, timeout=5000):
        vm.connectAndScan(" /dev/pts/5 ", 9600)
        assert vm.property("busy")
        assert "/dev/pts/5" in vm.property("busyText")
    runner.wait()
    assert threads and threads[0] != threading.get_ident()
    assert fake_backend.calls == [("diagnose", "/dev/pts/5", 9600)]
    assert not vm.property("busy")
    assert vm.property("hasResult")
    assert vm.property("adapter") == "ELM327 v1.5"
    assert vm.property("protocol") == "ISO 15765-4 (CAN 11/500)"
    assert vm.property("voltageText") == "12,4 V"
    assert not vm.property("lowVoltage")
    assert vm.property("connectedPort") == "/dev/pts/5"
    assert vm.property("codeCount") == 4
    assert vm.property("hasCodes") and vm.property("canClear")


def test_second_scan_is_ignored_while_busy(qtbot: QtBot, fake_backend: FakeBackend) -> None:
    release = threading.Event()
    original = fake_backend.diagnose

    def slow_diagnose(
        port: str, baud: int, online_vin_lookup: bool, online_dtc_lookup: bool
    ) -> Session:
        release.wait(5)
        return original(port, baud, online_vin_lookup, online_dtc_lookup)

    fake_backend.diagnose = slow_diagnose  # type: ignore[method-assign]
    runner = ThreadPoolRunner()
    vm = DiagnosisViewModel(fake_backend.as_backend(), runner)
    with qtbot.waitSignal(vm.scanFinished, timeout=5000):
        vm.connectAndScan("/dev/ttyUSB0", 38400)
        vm.connectAndScan("/dev/ttyUSB1", 38400)
        assert vm.property("busy") and not vm.property("canClear")
        release.set()
    runner.wait()
    assert [c[1] for c in fake_backend.calls] == ["/dev/ttyUSB0"]


def test_codes_are_grouped_by_kind_and_first_is_selected(fake_backend: FakeBackend) -> None:
    vm = _vm(fake_backend)
    vm.connectAndScan("/dev/ttyUSB0", 38400)
    model = vm.property("codes")
    rows = [model.entry(i) for i in range(model.rowCount())]
    assert [(r["code"], r["kindLabel"]) for r in rows if r] == [
        ("P0420", "Gespeichert"),
        ("P1234", "Gespeichert"),
        ("P0420", "Ausstehend"),
        ("U0100", "Permanent"),
    ]
    assert vm.property("selectedIndex") == 0
    assert vm.property("selected")["code"] == "P0420"
    # Permanente Codes löscht Mode 04 nicht, sie stehen nicht im Dialog
    assert vm.property("uniqueCodes") == ["P0420", "P1234"]
    assert vm.property("permanentCount") == 1


def test_selection_and_detail_without_catalog_entry(
    qtbot: QtBot, fake_backend: FakeBackend
) -> None:
    vm = _vm(fake_backend)
    vm.connectAndScan("/dev/ttyUSB0", 38400)
    with qtbot.waitSignal(vm.selectionChanged):
        vm.setProperty("selectedIndex", 1)
    detail = vm.property("selected")
    assert detail["code"] == "P1234"
    assert not detail["hasInfo"]
    assert detail["title"] == "Keine Beschreibung im Katalog"
    assert detail["causes"] == [] and detail["mil"] is None
    vm.setProperty("selectedIndex", 99)  # außerhalb: bleibt
    assert vm.property("selectedIndex") == 1


def test_model_roles() -> None:
    model = CodeListModel()
    model.set_codes([DiagnosticCode("P0420", DtcKind.PENDING, P0420)])
    roles = {bytes(name.data()).decode(): role for role, name in model.roleNames().items()}
    index = model.index(0)
    assert model.data(index, roles["code"]) == "P0420"
    assert model.data(index, roles["kind"]) == "pending"
    assert model.data(index, roles["kindLabel"]) == "Ausstehend"
    assert model.data(index, roles["title"]).startswith("Katalysatorwirkungsgrad")
    assert model.data(index, roles["mil"]) is True
    assert model.data(index, roles["emissionsRelevant"]) is True
    assert model.data(index, roles["difficultyLabel"]) == "schwierig"
    assert model.data(index, roles["costText"]) == "ca. 600–2500 €"  # noqa: RUF001
    assert model.data(index, roles["symptoms"]) == ["Motorkontrollleuchte an"]
    assert model.data(index, roles["causes"]) == [
        {"label": "Katalysator gealtert", "likelihood": "high", "likelihoodLabel": "hoch"},
        {"label": "Abgasleck vor Katalysator", "likelihood": "low", "likelihoodLabel": "niedrig"},
    ]
    assert model.data(model.index(5), roles["code"]) is None


def test_cost_text() -> None:
    assert cost_text(None) == ""
    assert cost_text((60, 250)) == "ca. 60–250 €"  # noqa: RUF001
    assert cost_text((100, 100)) == "ca. 100 €"


def test_empty_scan_and_low_voltage() -> None:
    backend = FakeBackend(ScanResult("ELM327 v2.1", "ISO 14230-4 (KWP FAST)", 11.2))
    vm = _vm(backend)
    vm.connectAndScan("/dev/ttyUSB0", 38400)
    assert vm.property("hasResult") and not vm.property("hasCodes") and not vm.property("canClear")
    assert vm.property("lowVoltage")
    assert vm.property("voltageText") == "11,2 V"
    assert vm.property("selectedIndex") == -1 and vm.property("selected") == {}


def test_unknown_voltage() -> None:
    vm = _vm(FakeBackend(ScanResult("ELM327", "ISO 9141-2", None)))
    vm.connectAndScan("/dev/ttyUSB0", 38400)
    assert vm.property("voltageText") == "unbekannt"
    assert not vm.property("lowVoltage")


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (
            TransportError("/dev/ttyUSB0 lässt sich nicht öffnen (No such file)"),
            "Verbindung fehlgeschlagen: /dev/ttyUSB0 lässt sich nicht öffnen (No such file)",
        ),
        (NoConnectionError("0100: UNABLE TO CONNECT"), "Kein Steuergerät antwortet. Zündung"),
        (ElmError("0100: CAN ERROR"), "Der Adapter meldet einen Fehler: 0100: CAN ERROR"),
        (RuntimeError("kaputt"), "Unerwarteter Fehler (RuntimeError): kaputt"),
    ],
)
def test_scan_errors_are_shown(
    qtbot: QtBot, fake_backend: FakeBackend, error: Exception, message: str
) -> None:
    fake_backend.diagnose_error = error
    runner = ThreadPoolRunner()
    vm = DiagnosisViewModel(fake_backend.as_backend(), runner)
    with qtbot.waitSignal(vm.jobFailed, timeout=5000) as blocker:
        vm.connectAndScan("/dev/ttyUSB0", 38400)
    runner.wait()
    assert blocker.args is not None and blocker.args[0].startswith(message)
    assert vm.property("errorMessage").startswith(message)
    assert not vm.property("busy") and not vm.property("hasResult")
    vm.dismissError()
    assert vm.property("errorMessage") == ""


def test_empty_port_is_rejected(fake_backend: FakeBackend) -> None:
    vm = _vm(fake_backend)
    vm.connectAndScan("  ", 38400)
    assert fake_backend.calls == []
    assert "Port" in vm.property("errorMessage")


def test_ports_and_default_port() -> None:
    vm = _vm(FakeBackend(ports=None))  # Adaptersuche nicht verfügbar
    assert vm.property("ports") == []
    assert vm.property("defaultPort") == "/dev/ttyUSB0"
    backend = FakeBackend(ports=[PortInfo("/dev/rfcomm0", "OBDII (Bluetooth)")])
    vm = _vm(backend)
    assert vm.property("ports") == [{"device": "/dev/rfcomm0", "description": "OBDII (Bluetooth)"}]
    assert vm.property("defaultPort") == "/dev/rfcomm0"
    backend.ports = []
    vm.refreshPorts()
    assert vm.property("ports") == []


def test_catalog_missing() -> None:
    assert _vm(FakeBackend(catalog=False)).catalogMissing
    assert not _vm(FakeBackend()).catalogMissing


def test_clear_without_codes_does_nothing() -> None:
    backend = FakeBackend(ScanResult("ELM327", "CAN", 12.5))
    vm = _vm(backend)
    vm.clearCodes()  # noch kein Scan
    vm.connectAndScan("/dev/ttyUSB0", 38400)
    vm.clearCodes()
    assert [c[0] for c in backend.calls] == ["diagnose"]


def test_clear_success_shows_backup_and_after_scan(qtbot: QtBot, fake_backend: FakeBackend) -> None:
    after = ScanResult("ELM327 v1.5", "CAN", 12.3, [DiagnosticCode("U0100", DtcKind.PERMANENT)])
    fake_backend.clear_result = ClearResult(Path("/var/backup/2026.json"), SCAN, after)
    runner = ThreadPoolRunner()
    vm = DiagnosisViewModel(fake_backend.as_backend(), runner)
    with qtbot.waitSignal(vm.scanFinished, timeout=5000):
        vm.connectAndScan("/dev/pts/7", 38400)
    with qtbot.waitSignal(vm.clearSucceeded, timeout=5000) as blocker:
        vm.clearCodes()
    runner.wait()
    # Nach dem Löschen wird die Diagnose am selben Port neu gelesen
    assert fake_backend.calls[-2:] == [
        ("clear", "/dev/pts/7", 38400),
        ("diagnose", "/dev/pts/7", 38400),
    ]
    assert blocker.args == ["/var/backup/2026.json"]
    assert "Sicherung: /var/backup/2026.json" in vm.property("notice")
    assert "1 permanente(r) Code(s) bleiben" in vm.property("notice")
    assert vm.property("codeCount") == 1 and vm.property("selected")["code"] == "U0100"
    assert vm.property("voltageText") == "12,3 V"
    vm.dismissNotice()
    assert vm.property("notice") == ""


def test_clear_all_codes(fake_backend: FakeBackend) -> None:
    vm = _vm(fake_backend)
    vm.connectAndScan("/dev/ttyUSB0", 38400)
    vm.clearCodes()
    assert vm.property("notice") == "Fehlercodes gelöscht. Sicherung: /tmp/backup.json"
    assert not vm.property("hasCodes") and vm.property("hasResult")


def test_clear_refused_is_reported_separately(qtbot: QtBot, fake_backend: FakeBackend) -> None:
    vm = _vm(fake_backend)
    vm.connectAndScan("/dev/ttyUSB0", 38400)
    fake_backend.clear_error = ClearRefused("Der Motor läuft. Bitte Motor abstellen.")
    with qtbot.waitSignal(vm.clearRefused) as blocker:
        vm.clearCodes()
    assert blocker.args == ["Der Motor läuft. Bitte Motor abstellen."]
    assert vm.property("errorMessage") == "" and not vm.property("busy")
    assert vm.property("codeCount") == 4  # unverändert


def test_clear_transport_error_goes_to_banner(fake_backend: FakeBackend) -> None:
    vm = _vm(fake_backend)
    vm.connectAndScan("/dev/ttyUSB0", 38400)
    fake_backend.clear_error = TransportError("keine Antwort von /dev/ttyUSB0 nach 5.0 s")
    vm.clearCodes()
    assert vm.property("errorMessage").startswith("Verbindung fehlgeschlagen: keine Antwort")


def test_only_permanent_codes_cannot_be_cleared() -> None:
    result = ScanResult("ELM327", "CAN", 12.5, [DiagnosticCode("P0420", DtcKind.PERMANENT)])
    backend = FakeBackend(result)
    vm = _vm(backend)
    vm.connectAndScan("/dev/ttyUSB0", 38400)
    assert vm.property("hasCodes")
    assert not vm.property("canClear")
    vm.clearCodes()
    assert [c[0] for c in backend.calls] == ["diagnose"]


def test_model_online_and_search_roles() -> None:
    from tests.samples import ONLINE_P1234

    model = CodeListModel()
    model.set_codes(
        [
            DiagnosticCode("P0420", DtcKind.PENDING, P0420),
            DiagnosticCode("P1234", DtcKind.PENDING, None, ONLINE_P1234),
        ],
        "Volkswagen (SUV)",
    )
    known, unknown = model.entry(0), model.entry(1)
    assert known is not None and unknown is not None
    assert known["onlineText"] == "" and known["onlineUrl"] == ""
    assert unknown["onlineText"] == "Camshaft Position Actuator Circuit"
    assert unknown["onlineSource"].endswith("volkswagen_codes.txt")
    assert unknown["onlineUrl"] == ONLINE_P1234.url
    assert unknown["searchUrl"] == "https://duckduckgo.com/?q=P1234+Volkswagen"
