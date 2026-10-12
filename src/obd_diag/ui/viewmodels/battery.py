"""View-Model des Dialogs „Batterie- und Anlasstest“ (Optionen-Menü).

Der Test läuft als ein Job über denselben ``JobRunner`` wie Diagnose und Live-Daten
(ein Thread, nie zwei Jobs am Adapter). Zwischenstände kommen aus dem Worker-Thread über
ein ``_BatteryRelay`` im GUI-Thread (queued), mit laufender Nummer wie bei den
Live-Daten. Abgebrochen wird über ein ``threading.Event``. Solange der Test läuft, ist
die Diagnose gesperrt (``DiagnosisViewModel.blocked``), sonst stauten sich Scans im
einen Thread.
"""

import threading
from typing import Any

from PySide6.QtCore import Property, QObject, Qt, Signal, Slot

from obd_diag.i18n import decimal, tr
from obd_diag.services.battery import (
    BATTERY_INSTRUCTIONS,
    PHASE_TEXTS,
    BatteryProgress,
    BatteryTestCancelled,
    BatteryTestRefused,
    BatteryTestResult,
    Phase,
    charge_verdict,
    crank_verdict,
    notes,
    overall_rating,
    overall_text,
    rest_verdict,
)
from obd_diag.ui.backend import Backend
from obd_diag.ui.jobs import JobRunner
from obd_diag.ui.viewmodels.diagnosis import DiagnosisViewModel, user_message


class _BatteryRelay(QObject):
    """Lebt im GUI-Thread; Signale aus dem Worker kommen dort an (queued)."""

    progress = Signal(int, object)  # Nummer des Tests, BatteryProgress


class BatteryViewModel(QObject):
    stateChanged = Signal()
    progressChanged = Signal()

    def __init__(
        self,
        backend: Backend,
        runner: JobRunner,
        diagnosis: DiagnosisViewModel,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._backend = backend
        self._runner = runner
        self._diagnosis = diagnosis
        self._relay = _BatteryRelay(self)
        self._relay.progress.connect(self._on_progress, Qt.ConnectionType.QueuedConnection)
        diagnosis.stateChanged.connect(self.stateChanged)
        self._run_id = 0
        self._stop_event = threading.Event()
        self._running = False
        self._progress: BatteryProgress | None = None
        self._result: BatteryTestResult | None = None
        self._error = ""

    # --- Eigenschaften ---------------------------------------------------------

    @Property(bool, notify=stateChanged)
    def running(self) -> bool:
        return self._running

    @Property(bool, notify=stateChanged)
    def canStart(self) -> bool:
        return (
            not self._running
            and not self._diagnosis.property("busy")
            and not self._diagnosis.property("blocked")
        )

    @Property(str, notify=stateChanged)
    def instructions(self) -> str:
        return tr(BATTERY_INSTRUCTIONS)

    @Property(str, notify=progressChanged)
    def phase(self) -> str:
        return self._progress.phase.value if self._running and self._progress else ""

    @Property(str, notify=progressChanged)
    def phaseText(self) -> str:
        if not self._running or self._progress is None:
            return ""
        return tr(PHASE_TEXTS[self._progress.phase])

    @Property(str, notify=progressChanged)
    def voltageText(self) -> str:
        progress = self._progress
        if not self._running or progress is None or progress.voltage is None:
            return ""
        return f"{decimal(progress.voltage)} V"

    @Property(str, notify=progressChanged)
    def remainingText(self) -> str:
        progress = self._progress
        if not self._running or progress is None or progress.remaining is None:
            return ""
        if progress.phase not in (Phase.REST, Phase.START, Phase.RUNNING):
            return ""
        return tr("noch {seconds:.0f} s").format(seconds=progress.remaining)

    @Property(bool, notify=stateChanged)
    def done(self) -> bool:
        return self._result is not None

    @Property(str, notify=stateChanged)
    def error(self) -> str:
        return self._error

    @Property(list, notify=stateChanged)
    def rows(self) -> list[dict[str, str]]:
        result = self._result
        if result is None:
            return []
        rows = [
            (tr("Ruhespannung"), rest_verdict(result)),
            (tr("Anlassen"), crank_verdict(result)),
            (tr("Ladespannung"), charge_verdict(result)),
        ]
        return [
            {"label": label, "rating": verdict.rating.value, "text": verdict.text}
            for label, verdict in rows
        ]

    @Property(str, notify=stateChanged)
    def overallRating(self) -> str:
        return overall_rating(self._result).value if self._result is not None else ""

    @Property(str, notify=stateChanged)
    def overallText(self) -> str:
        return overall_text(self._result) if self._result is not None else ""

    @Property(list, notify=stateChanged)
    def notes(self) -> list[str]:
        return notes(self._result) if self._result is not None else []

    @property
    def result(self) -> BatteryTestResult | None:
        return self._result

    # --- Aktionen --------------------------------------------------------------

    @Slot(str, int)
    def start(self, port: str, baud: int) -> None:
        port = port.strip()
        if self._running:
            return
        if not self.canStart:
            self._error = tr("Erst die laufende Abfrage beenden, dann den Test starten.")
            self.stateChanged.emit()
            return
        if not port:
            self._error = tr("Bitte einen Port angeben, z. B. /dev/ttyUSB0.")
            self.stateChanged.emit()
            return
        self._run_id += 1
        run_id = self._run_id
        stop = threading.Event()
        self._stop_event = stop
        self._running = True
        self._progress = None
        self._result = None
        self._error = ""
        self._diagnosis.set_blocked(True)
        self.stateChanged.emit()
        self.progressChanged.emit()

        relay, backend = self._relay, self._backend

        def job() -> BatteryTestResult:
            return backend.battery_test(
                port,
                baud,
                on_progress=lambda p: relay.progress.emit(run_id, p),
                should_stop=stop.is_set,
            )

        self._runner.run(
            job,
            lambda result: self._done(run_id, result),
            lambda error: self._failed(run_id, error),
        )

    @Slot()
    def cancel(self) -> None:
        """Test abbrechen; der Job hört nach der laufenden Messung auf."""
        if self._running:
            self._stop_event.set()

    def retranslate(self) -> None:
        self.stateChanged.emit()
        self.progressChanged.emit()

    # --- Rückmeldungen (im GUI-Thread) ---------------------------------------

    def _on_progress(self, run_id: int, progress: BatteryProgress) -> None:
        if run_id != self._run_id or not self._running:
            return
        self._progress = progress
        self.progressChanged.emit()

    def _finish(self) -> None:
        self._running = False
        self._diagnosis.set_blocked(False)
        self.progressChanged.emit()

    def _done(self, run_id: int, result: Any) -> None:
        if run_id != self._run_id:
            return
        assert isinstance(result, BatteryTestResult)
        self._finish()
        self._result = result
        self.stateChanged.emit()

    def _failed(self, run_id: int, error: Exception) -> None:
        if run_id != self._run_id:
            return
        self._finish()
        if isinstance(error, (BatteryTestRefused, BatteryTestCancelled)):
            self._error = str(error)
        else:
            self._error = user_message(error)
        self.stateChanged.emit()
