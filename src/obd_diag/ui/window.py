"""QML-Engine mit dem Hauptfenster aufbauen."""

from pathlib import Path

from PySide6.QtCore import (
    QCoreApplication,
    QLibraryInfo,
    QLocale,
    QObject,
    QTranslator,
    QUrl,
    Slot,
)
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle

from obd_diag.ui.viewmodels.diagnosis import DiagnosisViewModel

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


def load_main_window(engine: QQmlApplicationEngine, vm: DiagnosisViewModel) -> None:
    # Als Kontext-Eigenschaft steht das View-Model schon bereit, bevor die ersten
    # Bindungen ausgewertet werden; Main.qml reicht es an die Teile weiter.
    helper = DialogHelper(engine)
    engine.rootContext().setContextProperty("diagnosis", vm)
    engine.rootContext().setContextProperty("dialogHelper", helper)
    engine.load(QUrl.fromLocalFile(str(MAIN_QML)))
