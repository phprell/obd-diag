"""Dialog „Einrichtung prüfen“: View-Model, erster Start und Übernehmen in die Kopfzeile."""

import gc
from collections.abc import Iterator
from typing import Any

import pytest

pytest.importorskip("PySide6.QtQuick")

from PySide6.QtCore import QMetaObject, QObject, QSettings
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from pytestqt.qtbot import QtBot

from obd_diag.services.setup_check import CheckItem, FoundAdapter, SetupReport, Status
from obd_diag.ui.viewmodels.diagnosis import SETTINGS_APP, SETTINGS_ORG, DiagnosisViewModel
from obd_diag.ui.viewmodels.setup import SETUP_SEEN_KEY, SetupViewModel
from obd_diag.ui.window import load_main_window, set_style
from tests.ui.conftest import FakeBackend, SyncRunner

REPORT = SetupReport(
    [
        CheckItem(Status.OK, "Oberfläche (PySide6) installiert"),
        CheckItem(Status.ERROR, "Nutzer ist nicht in der Gruppe uucp", "Text", "sudo befehl"),
        CheckItem(Status.OK, "/dev/ttyUSB1: ELM327 v1.5 antwortet mit 115200 Baud"),
    ],
    [FoundAdapter("/dev/ttyUSB1", 115200, "ELM327 v1.5")],
)


class Check:
    def __init__(self, report: SetupReport = REPORT, error: Exception | None = None) -> None:
        self.report = report
        self.error = error
        self.calls = 0

    def __call__(self) -> SetupReport:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.report


def diagnosis() -> DiagnosisViewModel:
    return DiagnosisViewModel(FakeBackend().as_backend(), SyncRunner())


# --- View-Model ------------------------------------------------------------------------


def test_check_result(qapp: Any) -> None:
    vm = SetupViewModel(SyncRunner(), diagnosis(), Check())
    assert not vm.property("done")
    vm.start()
    assert vm.property("done") and not vm.property("running")
    items = vm.property("items")
    assert [i["status"] for i in items] == ["ok", "error", "ok"]
    assert items[1]["fix"] == "sudo befehl"
    assert vm.property("port") == "/dev/ttyUSB1"
    assert vm.property("baud") == 115200
    assert not vm.property("ready")  # ein Fehler bleibt
    assert vm.property("summary") == "Der Adapter antwortet, aber nicht alles ist eingerichtet."


def test_ready_and_no_adapter(qapp: Any) -> None:
    ok = SetupReport([REPORT.items[0]], REPORT.adapters)
    vm = SetupViewModel(SyncRunner(), diagnosis(), Check(ok))
    vm.start()
    assert vm.property("ready")
    assert vm.property("summary") == "Bereit: /dev/ttyUSB1 mit 115200 Baud."

    vm = SetupViewModel(SyncRunner(), diagnosis(), Check(SetupReport(REPORT.items)))
    vm.start()
    assert vm.property("port") == "" and vm.property("baud") == 0
    assert vm.property("summary").startswith("Kein nutzbarer Adapter gefunden.")


def test_not_while_diagnosis_is_busy(qapp: Any) -> None:
    check = Check()
    diag = diagnosis()
    diag.set_blocked(True)  # Live-Daten laufen
    vm = SetupViewModel(SyncRunner(), diag, check)
    vm.start()
    assert check.calls == 0
    assert vm.property("summary") == "Erst die laufende Abfrage beenden, dann erneut prüfen."


def test_failure_is_shown(qapp: Any) -> None:
    vm = SetupViewModel(SyncRunner(), diagnosis(), Check(error=RuntimeError("kaputt")))
    vm.start()
    assert not vm.property("running") and not vm.property("done")
    assert vm.property("summary") == "Prüfung fehlgeschlagen: kaputt"


def test_first_start_until_seen(qapp: Any) -> None:
    settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
    settings.remove(SETUP_SEEN_KEY)
    assert SetupViewModel.first_start()
    SetupViewModel(SyncRunner(), diagnosis(), Check()).markSeen()
    assert not SetupViewModel.first_start()


# --- QML ------------------------------------------------------------------------------


class Ui:
    def __init__(self, engine: QQmlApplicationEngine) -> None:
        self.engine = engine
        root = engine.rootObjects()[0]
        assert isinstance(root, QQuickWindow)
        self.window = root

    def find(self, name: str) -> QObject:
        obj = self.window.findChild(QObject, name)
        assert obj is not None, name
        return obj


def _load(show_on_start: bool, check: Check) -> tuple[QQmlApplicationEngine, Ui]:
    set_style()
    vm = diagnosis()
    engine = QQmlApplicationEngine()
    setup = SetupViewModel(SyncRunner(), vm, check, show_on_start=show_on_start, parent=engine)
    load_main_window(engine, vm, setup=setup)
    return engine, Ui(engine)


@pytest.fixture
def first_start(qapp: Any) -> Iterator[tuple[Ui, Check]]:
    QSettings(SETTINGS_ORG, SETTINGS_APP).remove(SETUP_SEEN_KEY)
    check = Check()
    engine, ui = _load(True, check)
    yield ui, check
    del ui, engine
    gc.collect()


def test_opens_and_checks_on_first_start(first_start: tuple[Ui, Check], qtbot: QtBot) -> None:
    ui, check = first_start
    dialog = ui.find("setupDialog")
    qtbot.waitUntil(lambda: dialog.property("opened") is True)
    assert check.calls == 1
    assert ui.find("setupItems").property("count") == 3


def test_apply_sets_port_and_baud(first_start: tuple[Ui, Check], qtbot: QtBot) -> None:
    ui, _check = first_start
    dialog = ui.find("setupDialog")
    qtbot.waitUntil(lambda: dialog.property("opened") is True)
    apply = ui.find("setupApply")
    assert apply.property("visible")
    QMetaObject.invokeMethod(apply, "clicked")
    qtbot.waitUntil(lambda: dialog.property("visible") is False)
    assert ui.find("portBox").property("editText") == "/dev/ttyUSB1"
    assert ui.find("baudBox").property("currentValue") == 115200
    # einmal geschlossen: beim nächsten Start nicht mehr von selbst
    assert not SetupViewModel.first_start()


def test_not_shown_after_first_start(qapp: Any, qtbot: QtBot) -> None:
    check = Check()
    engine, ui = _load(False, check)
    dialog = ui.find("setupDialog")
    assert dialog.property("opened") is False
    assert check.calls == 0
    # aus dem Menü: öffnet und prüft
    QMetaObject.invokeMethod(ui.find("setupMenuItem"), "triggered")
    qtbot.waitUntil(lambda: dialog.property("opened") is True)
    assert check.calls == 1
    del ui, engine
    gc.collect()
