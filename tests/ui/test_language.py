"""Sprachwahl der Oberfläche (Optionen → Sprache): Standard, Speichern, Umschalten."""

import gc
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("PySide6.QtQuick")

from PySide6.QtCore import QLocale, QMetaObject, QObject, QSettings
from PySide6.QtQml import QQmlApplicationEngine, QQmlError
from PySide6.QtQuick import QQuickWindow
from pytestqt.qtbot import QtBot

from obd_diag import i18n
from obd_diag.ui.viewmodels.diagnosis import DiagnosisViewModel
from obd_diag.ui.window import LANGUAGE_KEY, LanguageController, load_main_window, set_style
from tests.ui.conftest import FakeBackend, SyncRunner


@pytest.fixture
def settings(tmp_path: Path) -> QSettings:
    return QSettings(str(tmp_path / "sprache.conf"), QSettings.Format.IniFormat)


@pytest.fixture(autouse=True)
def _restore_locale(qapp: Any) -> Iterator[None]:
    locale = QLocale()
    yield
    QLocale.setDefault(locale)


@pytest.mark.parametrize(("env", "expected"), [("de_DE.UTF-8", "de"), ("en_GB.UTF-8", "en")])
def test_default_follows_system(
    qapp: Any, settings: QSettings, monkeypatch: pytest.MonkeyPatch, env: str, expected: str
) -> None:
    for name in ("LANGUAGE", "LC_ALL", "LC_MESSAGES"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LANG", env)
    controller = LanguageController(settings)
    assert controller.property("language") == expected
    assert i18n.language() == expected


def test_stored_choice_wins_and_unknown_falls_back(qapp: Any, settings: QSettings) -> None:
    settings.setValue(LANGUAGE_KEY, "en")
    assert LanguageController(settings).property("language") == "en"
    settings.setValue(LANGUAGE_KEY, "klingonisch")
    assert LanguageController(settings).property("language") == "de"  # LANGUAGE=de in Tests


def test_change_is_saved_and_applied(qapp: Any, qtbot: QtBot, settings: QSettings) -> None:
    controller = LanguageController(settings)
    with qtbot.waitSignal(controller.languageChanged):
        controller.setProperty("language", "en")
    assert settings.value(LANGUAGE_KEY) == "en"
    assert i18n.language() == "en"
    assert QLocale().language() == QLocale.Language.English
    assert i18n.tr("Abbrechen") == "Cancel"
    with qtbot.assertNotEmitted(controller.languageChanged):
        controller.setProperty("language", "en")  # unverändert
        controller.setProperty("language", "fr")  # unbekannt: ignoriert
    assert controller.property("language") == "en"


class _Window:
    def __init__(self, engine: QQmlApplicationEngine) -> None:
        root = engine.rootObjects()[0]
        assert isinstance(root, QQuickWindow)
        self.root = root

    def prop(self, name: str, prop: str) -> Any:
        obj = self.root.findChild(QObject, name)
        assert obj is not None, name
        return obj.property(prop)

    def forget(self) -> None:
        # Den Wrapper loslassen, damit die Engine wirklich abgebaut wird
        del self.root

    def click(self, name: str) -> None:
        obj = self.root.findChild(QObject, name)
        assert obj is not None, name
        QMetaObject.invokeMethod(obj, "click")


def test_switching_retranslates_open_window(
    qapp: Any, qtbot: QtBot, settings: QSettings, fake_backend: FakeBackend
) -> None:
    set_style()
    vm = DiagnosisViewModel(fake_backend.as_backend(), SyncRunner())
    engine = QQmlApplicationEngine()
    warnings: list[str] = []

    def collect(errors: list[QQmlError]) -> None:
        warnings.extend(e.toString() for e in errors)

    engine.warnings.connect(collect)
    language = LanguageController(settings, engine)
    load_main_window(engine, vm, language=language)
    win = _Window(engine)
    try:
        vm.connectAndScan("/dev/ttyUSB0", 38400)
        qtbot.waitUntil(lambda: win.prop("codeList", "count") == 4)
        assert win.root.title() == "OBD-Diagnose"
        assert win.prop("language_de", "checked") is True
        assert win.prop("detailCost", "text") == "ca. 600–2500 €"  # noqa: RUF001

        win.click("language_en")
        qtbot.waitUntil(lambda: win.root.title() == "OBD Diagnostics")
        assert settings.value(LANGUAGE_KEY) == "en"
        assert win.prop("language_en", "checked") is True
        assert win.prop("languageMenu", "title") == "Sprache / Language"
        assert "Protocol: ISO 15765-4" in win.prop("statusLine", "text")
        assert win.prop("detailCost", "text") == "approx. €600–2500"  # noqa: RUF001
        assert win.prop("optionsMenu", "title") == "&Options"

        win.click("language_de")
        qtbot.waitUntil(lambda: win.root.title() == "OBD-Diagnose")
        assert "Protokoll: ISO 15765-4" in win.prop("statusLine", "text")
        assert win.prop("detailCost", "text") == "ca. 600–2500 €"  # noqa: RUF001
        assert warnings == []
    finally:
        engine.warnings.disconnect(collect)
        win.forget()
        del engine
        gc.collect()
