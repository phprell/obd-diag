"""Helles und dunkles Design: folgt dem System oder der Wahl unter Optionen → Design.

``DesignController`` entscheidet, ob dunkel gezeichnet wird, und reicht ``dark`` an
``Theme.qml`` weiter. Dort stehen beide Farbsätze: die eigenen Farben (Hinweise,
Etiketten) und die Palette des Fusion-Stils, die ``Main.qml`` dem Fenster setzt. Qt
Quick übernimmt eine Palette aus ``QGuiApplication.setPalette`` nicht, und ohne
Unterstützung der Plattform (z. B. offscreen, viele X11-Sitzungen) bleibt Fusion
sonst hell. Die Wahl wird in den QSettings gespeichert.
"""

from PySide6.QtCore import Property, QObject, QSettings, Qt, Signal, Slot
from PySide6.QtGui import QGuiApplication

SETTINGS_KEY = "ui/design"
MODES = ("system", "light", "dark")


def system_is_dark() -> bool:
    return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark


class DesignController(QObject):
    """``mode``: ``system``, ``light`` oder ``dark``; ``dark``: tatsächlich dunkel."""

    modeChanged = Signal()
    darkChanged = Signal()

    def __init__(self, settings: QSettings, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        stored = settings.value(SETTINGS_KEY, "system")
        self._mode = stored if stored in MODES else "system"
        self._dark: bool | None = None
        QGuiApplication.styleHints().colorSchemeChanged.connect(self._system_changed)
        self._request_scheme()
        self._apply()

    def _get_mode(self) -> str:
        return self._mode

    def _set_mode(self, mode: str) -> None:
        if mode not in MODES or mode == self._mode:
            return
        self._mode = mode
        self._settings.setValue(SETTINGS_KEY, mode)
        self._settings.sync()
        self.modeChanged.emit()
        self._request_scheme()
        self._apply()

    mode = Property(str, _get_mode, _set_mode, notify=modeChanged)

    def _get_dark(self) -> bool:
        return bool(self._dark)

    dark = Property(bool, _get_dark, notify=darkChanged)

    def _request_scheme(self) -> None:
        # Fensterrahmen und Systemdialoge folgen der Wahl, soweit die Plattform es kann.
        hints = QGuiApplication.styleHints()
        if self._mode == "system":
            hints.unsetColorScheme()
        else:
            dark = self._mode == "dark"
            hints.setColorScheme(Qt.ColorScheme.Dark if dark else Qt.ColorScheme.Light)

    @Slot()
    def _system_changed(self) -> None:
        if self._mode == "system":
            self._apply()

    def _apply(self) -> None:
        dark = self._mode == "dark" or (self._mode == "system" and system_is_dark())
        if dark == self._dark:
            return
        self._dark = dark
        self.darkChanged.emit()
