"""QML-Engine mit dem Hauptfenster aufbauen."""

from pathlib import Path

from PySide6.QtCore import (
    QCoreApplication,
    QLibraryInfo,
    QLocale,
    QObject,
    QSettings,
    QTranslator,
    QUrl,
    Slot,
)
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle

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


def install_translations(app: QCoreApplication) -> list[QTranslator]:
    """Qt-eigene Texte (Dateidialog: „Speichern“, „Abbrechen“ …) auf Deutsch.

    Die Oberfläche selbst ist fest deutsch, also gilt das unabhängig von der
    Systemsprache.
    Die Übersetzer gehören der Anwendung und leben so lange wie sie.
    """
    german = QLocale(QLocale.Language.German, QLocale.Country.Germany)
    QLocale.setDefault(german)  # auch Datumsangaben im Dateidialog
    folder = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    translators = []
    for name in ("qtbase", "qtdeclarative"):
        translator = QTranslator(app)
        if translator.load(german, name, "_", folder):
            app.installTranslator(translator)
            translators.append(translator)
    return translators


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


def load_main_window(
    engine: QQmlApplicationEngine,
    vm: DiagnosisViewModel,
    design: DesignController | None = None,
    live: LiveViewModel | None = None,
) -> None:
    # Als Kontext-Eigenschaft steht das View-Model schon bereit, bevor die ersten
    # Bindungen ausgewertet werden; Main.qml reicht es an die Teile weiter.
    helper = DialogHelper(engine)
    if design is None:
        design = create_design(engine)
    if live is None:
        # Teilt Backend und Runner der Diagnose: ein Thread, nie zwei Jobs am Adapter
        live = LiveViewModel(vm.backend, vm.runner, vm, engine)
    engine.rootContext().setContextProperty("diagnosis", vm)
    engine.rootContext().setContextProperty("live", live)
    engine.rootContext().setContextProperty("dialogHelper", helper)
    engine.rootContext().setContextProperty("designController", design)
    engine.load(QUrl.fromLocalFile(str(MAIN_QML)))
