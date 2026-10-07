"""Lädt die echte QML-Oberfläche offscreen und prüft sie über objectName."""

import gc
from collections.abc import Iterator
from typing import Any

import pytest

pytest.importorskip("PySide6.QtQuick")

from PySide6.QtCore import QMetaObject, QObject
from PySide6.QtQml import QQmlApplicationEngine, QQmlError
from PySide6.QtQuick import QQuickWindow
from pytestqt.qtbot import QtBot

from obd_diag.services.clear import ClearRefused
from obd_diag.ui.viewmodels.diagnosis import DiagnosisViewModel
from obd_diag.ui.window import load_main_window, set_style
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
    assert [c[0] for c in fake_backend.calls] == ["scan"]
    QMetaObject.invokeMethod(dialog, "accept")
    qtbot.waitUntil(lambda: ui.prop("noticeBanner", "visible") is True)
    assert [c[0] for c in fake_backend.calls] == ["scan", "clear"]
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
