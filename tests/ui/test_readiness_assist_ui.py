"""Readiness-Assistent in der Oberfläche: View-Model und Karte im Readiness-Tab."""

import pytest

pytest.importorskip("PySide6.QtQuick")

from PySide6.QtCore import QMetaObject
from pytestqt.qtbot import QtBot

from obd_diag.protocol.elm327 import Elm327
from obd_diag.services.readiness_assist import HINTS, check_readiness
from obd_diag.ui.viewmodels.diagnosis import DiagnosisViewModel
from obd_diag.ui.viewmodels.session_parts import readiness_assist_entry
from tests.fakes import CAN_CAR_FULL, FakeTransport
from tests.samples import READINESS, full_session
from tests.ui.conftest import FakeBackend, SyncRunner
from tests.ui.test_qml import Ui, _tab, ui  # noqa: F401  (Fixture)


def _vm(backend: FakeBackend) -> DiagnosisViewModel:
    return DiagnosisViewModel(backend.as_backend(), SyncRunner())


def test_entry_from_the_session_has_hints_but_no_drive_status() -> None:
    entry = readiness_assist_entry(READINESS, None)
    assert entry["available"] and not entry["checked"] and not entry["allComplete"]
    assert [i["key"] for i in entry["items"]] == ["catalyst", "oxygen_sensor"]
    assert entry["items"][0]["hint"] == HINTS["catalyst"]
    assert entry["items"][0]["cycleState"] == "" and entry["items"][0]["cycleLabel"] == ""
    # PID 41 ist noch nicht gefragt: kein Hinweis, das Fahrzeug melde ihn nicht
    assert not any("PID 41" in n for n in entry["notes"])
    assert readiness_assist_entry(None, None) == {"available": False, "checked": False}


def test_entry_after_the_check() -> None:
    check = check_readiness(Elm327(FakeTransport(CAN_CAR_FULL)))
    entry = readiness_assist_entry(READINESS, check)
    assert entry["checked"]
    assert entry["checkedText"].startswith("Stand dieser Fahrt gelesen um ")
    states = [(i["key"], i["cycleState"], i["cycleLabel"]) for i in entry["items"]]
    assert states == [
        ("catalyst", "incomplete", "noch offen"),
        ("oxygen_sensor", "complete", "abgeschlossen"),
    ]
    assert any("Einschalten der Zündung" in n for n in entry["notes"])


def test_check_reads_at_the_scanned_port(qtbot: QtBot, fake_backend: FakeBackend) -> None:
    fake_backend.session = full_session()
    vm = _vm(fake_backend)
    assert not vm.property("canCheckReadiness")  # noch keine Diagnose
    vm.checkReadiness()
    assert fake_backend.calls == []
    vm.connectAndScan("/dev/ttyUSB1", 115200)
    assert vm.property("canCheckReadiness")
    assert not vm.property("readinessAssist")["checked"]
    vm.checkReadiness()
    assert fake_backend.calls[-1] == ("readiness", "/dev/ttyUSB1", 115200)
    assert not vm.property("busy")
    assert vm.property("readinessAssist")["items"][0]["cycleState"] == "incomplete"
    # neue Diagnose: der Stand der Fahrt gehört zur alten
    vm.connectAndScan("/dev/ttyUSB1", 115200)
    assert not vm.property("readinessAssist")["checked"]


def test_no_check_for_an_opened_session_or_while_live_runs(fake_backend: FakeBackend) -> None:
    vm = _vm(fake_backend)
    path = fake_backend.as_backend().save_session(full_session())
    vm.openSession(str(path))
    assert vm.property("readinessAssist")["available"]
    assert not vm.property("canCheckReadiness")
    vm.connectAndScan("/dev/ttyUSB0", 38400)
    vm.set_blocked(True)
    assert not vm.property("canCheckReadiness")
    vm.checkReadiness()
    assert ("readiness", "/dev/ttyUSB0", 38400) not in fake_backend.calls


def test_card_in_the_readiness_tab(qtbot: QtBot, ui: Ui, fake_backend: FakeBackend) -> None:  # noqa: F811
    fake_backend.session = full_session()
    ui.vm.connectAndScan("/dev/ttyUSB0", 38400)
    _tab(ui, 1)
    qtbot.waitUntil(lambda: ui.prop("readinessAdvice", "count") == 2)
    assert ui.prop("readinessCheckButton", "enabled") is True
    assert ui.prop("readinessAssistDone", "visible") is False
    assert "PID 41" in ui.prop("readinessAssistIntro", "text")
    QMetaObject.invokeMethod(ui.find("readinessCheckButton"), "clicked")
    qtbot.waitUntil(lambda: "gelesen um" in ui.prop("readinessAssistIntro", "text"))
    assert ui.warnings == []
