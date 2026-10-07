"""QML-Engine mit dem Hauptfenster aufbauen."""

from pathlib import Path

from PySide6.QtCore import QUrl
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


def load_main_window(engine: QQmlApplicationEngine, vm: DiagnosisViewModel) -> None:
    # Als Kontext-Eigenschaft steht das View-Model schon bereit, bevor die ersten
    # Bindungen ausgewertet werden; Main.qml reicht es an die Teile weiter.
    engine.rootContext().setContextProperty("diagnosis", vm)
    engine.load(QUrl.fromLocalFile(str(MAIN_QML)))
