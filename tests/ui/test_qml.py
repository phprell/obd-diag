"""Lädt die echte QML-Oberfläche offscreen und prüft sie über objectName."""

import gc
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("PySide6.QtQuick")

from PySide6.QtCore import Q_ARG, QMetaObject, QObject
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine, QQmlError
from PySide6.QtQuick import QQuickWindow
from pytestqt.qtbot import QtBot

from obd_diag.services.clear import CLEAR_DISABLED_MESSAGE, ClearRefused
from obd_diag.ui.viewmodels.diagnosis import DiagnosisViewModel
from obd_diag.ui.window import load_main_window, set_style
from tests.samples import full_session, minimal_session
from tests.ui.conftest import FakeBackend, SyncRunner


class Ui:
    def __init__(self, engine: QQmlApplicationEngine, vm: DiagnosisViewModel) -> None:
        self.engine = engine
        self.vm = vm
        self.warnings: list[str] = []
        self._window: QQuickWindow | None = None

    @property
    def window(self) -> QQuickWindow:
        # Den Wrapper behalten: stirbt er, erklärt Shiboken auch die per
        # findChild geholten Kinder für gelöscht.
        if self._window is None:
            root = self.engine.rootObjects()[0]
            assert isinstance(root, QQuickWindow)
            self._window = root
        return self._window

    def find(self, name: str) -> QObject:
        obj = self.window.findChild(QObject, name)
        assert obj is not None, name
        return obj

    def prop(self, name: str, prop: str) -> Any:
        return self.find(name).property(prop)


@pytest.fixture
def ui(qapp: Any, fake_backend: FakeBackend) -> Iterator[Ui]:
    set_style()
    vm = DiagnosisViewModel(fake_backend.as_backend(), SyncRunner())
    engine = QQmlApplicationEngine()
    result = Ui(engine, vm)

    def collect(errors: list[QQmlError]) -> None:
        result.warnings.extend(e.toString() for e in errors)

    engine.warnings.connect(collect)
    load_main_window(engine, vm)
    yield result
    # Engine vor dem View-Model abbauen, sonst laufen Bindungen ins Leere. Danach
    # aufräumen, damit Shiboken keine Wrapper gelöschter Objekte wiederverwendet.
    engine.warnings.disconnect(collect)
    result._window = None
    del result.engine, engine
    gc.collect()


def test_main_window_loads_without_warnings(ui: Ui) -> None:
    assert ui.engine.rootObjects()
    assert ui.window.title() == "OBD-Diagnose"
    assert ui.window.width() >= 900 and ui.window.height() >= 600
    assert ui.prop("statusLine", "text") == "Nicht verbunden"
    assert ui.prop("clearButton", "enabled") is False
    assert ui.prop("portBox", "editText") == "/dev/ttyUSB0"
    assert ui.prop("catalogBanner", "visible") is False
    assert ui.warnings == []


def test_scan_fills_list_and_detail(qtbot: QtBot, ui: Ui) -> None:
    ui.vm.connectAndScan(ui.prop("portBox", "editText"), 38400)
    qtbot.waitUntil(lambda: ui.prop("codeList", "count") == 4)
    assert ui.prop("detailCode", "text") == "P0420"
    assert ui.prop("detailTitle", "text").startswith("Katalysatorwirkungsgrad")
    assert ui.prop("detailCost", "text") == "ca. 600–2500 €"  # noqa: RUF001
    assert "Protokoll: ISO 15765-4 (CAN 11/500)" in ui.prop("statusLine", "text")
    assert ui.prop("clearButton", "enabled") is True
    ui.vm.setProperty("selectedIndex", 1)
    qtbot.waitUntil(lambda: ui.prop("detailCode", "text") == "P1234")
    assert ui.prop("codeList", "currentIndex") == 1
    assert ui.warnings == []


@pytest.mark.clear_disabled
def test_clear_button_stays_disabled_while_clearing_is_locked(
    qtbot: QtBot, ui: Ui, fake_backend: FakeBackend
) -> None:
    ui.vm.connectAndScan(ui.prop("portBox", "editText"), 38400)
    qtbot.waitUntil(lambda: ui.prop("codeList", "count") == 4)
    assert ui.vm.property("canClear") is False
    assert ui.prop("clearButton", "enabled") is False
    assert ui.prop("clearTooltip", "text") == CLEAR_DISABLED_MESSAGE
    ui.vm.clearCodes()  # auch direkt aufgerufen: nichts geht an das Backend
    assert [c[0] for c in fake_backend.calls] == ["diagnose"]
    assert ui.warnings == []


def test_clear_dialog_requires_confirmation(
    qtbot: QtBot, ui: Ui, fake_backend: FakeBackend
) -> None:
    ui.vm.connectAndScan("/dev/ttyUSB0", 38400)
    dialog = ui.find("clearDialog")
    dialog.setProperty("visible", True)
    qtbot.waitUntil(lambda: dialog.property("opened") is True)
    accept = ui.find("clearAccept")
    assert accept.property("enabled") is False
    ui.find("clearConfirm").setProperty("checked", True)
    assert accept.property("enabled") is True
    assert [c[0] for c in fake_backend.calls] == ["diagnose"]
    QMetaObject.invokeMethod(dialog, "accept")
    qtbot.waitUntil(lambda: ui.prop("noticeBanner", "visible") is True)
    assert [c[0] for c in fake_backend.calls] == ["diagnose", "clear", "diagnose"]
    assert "Sicherung: /tmp/backup.json" in ui.prop("noticeBanner", "text")
    assert ui.prop("codeList", "count") == 0
    assert ui.warnings == []


def test_clear_refused_opens_dialog(qtbot: QtBot, ui: Ui, fake_backend: FakeBackend) -> None:
    ui.vm.connectAndScan("/dev/ttyUSB0", 38400)
    fake_backend.clear_error = ClearRefused("Der Motor läuft.")
    ui.vm.clearCodes()
    refused = ui.find("refusedDialog")
    qtbot.waitUntil(lambda: refused.property("visible") is True)
    assert refused.property("message") == "Der Motor läuft."
    assert ui.warnings == []


def test_error_banner(qtbot: QtBot, ui: Ui) -> None:
    ui.vm.connectAndScan("", 38400)
    qtbot.waitUntil(lambda: ui.prop("errorBanner", "visible") is True)
    assert "Port" in ui.prop("errorBanner", "text")
    assert ui.warnings == []


def _tab(ui: Ui, index: int) -> None:
    ui.find("viewTabs").setProperty("currentIndex", index)


def test_tabs_show_session_parts(qtbot: QtBot, ui: Ui, fake_backend: FakeBackend) -> None:
    fake_backend.session = full_session()
    ui.vm.connectAndScan("/dev/ttyUSB0", 38400)
    qtbot.waitUntil(lambda: ui.prop("codeList", "count") == 5)
    assert ui.prop("vehicleHeader", "text") == "Volkswagen · WVWZZZ1KZ6W123456"
    assert ui.prop("vehicleHeader", "visible") is True
    _tab(ui, 1)
    qtbot.waitUntil(lambda: ui.prop("readinessHeadline", "visible") is True)
    assert ui.prop("readinessHeadline", "text") == "Nicht alle Tests abgeschlossen"
    assert "keine AU-Bewertung" in ui.prop("readinessNote", "text")
    assert ui.prop("monitorRepeater", "count") == 10
    assert ui.prop("readinessEmpty", "visible") is False
    _tab(ui, 2)
    qtbot.waitUntil(lambda: ui.prop("freezeDtc", "visible") is True)
    assert ui.prop("freezeDtc", "text") == "P0300"
    assert ui.prop("freezeRows", "count") == 4
    _tab(ui, 3)
    qtbot.waitUntil(lambda: ui.prop("vehicleVin", "visible") is True)
    assert ui.prop("vehicleVin", "text") == "WVWZZZ1KZ6W123456"
    assert ui.prop("vehicleFacts", "count") == 5
    assert ui.prop("vehicleOnline", "count") == 2
    ui.find("onlineVinCheck").setProperty("checked", True)
    QMetaObject.invokeMethod(ui.find("onlineVinCheck"), "toggled")
    assert ui.vm.property("onlineVinLookup") is True
    assert ui.prop("onlineVinMenuItem", "checked") is True
    assert ui.warnings == []


@pytest.mark.parametrize(
    ("index", "name"), [(1, "readinessEmpty"), (2, "freezeEmpty"), (3, "vehicleEmpty")]
)
def test_tabs_empty_states(
    qtbot: QtBot, ui: Ui, fake_backend: FakeBackend, index: int, name: str
) -> None:
    _tab(ui, index)
    qtbot.waitUntil(lambda: ui.prop(name, "visible") is True)
    assert ui.prop(name, "title") == "Noch nicht verbunden"
    fake_backend.session = minimal_session()
    ui.vm.connectAndScan("/dev/ttyUSB0", 38400)
    qtbot.waitUntil(lambda: ui.prop(name, "title") != "Noch nicht verbunden")
    assert ui.prop(name, "title") == "Nicht verfügbar – Steuergerät hat nicht geantwortet"  # noqa: RUF001
    assert ui.prop(name, "visible") is True
    assert ui.prop("vehicleHeader", "visible") is False
    assert ui.warnings == []


def test_opened_session_is_view_only(qtbot: QtBot, ui: Ui, fake_backend: FakeBackend) -> None:
    path = fake_backend.as_backend().save_session(full_session())
    ui.vm.openSession(path.as_uri())
    qtbot.waitUntil(lambda: ui.prop("codeList", "count") == 5)
    assert ui.prop("clearButton", "enabled") is False
    assert ui.prop("clearTooltip", "text") == "nur bei verbundenem Fahrzeug"
    assert ui.prop("saveButton", "enabled") is False
    assert ui.prop("pdfButton", "enabled") is True
    assert ui.prop("statusLine", "text").startswith("Sitzung vom 07.10.2026, 14:32")
    assert ui.prop("scanButton", "text") == "Verbinden && Scannen"
    assert "nur ansehen" in ui.prop("noticeBanner", "text")
    assert ui.warnings == []


def test_save_button_shows_path(qtbot: QtBot, ui: Ui) -> None:
    ui.vm.connectAndScan("/dev/ttyUSB0", 38400)
    assert ui.prop("saveButton", "enabled") is True
    QMetaObject.invokeMethod(ui.find("saveButton"), "click")
    qtbot.waitUntil(lambda: ui.prop("noticeBanner", "visible") is True)
    assert ui.prop("noticeBanner", "text").startswith("Sitzung gespeichert: ")
    assert ui.prop("saveButton", "enabled") is False
    assert ui.warnings == []


def test_export_dialog_suggests_name_and_writes_pdf(
    qtbot: QtBot, ui: Ui, fake_backend: FakeBackend, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Startordner des Dialogs (Dokumente bzw. Home) liegt hier in tmp_path
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    fake_backend.session = full_session()
    ui.vm.connectAndScan("/dev/ttyUSB0", 38400)
    dialog = ui.find("exportDialog")
    QMetaObject.invokeMethod(dialog, "start", Q_ARG("QVariant", "pdf"))
    qtbot.waitUntil(lambda: dialog.property("visible") is True)
    assert dialog.property("title") == "Bericht als PDF speichern"
    name = "obd-bericht-20261007-1432.pdf"
    assert dialog.property("selectedFile").toLocalFile() == str(tmp_path / name)

    # Offscreen gibt es keinen Systemdialog; Qt nimmt seinen eigenen. Dort füllt
    # DialogHelper das Namensfeld.
    def name_field_text() -> str | None:
        for win in QGuiApplication.allWindows():
            field = win.findChild(QObject, "fileNameTextField")
            if field is not None:
                text: str = field.property("text")
                return text
        return None

    qtbot.waitUntil(lambda: name_field_text() == name)
    QMetaObject.invokeMethod(dialog, "accept")
    qtbot.waitUntil(lambda: ui.prop("noticeBanner", "visible") is True)
    assert (tmp_path / name).read_bytes().startswith(b"%PDF")
    assert ui.prop("noticeBanner", "text") == f"PDF-Bericht gespeichert: {tmp_path / name}"
    assert ui.warnings == []


def test_detail_shows_online_explanation_and_search(
    qtbot: QtBot, ui: Ui, fake_backend: FakeBackend
) -> None:
    from tests.samples import with_online

    fake_backend.session = with_online(full_session())
    ui.vm.connectAndScan("/dev/ttyUSB0", 38400)
    qtbot.waitUntil(lambda: ui.prop("codeList", "count") == 5)
    assert ui.prop("searchWebButton", "visible") is False  # Code mit Katalogtext
    assert ui.prop("detailOnline", "visible") is False
    ui.vm.setProperty("selectedIndex", 3)  # gespeichert (2), dann ausstehend: P0133, P1234
    qtbot.waitUntil(lambda: ui.prop("detailCode", "text") == "P1234")
    assert ui.prop("detailOnline", "visible") is True
    assert ui.prop("detailOnline", "text") == "Camshaft Position Actuator Circuit"
    assert ui.prop("searchWebButton", "visible") is True
    ui.find("onlineCodesMenuItem").setProperty("checked", True)
    QMetaObject.invokeMethod(ui.find("onlineCodesMenuItem"), "toggled")
    assert ui.vm.property("onlineCodeLookup") is True
    assert ui.warnings == []
