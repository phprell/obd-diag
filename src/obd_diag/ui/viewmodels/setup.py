"""View-Model des Dialogs „Einrichtung prüfen“ (Optionen-Menü und erster Start)."""

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Property, QObject, QSettings, Signal, Slot

from obd_diag.i18n import tr
from obd_diag.services.setup_check import SetupReport, run_setup_check
from obd_diag.ui.jobs import JobRunner
from obd_diag.ui.viewmodels.diagnosis import SETTINGS_APP, SETTINGS_ORG

# Gesetzt, sobald der Dialog einmal geschlossen wurde; vorher öffnet er sich beim Start
SETUP_SEEN_KEY = "einrichtung/geprueft"


class SetupViewModel(QObject):
    """Führt ``run_setup_check`` im Worker-Thread der Diagnose aus.

    Derselbe ``runner`` wie Diagnose und Live-Daten: so hängt nie mehr als ein Job am
    Adapter. Solange die Diagnose oder die Live-Daten laufen (``busy``/``blocked`` des
    View-Models der Diagnose), startet die Prüfung nicht.
    """

    stateChanged = Signal()

    def __init__(
        self,
        runner: JobRunner,
        diagnosis: QObject,
        check: Callable[[], SetupReport] = run_setup_check,
        *,
        show_on_start: bool = False,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._runner = runner
        self._diagnosis = diagnosis
        self._check = check
        self._show_on_start = show_on_start
        self._running = False
        self._report: SetupReport | None = None
        self._error = ""

    @classmethod
    def first_start(cls, settings: QSettings | None = None) -> bool:
        """True, solange der Dialog noch nie geschlossen wurde."""
        settings = settings or QSettings(SETTINGS_ORG, SETTINGS_APP)
        return settings.value(SETUP_SEEN_KEY, False, type=bool) is not True

    @Property(bool, constant=True)
    def showOnStart(self) -> bool:
        return self._show_on_start

    @Property(bool, notify=stateChanged)
    def running(self) -> bool:
        return self._running

    @Property(bool, notify=stateChanged)
    def done(self) -> bool:
        return self._report is not None

    @Property(list, notify=stateChanged)
    def items(self) -> list[dict[str, Any]]:
        if self._report is None:
            return []
        return [
            {
                "status": item.status.value,
                "title": item.title,
                "detail": item.detail,
                "fix": item.fix,
            }
            for item in self._report.items
        ]

    @Property(bool, notify=stateChanged)
    def ready(self) -> bool:
        return self._report is not None and self._report.ok

    @Property(str, notify=stateChanged)
    def port(self) -> str:
        adapter = self._report.adapter if self._report is not None else None
        return adapter.port if adapter is not None else ""

    @Property(int, notify=stateChanged)
    def baud(self) -> int:
        adapter = self._report.adapter if self._report is not None else None
        return adapter.baud if adapter is not None else 0

    @Property(str, notify=stateChanged)
    def summary(self) -> str:
        if self._error:
            return self._error
        if self._running:
            return tr("Prüfe Adapter und Zugriffsrechte …")
        if self._report is None:
            return ""
        adapter = self._report.adapter
        if adapter is None:
            return tr("Kein nutzbarer Adapter gefunden. Die Punkte unten nennen die Abhilfe.")
        if self._report.ok:
            return tr("Bereit: {port} mit {baud} Baud.").format(
                port=adapter.port, baud=adapter.baud
            )
        return tr("Der Adapter antwortet, aber nicht alles ist eingerichtet.")

    @Slot()
    def start(self) -> None:
        if self._running:
            return
        if self._diagnosis.property("busy") or self._diagnosis.property("blocked"):
            self._error = tr("Erst die laufende Abfrage beenden, dann erneut prüfen.")
            self.stateChanged.emit()
            return
        self._running = True
        self._error = ""
        self.stateChanged.emit()
        self._runner.run(self._check, self._finished, self._failed)

    @Slot()
    def markSeen(self) -> None:
        """Nach dem ersten Schließen öffnet sich der Dialog nicht mehr von selbst."""
        settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
        settings.setValue(SETUP_SEEN_KEY, True)
        settings.sync()

    def _finished(self, report: Any) -> None:
        self._running = False
        self._report = report
        self.stateChanged.emit()

    def _failed(self, error: Exception) -> None:
        self._running = False
        self._report = None
        self._error = tr("Prüfung fehlgeschlagen: {error}").format(error=error)
        self.stateChanged.emit()
