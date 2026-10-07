"""Helles/dunkles Design: Steuerung, Speichern in QSettings und Wirkung in QML."""

import gc
from collections.abc import Iterator
from typing import Any

import pytest

pytest.importorskip("PySide6.QtQuick")

from PySide6.QtCore import QMetaObject, QObject, QSettings
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from pytestqt.qtbot import QtBot

from obd_diag.ui import theme
from obd_diag.ui.theme import SETTINGS_KEY, DesignController
from obd_diag.ui.viewmodels.diagnosis import (
    SETTINGS_APP,
    SETTINGS_ORG,
    DiagnosisViewModel,
)
from obd_diag.ui.window import create_design, load_main_window, set_style
from tests.samples import full_session
from tests.ui.conftest import FakeBackend, SyncRunner


def _settings() -> QSettings:
    return QSettings(SETTINGS_ORG, SETTINGS_APP)


@pytest.fixture(autouse=True)
def _restore_scheme(qapp: Any) -> Iterator[None]:
    yield
    QGuiApplication.styleHints().unsetColorScheme()


def test_default_follows_system(qapp: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    design = DesignController(_settings())
    assert design.property("mode") == "system"
    # offscreen meldet kein Farbschema: hell
    assert design.property("dark") is False

    monkeypatch.setattr(theme, "system_is_dark", lambda: True)
    with_dark_system = DesignController(_settings())
    assert with_dark_system.property("dark") is True


def test_system_change_is_followed_only_in_system_mode(
    qapp: Any, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    system_dark = [False]
    monkeypatch.setattr(theme, "system_is_dark", lambda: system_dark[0])
    design = DesignController(_settings())
    system_dark[0] = True
    with qtbot.waitSignal(design.darkChanged):
        design._system_changed()  # wie bei colorSchemeChanged
    assert design.property("dark") is True

    design.setProperty("mode", "light")
    assert design.property("dark") is False
    with qtbot.assertNotEmitted(design.darkChanged):
        design._system_changed()
    assert design.property("dark") is False


def test_override_is_saved(qapp: Any, qtbot: QtBot) -> None:
    design = DesignController(_settings())
    with qtbot.waitSignals([design.modeChanged, design.darkChanged]):
        design.setProperty("mode", "dark")
    assert design.property("dark") is True
    assert _settings().value(SETTINGS_KEY) == "dark"
    # beim nächsten Start wieder dunkel
    assert DesignController(_settings()).property("dark") is True

    with qtbot.assertNotEmitted(design.modeChanged):
        design.setProperty("mode", "dark")  # unverändert
        design.setProperty("mode", "lila")  # unbekannt: ignoriert
    assert design.property("mode") == "dark"
    design.setProperty("mode", "system")
    assert _settings().value(SETTINGS_KEY) == "system"


def test_unknown_stored_mode_falls_back_to_system(qapp: Any) -> None:
    _settings().setValue(SETTINGS_KEY, "neon")
    assert DesignController(_settings()).property("mode") == "system"


class _Ui:
    def __init__(self, mode: str) -> None:
        set_style()
        self.backend = FakeBackend(full_session(voltage=11.2))
        self.vm = DiagnosisViewModel(self.backend.as_backend(), SyncRunner())
        self.engine = QQmlApplicationEngine()
        self.warnings: list[str] = []
        self.engine.warnings.connect(lambda errs: self.warnings.extend(e.toString() for e in errs))
        self.design = create_design(self.engine)
        self.design.setProperty("mode", mode)
        load_main_window(self.engine, self.vm, self.design)
        root = self.engine.rootObjects()[0]
        assert isinstance(root, QQuickWindow)
        self.window = root

    def find(self, name: str) -> QObject:
        obj = self.window.findChild(QObject, name)
        assert obj is not None, name
        return obj


@pytest.fixture
def make_ui(qapp: Any) -> Iterator[Any]:
    made: list[_Ui] = []

    def make(mode: str) -> _Ui:
        ui = _Ui(mode)
        made.append(ui)
        return ui

    yield make
    for ui in made:
        del ui.window, ui.engine
    made.clear()
    gc.collect()


def _luminance(color: Any) -> float:
    c = QColor(color)
    return 0.2126 * c.redF() + 0.7152 * c.greenF() + 0.0722 * c.blueF()


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_window_follows_design(make_ui: Any, mode: str) -> None:
    ui = make_ui(mode)
    ui.vm.connectAndScan("/dev/ttyUSB0", 38400)
    dark = mode == "dark"
    background = ui.window.property("color")
    assert (_luminance(background) < 0.2) == dark
    # Hinweisleiste (Spannung niedrig) liest sich: Text heller bzw. dunkler als Grund
    banner = next(
        b
        for b in ui.window.findChildren(QObject)
        if b.property("kind") == "warn" and b.property("visible")
    )
    text, fill = _luminance(banner.property("textColor")), _luminance(banner.property("color"))
    assert (text > fill) == dark
    assert abs(text - fill) > 0.4
    item = ui.find(f"design_{mode}")
    assert item.property("checked") is True
    assert ui.find("design_system").property("checked") is False
    assert ui.warnings == []


def test_switching_via_menu(make_ui: Any, qtbot: QtBot) -> None:
    ui = make_ui("light")
    assert _luminance(ui.window.property("color")) > 0.8
    QMetaObject.invokeMethod(ui.find("design_dark"), "click")
    qtbot.waitUntil(lambda: _luminance(ui.window.property("color")) < 0.2)
    assert ui.design.property("mode") == "dark"
    assert _settings().value(SETTINGS_KEY) == "dark"
    assert ui.find("design_dark").property("checked") is True
    assert ui.find("design_light").property("checked") is False
    QMetaObject.invokeMethod(ui.find("design_system"), "click")
    qtbot.waitUntil(lambda: _luminance(ui.window.property("color")) > 0.8)
    assert ui.warnings == []
