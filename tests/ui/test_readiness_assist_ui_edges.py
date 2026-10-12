"""Readiness-Assistent in der Oberfläche: Grenzfälle von View-Model und Karte."""

from datetime import datetime

import pytest

pytest.importorskip("PySide6.QtQuick")

from PySide6.QtCore import QMetaObject
from pytestqt.qtbot import QtBot

from obd_diag.i18n import set_language, tr
from obd_diag.protocol.elm327 import Elm327
from obd_diag.services.readiness import decode_readiness
from obd_diag.services.readiness_assist import (
    CYCLE_STATE_TEXTS,
    NO_CYCLE_NOTE,
    CycleState,
    ReadinessCheck,
    check_readiness,
    decode_drive_cycle,
)
from obd_diag.transport import TransportError
from obd_diag.ui.viewmodels.diagnosis import DiagnosisViewModel
from obd_diag.ui.viewmodels.session_parts import readiness_assist_entry
from tests.fakes import CAN_CAR_FULL, FakeTransport
from tests.samples import READINESS, full_session
from tests.ui import conftest as ui_conftest
from tests.ui.conftest import FakeBackend, SyncRunner
from tests.ui.test_qml import Ui, _tab, ui  # noqa: F401  (Fixture)

CREATED = datetime.fromisoformat("2026-10-09T17:05:59+02:00")


def _check(pid01: str | None, pid41: str | None) -> ReadinessCheck:
    readiness = None if pid01 is None else decode_readiness(bytes.fromhex(pid01))
    cycle = None
    if readiness is not None and pid41 is not None:
        cycle = decode_drive_cycle(bytes.fromhex(pid41), readiness.compression_ignition)
    return ReadinessCheck(CREATED, "ELM327 v1.5", "ISO 15765-4 (CAN 11/500)", readiness, cycle)


def _vm(backend: FakeBackend) -> DiagnosisViewModel:
    return DiagnosisViewModel(backend.as_backend(), SyncRunner())


# --- Eintrag fürs View-Model -----------------------------------------------------------


def test_checked_text_has_hours_and_minutes() -> None:
    entry = readiness_assist_entry(None, _check("83076521", "00076101"))
    assert entry["checkedText"] == "Stand dieser Fahrt gelesen um 17:05"


def test_check_replaces_the_session_readiness() -> None:
    # Der Stand von PID 01 kommt nach dem Lesen aus der neuen Antwort, nicht der Sitzung
    entry = readiness_assist_entry(READINESS, _check("00076504", "00076100"))
    assert [(i["key"], i["cycleState"]) for i in entry["items"]] == [("evap", "disabled")]
    assert entry["items"][0]["cycleLabel"] == "ausgesetzt"
    assert entry["items"][0]["cycleText"] == CYCLE_STATE_TEXTS[CycleState.DISABLED]


def test_check_without_pid_01_is_not_available() -> None:
    assert readiness_assist_entry(READINESS, _check(None, None)) == {
        "available": False,
        "checked": True,
    }


def test_check_without_pid_41_says_so() -> None:
    entry = readiness_assist_entry(READINESS, _check("83076521", None))
    assert entry["checked"]
    assert [i["cycleState"] for i in entry["items"]] == ["", ""]
    assert [i["cycleText"] for i in entry["items"]] == ["", ""]
    assert NO_CYCLE_NOTE in entry["notes"]


def test_all_complete_has_no_items_and_no_notes() -> None:
    entry = readiness_assist_entry(None, _check("81076500", "00076101"))
    assert entry["available"] and entry["allComplete"]
    assert entry["items"] == [] and entry["notes"] == []


def test_entry_in_english() -> None:
    set_language("en")
    entry = readiness_assist_entry(READINESS, _check("83076521", "00076101"))
    assert entry["checkedText"] == "Status of this drive read at 17:05"
    assert [(i["name"], i["cycleLabel"]) for i in entry["items"]] == [
        ("Catalyst", "incomplete"),
        ("Oxygen sensor", "complete"),
    ]
    assert entry["items"][0]["hint"].startswith("With the engine")
    assert entry["items"][0]["cycleText"] == tr(CYCLE_STATE_TEXTS[CycleState.INCOMPLETE])
    assert entry["items"][0]["cycleText"] != CYCLE_STATE_TEXTS[CycleState.INCOMPLETE]
    assert entry["notes"][0].startswith("Trouble codes are stored")


# --- View-Model --------------------------------------------------------------------------


def test_failed_check_shows_the_error_and_can_be_repeated(
    qtbot: QtBot, fake_backend: FakeBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_backend.session = full_session()
    vm = _vm(fake_backend)
    vm.connectAndScan("/dev/ttyUSB0", 38400)

    def broken(elm: Elm327) -> ReadinessCheck:
        raise TransportError("Verbindung zu /dev/ttyUSB0 unterbrochen")

    monkeypatch.setattr(ui_conftest, "check_readiness", broken)
    vm.checkReadiness()
    assert fake_backend.calls[-1] == ("readiness", "/dev/ttyUSB0", 38400)
    assert vm.property("errorMessage") == (
        "Verbindung fehlgeschlagen: Verbindung zu /dev/ttyUSB0 unterbrochen"
    )
    assert not vm.property("busy")
    assert not vm.property("readinessAssist")["checked"]
    assert vm.property("canCheckReadiness")
    # zweiter Versuch klappt, die Fehlermeldung verschwindet
    monkeypatch.setattr(ui_conftest, "check_readiness", check_readiness)
    vm.checkReadiness()
    assert vm.property("errorMessage") == ""
    assert vm.property("readinessAssist")["checked"]


def test_check_without_pid_01_in_the_view_model(
    fake_backend: FakeBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_backend.session = full_session()
    vm = _vm(fake_backend)
    vm.connectAndScan("/dev/ttyUSB0", 38400)
    responses = {**CAN_CAR_FULL, "0101": "NO DATA"}
    monkeypatch.setattr(
        ui_conftest,
        "check_readiness",
        lambda elm: check_readiness(Elm327(FakeTransport(responses))),
    )
    vm.checkReadiness()
    assert vm.property("readinessAssist") == {"available": False, "checked": True}
    assert vm.property("errorMessage") == ""
    # die Readiness der Sitzung selbst bleibt
    assert vm.property("readiness")["available"]


# --- Karte ----------------------------------------------------------------------------------


def test_card_when_pid_01_is_missing_at_the_check(
    qtbot: QtBot,
    ui: Ui,  # noqa: F811
    fake_backend: FakeBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_backend.session = full_session()
    ui.vm.connectAndScan("/dev/ttyUSB0", 38400)
    _tab(ui, 1)
    qtbot.waitUntil(lambda: ui.prop("readinessAdvice", "count") == 2)
    assert ui.prop("readinessAssistMissing", "visible") is False
    responses = {**CAN_CAR_FULL, "0101": "NO DATA"}
    monkeypatch.setattr(
        ui_conftest,
        "check_readiness",
        lambda elm: check_readiness(Elm327(FakeTransport(responses))),
    )
    QMetaObject.invokeMethod(ui.find("readinessCheckButton"), "clicked")
    qtbot.waitUntil(lambda: ui.prop("readinessAdvice", "count") == 0)
    assert ui.prop("readinessAssistMissing", "visible") is True
    assert ui.prop("readinessAssistDone", "visible") is False
    assert ui.warnings == []


def test_card_when_everything_is_complete(
    qtbot: QtBot,
    ui: Ui,  # noqa: F811
    fake_backend: FakeBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_backend.session = full_session()
    ui.vm.connectAndScan("/dev/ttyUSB0", 38400)
    _tab(ui, 1)
    responses = {**CAN_CAR_FULL, "0101": "41010007 6500"}
    monkeypatch.setattr(
        ui_conftest,
        "check_readiness",
        lambda elm: check_readiness(Elm327(FakeTransport(responses))),
    )
    QMetaObject.invokeMethod(ui.find("readinessCheckButton"), "clicked")
    qtbot.waitUntil(lambda: ui.prop("readinessAssistDone", "visible") is True)
    assert ui.prop("readinessAdvice", "count") == 0
    assert ui.prop("readinessAssistMissing", "visible") is False
    assert ui.warnings == []
