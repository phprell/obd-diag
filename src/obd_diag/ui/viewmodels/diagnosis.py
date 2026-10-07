"""View-Model der Hauptansicht: Verbinden, Scannen, Fehlercodes löschen."""

from pathlib import Path
from typing import Any

from PySide6.QtCore import Property, QObject, Signal, Slot

from obd_diag.protocol.elm327 import ElmError
from obd_diag.services.clear import ClearRefused, ClearResult
from obd_diag.services.diagnostics import DtcKind, ScanResult
from obd_diag.transport import TransportError
from obd_diag.ui.backend import Backend
from obd_diag.ui.jobs import JobRunner
from obd_diag.ui.viewmodels.codes import CodeListModel

DEFAULT_PORT = "/dev/ttyUSB0"
DEFAULT_BAUD = 38400

LOW_VOLTAGE_WARNING = (
    "Batteriespannung niedrig: Ergebnisse können unzuverlässig sein. "
    "Vor dem Löschen Batterie laden oder Ladegerät anschließen."
)


def user_message(error: Exception) -> str:
    """Eine Ausnahme aus einem Job als Meldung für die Oberfläche."""
    if isinstance(error, ClearRefused):
        return str(error)
    if isinstance(error, TransportError):
        return f"Verbindung fehlgeschlagen: {error}"
    if isinstance(error, ElmError):
        return f"Der Adapter meldet einen Fehler: {error}"
    if isinstance(error, ValueError):
        return f"Unerwartete Antwort vom Fahrzeug: {error}"
    if isinstance(error, NotImplementedError):
        return "Diese Funktion ist noch nicht verfügbar."
    return f"Unerwarteter Fehler ({type(error).__name__}): {error}"


class DiagnosisViewModel(QObject):
    """Zustand und Aktionen der Hauptansicht.

    Serielle Arbeit läuft über ``runner`` abseits des GUI-Threads; solange ein Job
    läuft, ist ``busy`` gesetzt und weitere Aktionen werden ignoriert.
    """

    portsChanged = Signal()
    stateChanged = Signal()
    selectionChanged = Signal()
    clearRefused = Signal(str)  # Vorbedingung nicht erfüllt oder vom Steuergerät abgelehnt
    clearSucceeded = Signal(str)  # Pfad der Sicherung
    scanFinished = Signal()
    jobFailed = Signal(str)

    def __init__(self, backend: Backend, runner: JobRunner, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._backend = backend
        self._runner = runner
        self._codes = CodeListModel(self)
        self._ports: list[dict[str, str]] = []
        self._busy = False
        self._busy_text = ""
        self._result: ScanResult | None = None
        self._error = ""
        self._notice = ""
        self._selected = -1
        self._port = DEFAULT_PORT
        self._baud = DEFAULT_BAUD
        try:
            self._catalog_missing = not backend.catalog_available()
        except Exception:
            self._catalog_missing = True
        self.refreshPorts()

    # --- Eigenschaften -------------------------------------------------------

    @Property(list, notify=portsChanged)
    def ports(self) -> list[dict[str, str]]:
        return self._ports

    @Property(str, notify=portsChanged)
    def defaultPort(self) -> str:
        return self._ports[0]["device"] if self._ports else self._port

    @Property(QObject, constant=True)
    def codes(self) -> CodeListModel:
        return self._codes

    @Property(bool, notify=stateChanged)
    def busy(self) -> bool:
        return self._busy

    @Property(str, notify=stateChanged)
    def busyText(self) -> str:
        return self._busy_text

    @Property(bool, notify=stateChanged)
    def hasResult(self) -> bool:
        return self._result is not None

    @Property(str, notify=stateChanged)
    def adapter(self) -> str:
        return self._result.adapter if self._result is not None else ""

    @Property(str, notify=stateChanged)
    def protocol(self) -> str:
        return self._result.protocol if self._result is not None else ""

    @Property(str, notify=stateChanged)
    def voltageText(self) -> str:
        if self._result is None:
            return ""
        if self._result.voltage is None:
            return "unbekannt"
        return f"{self._result.voltage:.1f} V".replace(".", ",")

    @Property(bool, notify=stateChanged)
    def lowVoltage(self) -> bool:
        return self._result is not None and self._result.low_voltage

    @Property(str, constant=True)
    def lowVoltageWarning(self) -> str:
        return LOW_VOLTAGE_WARNING

    @Property(str, notify=stateChanged)
    def connectedPort(self) -> str:
        return self._port if self._result is not None else ""

    @Property(bool, notify=stateChanged)
    def hasCodes(self) -> bool:
        return self._codes.rowCount() > 0

    @Property(int, notify=stateChanged)
    def codeCount(self) -> int:
        return self._codes.rowCount()

    @Property(bool, notify=stateChanged)
    def canClear(self) -> bool:
        return not self._busy and self._codes.rowCount() > 0

    @Property(str, notify=stateChanged)
    def errorMessage(self) -> str:
        return self._error

    @Property(str, notify=stateChanged)
    def notice(self) -> str:
        return self._notice

    @Property(bool, constant=True)
    def catalogMissing(self) -> bool:
        return self._catalog_missing

    @Property(list, notify=stateChanged)
    def uniqueCodes(self) -> list[str]:
        """Die Codes für den Bestätigungsdialog, jeder nur einmal."""
        return list(dict.fromkeys(c.code for c in self._codes.codes))

    def _get_selected_index(self) -> int:
        return self._selected

    def _set_selected_index(self, row: int) -> None:
        if row != self._selected and -1 <= row < self._codes.rowCount():
            self._selected = row
            self.selectionChanged.emit()

    selectedIndex = Property(int, _get_selected_index, _set_selected_index, notify=selectionChanged)

    @Property(dict, notify=selectionChanged)
    def selected(self) -> dict[str, Any]:
        """Der ausgewählte Code für die Detailansicht, leer ohne Auswahl."""
        return self._codes.entry(self._selected) or {}

    # --- Aktionen ------------------------------------------------------------

    @Slot()
    def refreshPorts(self) -> None:
        try:
            found = self._backend.list_ports()
        except Exception:
            found = []  # ohne Adaptersuche bleibt die manuelle Eingabe
        self._ports = [{"device": p.device, "description": p.description} for p in found]
        self.portsChanged.emit()

    @Slot(str, int)
    def connectAndScan(self, port: str, baud: int) -> None:
        port = port.strip()
        if self._busy:
            return
        if not port:
            self._fail("Bitte einen Port angeben, z. B. /dev/ttyUSB0.")
            return
        self._port, self._baud = port, baud
        self._start(f"Verbinde mit {port} und lese Fehlercodes …")
        backend = self._backend
        self._runner.run(lambda: backend.scan(port, baud), self._scan_done, self._job_failed)

    @Slot()
    def clearCodes(self) -> None:
        """Löscht die Codes am zuletzt gescannten Port; die Bestätigung holt die QML-Seite ein."""
        if not self.canClear:
            return
        port, baud = self._port, self._baud
        self._start("Sichere Codes und Freeze Frame, lösche Fehlercodes …")
        backend = self._backend
        self._runner.run(lambda: backend.clear(port, baud), self._clear_done, self._clear_failed)

    @Slot()
    def dismissError(self) -> None:
        self._error = ""
        self.stateChanged.emit()

    @Slot()
    def dismissNotice(self) -> None:
        self._notice = ""
        self.stateChanged.emit()

    # --- Rückmeldungen der Jobs (im GUI-Thread) ------------------------------

    def _start(self, text: str) -> None:
        self._busy = True
        self._busy_text = text
        self._error = ""
        self._notice = ""
        self.stateChanged.emit()

    def _finish(self) -> None:
        self._busy = False
        self._busy_text = ""

    def _show(self, result: ScanResult) -> None:
        self._result = result
        self._codes.set_codes(result.codes)
        self._selected = 0 if result.codes else -1
        self.selectionChanged.emit()

    def _scan_done(self, result: Any) -> None:
        self._finish()
        self._show(result)
        self.stateChanged.emit()
        self.scanFinished.emit()

    def _clear_done(self, result: Any) -> None:
        assert isinstance(result, ClearResult)
        self._finish()
        self._show(result.after)
        backup = str(Path(result.backup_path))
        notice = f"Fehlercodes gelöscht. Sicherung: {backup}"
        remaining = [c for c in result.after.codes if c.kind is DtcKind.PERMANENT]
        if remaining:
            notice += (
                f". {len(remaining)} permanente(r) Code(s) bleiben, bis das Steuergerät"
                " den Fehler in Fahrzyklen selbst als behoben erkennt."
            )
        elif result.after.codes:
            notice += f". Weiterhin {len(result.after.codes)} Code(s) vorhanden."
        self._notice = notice
        self.stateChanged.emit()
        self.clearSucceeded.emit(backup)

    def _clear_failed(self, error: Exception) -> None:
        if isinstance(error, ClearRefused):
            self._finish()
            self.stateChanged.emit()
            self.clearRefused.emit(str(error))
        else:
            self._job_failed(error)

    def _job_failed(self, error: Exception) -> None:
        self._finish()
        self._fail(user_message(error))

    def _fail(self, message: str) -> None:
        self._error = message
        self.stateChanged.emit()
        self.jobFailed.emit(message)
