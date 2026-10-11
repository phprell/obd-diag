"""QML-Engine mit dem Hauptfenster aufbauen."""

import weakref
from pathlib import Path
from typing import Protocol

from PySide6.QtCore import (
    Property,
    QCoreApplication,
    QLibraryInfo,
    QLocale,
    QObject,
    QSettings,
    QTranslator,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle

from obd_diag import i18n
from obd_diag.ui.theme import DesignController
from obd_diag.ui.viewmodels.diagnosis import SETTINGS_APP, SETTINGS_ORG, DiagnosisViewModel
from obd_diag.ui.viewmodels.live import LiveViewModel

QML_DIR = Path(__file__).with_name("qml")
MAIN_QML = QML_DIR / "Main.qml"

# Fusion sieht unter jedem Linux-Desktop gleich aus.
STYLE = "Fusion"


def set_style() -> None:
    """Vor dem Laden der ersten QML-Datei aufrufen; später wirkt es nicht mehr."""
    if QQuickStyle.name() != STYLE:
        QQuickStyle.setStyle(STYLE)


LANGUAGE_KEY = "ui/sprache"
_LOCALES = {
    "de": (QLocale.Language.German, QLocale.Country.Germany),
    "en": (QLocale.Language.English, QLocale.Country.UnitedStates),
}


class CatalogTranslator(QTranslator):
    """Übersetzt die ``qsTr``-Texte der QML-Dateien aus demselben Katalog wie den
    Python-Code (``obd_diag.i18n``, ``locale/en.po``); Deutsch bleibt unverändert."""

    def translate(
        self,
        context: str,
        sourceText: str,
        disambiguation: str | None = None,
        n: int = -1,
    ) -> str | None:
        # None (nicht ""): Qt fragt dann die übrigen Übersetzer und nimmt zuletzt den
        # Quelltext; ein leerer Text würde als Übersetzung gelten
        if i18n.language() == i18n.SOURCE_LANGUAGE:
            return None
        translated = i18n.tr(sourceText)
        return translated if translated != sourceText else None

    def isEmpty(self) -> bool:
        return False


def install_translations(app: QCoreApplication, language: str | None = None) -> list[QTranslator]:
    """Stellt die Sprache ein (``obd_diag.i18n``) und lädt Qts eigene Texte
    (Dateidialog: „Speichern“, „Abbrechen“ …) in derselben Sprache.

    Ohne ``language`` die eingestellte Sprache. Liefert die installierten Übersetzer
    für Qts Texte; die Übersetzer gehören der Anwendung und leben so lange wie sie.
    """
    language = language or i18n.language()
    i18n.set_language(language)
    locale = QLocale(*_LOCALES[language])
    QLocale.setDefault(locale)  # auch Datumsangaben im Dateidialog und Zahlen in QML
    folder = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    translators = []
    for name in ("qtbase", "qtdeclarative"):
        translator = QTranslator(app)
        if translator.load(locale, name, "_", folder):
            app.installTranslator(translator)
            translators.append(translator)
    return translators


class Retranslatable(Protocol):
    def retranslate(self) -> None: ...


class LanguageController(QObject):
    """Sprache der Oberfläche (Optionen → Sprache), gespeichert in QSettings.

    Ohne gespeicherte Wahl gilt die Systemsprache: Deutsch, wenn das System deutsch
    ist, sonst Englisch (``i18n.system_language``). Ein Wechsel gilt sofort: Qt-Texte,
    ``qsTr`` in QML (``QQmlEngine.retranslate``) und alle Texte der View-Models;
    Fehlercode-Texte aus dem Katalog erst ab dem nächsten Scan.
    """

    languageChanged = Signal()

    def __init__(self, settings: QSettings, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        stored = settings.value(LANGUAGE_KEY, "")
        self._language = stored if stored in i18n.LANGUAGES else i18n.system_language()
        self._qt_translators: list[QTranslator] = []
        self._catalog = CatalogTranslator(self)
        # Nur schwach gehalten: der Controller gehört der Anwendung und lebt länger
        # als Engine und View-Models; sonst blieben sie bis zum Prozessende stehen
        self._listeners: list[weakref.ref[Retranslatable]] = []
        self._engine: weakref.ref[QQmlApplicationEngine] | None = None
        self._apply()
        app = QCoreApplication.instance()
        if app is not None:
            app.installTranslator(self._catalog)

    def attach(self, engine: QQmlApplicationEngine, *listeners: Retranslatable) -> None:
        """Engine und View-Models, die bei einem Wechsel neu übersetzen
        (``retranslate``)."""
        self._engine = weakref.ref(engine)
        self._listeners = [weakref.ref(listener) for listener in listeners]

    def _apply(self) -> None:
        app = QCoreApplication.instance()
        if app is not None:
            for translator in self._qt_translators:
                app.removeTranslator(translator)
            self._qt_translators = install_translations(app, self._language)
        else:
            i18n.set_language(self._language)

    def _get_language(self) -> str:
        return self._language

    def _set_language(self, language: str) -> None:
        if language not in i18n.LANGUAGES or language == self._language:
            return
        self._language = language
        self._settings.setValue(LANGUAGE_KEY, language)
        self._settings.sync()
        self._apply()
        for ref in self._listeners:
            listener = ref()
            if listener is not None:
                listener.retranslate()
        engine = self._engine() if self._engine is not None else None
        if engine is not None:
            engine.retranslate()
        self.languageChanged.emit()

    language = Property(str, _get_language, _set_language, notify=languageChanged)


class DialogHelper(QObject):
    """Ergänzt den Qt-eigenen Dateidialog, wenn kein Dialog des Systems verfügbar ist.

    Ohne Desktop-Portal oder GTK nimmt Qt Quick einen eigenen Dateidialog, und der
    übernimmt den vorgeschlagenen Dateinamen (``selectedFile``) nur, wenn die Datei
    schon existiert. Dann wird das Namensfeld hier direkt gefüllt. Systemdialoge haben
    kein solches Feld; dort bleibt alles beim Alten.
    """

    @Slot(str)
    def prefillFileName(self, name: str) -> None:
        for win in QGuiApplication.allWindows():
            field = win.findChild(QObject, "fileNameTextField")
            if field is not None and field.property("visible") and not field.property("text"):
                field.setProperty("text", name)


def create_design(parent: QObject | None = None) -> DesignController:
    """Design nach der gespeicherten Wahl (Optionen → Design), Standard: wie das System."""
    return DesignController(QSettings(SETTINGS_ORG, SETTINGS_APP), parent)


def create_language(parent: QObject | None = None) -> LanguageController:
    """Sprache nach der gespeicherten Wahl (Optionen → Sprache), sonst die Systemsprache."""
    return LanguageController(QSettings(SETTINGS_ORG, SETTINGS_APP), parent)


def load_main_window(
    engine: QQmlApplicationEngine,
    vm: DiagnosisViewModel,
    design: DesignController | None = None,
    live: LiveViewModel | None = None,
    language: LanguageController | None = None,
) -> None:
    # Als Kontext-Eigenschaft steht das View-Model schon bereit, bevor die ersten
    # Bindungen ausgewertet werden; Main.qml reicht es an die Teile weiter.
    helper = DialogHelper(engine)
    if design is None:
        design = create_design(engine)
    if live is None:
        # Teilt Backend und Runner der Diagnose: ein Thread, nie zwei Jobs am Adapter
        live = LiveViewModel(vm.backend, vm.runner, vm, engine)
    if language is None:
        language = create_language(engine)
    language.attach(engine, vm, live)
    engine.rootContext().setContextProperty("languageController", language)
    engine.rootContext().setContextProperty("diagnosis", vm)
    engine.rootContext().setContextProperty("live", live)
    engine.rootContext().setContextProperty("dialogHelper", helper)
    engine.rootContext().setContextProperty("designController", design)
    engine.load(QUrl.fromLocalFile(str(MAIN_QML)))
