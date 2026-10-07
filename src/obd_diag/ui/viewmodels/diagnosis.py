"""View-Model der Hauptansicht: Diagnose, Löschen, Sitzung speichern, öffnen, exportieren."""

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import Property, QObject, QSettings, QStandardPaths, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

from obd_diag.protocol.elm327 import ElmError
from obd_diag.services.clear import ClearRefused, ClearResult, clearable_codes
from obd_diag.services.diagnostics import DiagnosticCode, DtcKind, ScanResult
from obd_diag.services.session import Session, default_session_dir
from obd_diag.services.storage import trace_dir
from obd_diag.transport import TransportError
from obd_diag.ui.backend import Backend
from obd_diag.ui.jobs import JobRunner
from obd_diag.ui.viewmodels.codes import CodeListModel
from obd_diag.ui.viewmodels.session_parts import (
    MonitorListModel,
    freeze_frame_entry,
    readiness_entry,
    vehicle_entry,
)

DEFAULT_PORT = "/dev/ttyUSB0"
DEFAULT_BAUD = 38400

LOW_VOLTAGE_WARNING = (
    "Batteriespannung niedrig: Ergebnisse können unzuverlässig sein. "
    "Vor dem Löschen Batterie laden oder Ladegerät anschließen."
)

# Einstellungen je Nutzer (unter Linux ~/.config/obd-diag/obd-diag.conf)
SETTINGS_ORG = "obd-diag"
SETTINGS_APP = "obd-diag"
ONLINE_VIN_KEY = "fin/onlineNachschlagen"
TRACE_KEY = "adapter/mitschnitt"

_EXPORT_KINDS = {"pdf": "PDF-Bericht", "csv": "CSV-Datei"}


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


def _file_error(error: Exception) -> str:
    if isinstance(error, OSError):
        reason = error.strerror or str(error)
        return f"{reason} ({error.filename})" if error.filename else reason
    return str(error)


def local_path(target: str) -> Path:
    """Pfad aus dem Dateidialog (``file:///…``) oder aus einer Eingabe."""
    if target.startswith("file:"):
        return Path(QUrl(target).toLocalFile())
    return Path(target).expanduser()


def _folder_url(path: Path) -> str:
    return QUrl.fromLocalFile(str(path)).toString()


class DiagnosisViewModel(QObject):
    """Zustand und Aktionen der Hauptansicht.

    Serielle Arbeit und Export laufen über ``runner`` abseits des GUI-Threads; solange
    ein Job läuft, ist ``busy`` gesetzt und weitere Aktionen werden ignoriert.

    Eine aus einer Datei geöffnete Sitzung ist nur zum Ansehen (``viewOnly``): Löschen
    braucht ein verbundenes Fahrzeug.
    """

    portsChanged = Signal()
    stateChanged = Signal()
    selectionChanged = Signal()
    settingsChanged = Signal()
    clearRefused = Signal(str)  # Vorbedingung nicht erfüllt oder vom Steuergerät abgelehnt
    clearSucceeded = Signal(str)  # Pfad der Sicherung
    scanFinished = Signal()
    sessionSaved = Signal(str)
    sessionOpened = Signal(str)
    exportFinished = Signal(str)
    jobFailed = Signal(str)

    def __init__(self, backend: Backend, runner: JobRunner, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._backend = backend
        self._runner = runner
        self._codes = CodeListModel(self)
        self._monitors = MonitorListModel(self)
        self._settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
        self._ports: list[dict[str, str]] = []
        self._busy = False
        self._busy_text = ""
        self._session: Session | None = None
        self._view_only = False
        self._saved_path = ""  # gespeichert unter bzw. geöffnet aus
        self._error = ""
        self._notice = ""
        self._selected = -1
        self._port = DEFAULT_PORT
        self._baud = DEFAULT_BAUD
        self._online_vin = self._settings.value(ONLINE_VIN_KEY, False, type=bool) is True
        self._trace = self._settings.value(TRACE_KEY, False, type=bool) is True
        backend.set_tracing(self._trace)
        try:
            self._catalog_missing = not backend.catalog_available()
        except Exception:
            self._catalog_missing = True
        self.refreshPorts()

    # --- Eigenschaften: Verbindung und Fehlercodes ---------------------------

    @property
    def session(self) -> Session | None:
        return self._session

    @property
    def _result(self) -> ScanResult | None:
        return self._session.scan if self._session is not None else None

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
        return self._session is not None

    @Property(bool, notify=stateChanged)
    def viewOnly(self) -> bool:
        return self._view_only

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
        return self._port if self._session is not None and not self._view_only else ""

    @Property(bool, notify=stateChanged)
    def hasCodes(self) -> bool:
        return self._codes.rowCount() > 0

    @Property(int, notify=stateChanged)
    def codeCount(self) -> int:
        return self._codes.rowCount()

    @Property(bool, notify=stateChanged)
    def canClear(self) -> bool:
        # Nur gespeicherte und ausstehende Codes lassen sich löschen (Mode 04), und nur
        # am verbundenen Fahrzeug, nicht in einer geöffneten Sitzung
        return not self._busy and not self._view_only and bool(self._clearable())

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
        """Die zu löschenden Codes für den Bestätigungsdialog, jeder nur einmal."""
        return list(dict.fromkeys(c.code for c in self._clearable()))

    @Property(int, notify=stateChanged)
    def permanentCount(self) -> int:
        """Permanente Codes; sie bleiben nach dem Löschen stehen."""
        return sum(c.kind is DtcKind.PERMANENT for c in self._codes.codes)

    def _clearable(self) -> list[DiagnosticCode]:
        return clearable_codes(self._result) if self._result is not None else []

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

    # --- Eigenschaften: übrige Teile der Sitzung -----------------------------

    @Property(dict, notify=stateChanged)
    def vehicle(self) -> dict[str, Any]:
        return vehicle_entry(self._session.vehicle if self._session is not None else None)

    @Property(str, notify=stateChanged)
    def vehicleText(self) -> str:
        """Kurzform für die Kopfzeile, z. B. „Volkswagen · WVWZZZ1KZ6W123456“."""
        vin = self._session.vehicle if self._session is not None else None
        if vin is None:
            return ""
        return f"{vin.manufacturer} · {vin.vin}" if vin.manufacturer else vin.vin

    @Property(dict, notify=stateChanged)
    def readiness(self) -> dict[str, Any]:
        return readiness_entry(self._session.readiness if self._session is not None else None)

    @Property(QObject, constant=True)
    def monitors(self) -> MonitorListModel:
        return self._monitors

    @Property(dict, notify=stateChanged)
    def freezeFrame(self) -> dict[str, Any]:
        if self._session is None:
            return freeze_frame_entry(None)
        return freeze_frame_entry(self._session.freeze_frame, self._session.scan.codes)

    @Property(str, notify=stateChanged)
    def createdText(self) -> str:
        """Zeitpunkt der Diagnose, z. B. „07.10.2026, 14:32“."""
        if self._session is None:
            return ""
        return f"{self._session.created:%d.%m.%Y, %H:%M}"

    @Property(str, notify=stateChanged)
    def sessionPath(self) -> str:
        """Datei der Sitzung: gespeichert unter oder geöffnet aus; leer, wenn ungespeichert."""
        return self._saved_path

    @Property(bool, notify=stateChanged)
    def canSave(self) -> bool:
        return (
            not self._busy
            and self._session is not None
            and not self._view_only
            and not self._saved_path
        )

    @Property(bool, notify=stateChanged)
    def canExport(self) -> bool:
        return not self._busy and self._session is not None

    @Property(str, notify=stateChanged)
    def reportBaseName(self) -> str:
        """Vorschlag für den Dateinamen ohne Endung: ``obd-bericht-YYYYmmdd-HHMM``."""
        created = self._session.created if self._session is not None else datetime.now()
        return f"obd-bericht-{created:%Y%m%d-%H%M}"

    @Property(str, constant=True)
    def reportFolder(self) -> str:
        """Startordner für den Export (Dokumente, sonst Persönlicher Ordner) als URL."""
        docs = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
        folder = Path(docs) if docs and Path(docs).is_dir() else Path.home()
        return _folder_url(folder)

    @Property(str, constant=True)
    def sessionFolder(self) -> str:
        """Startordner zum Öffnen: der Ablageort gespeicherter Sitzungen, falls vorhanden."""
        folder = default_session_dir()
        return _folder_url(folder if folder.is_dir() else Path.home())

    def _get_online_vin(self) -> bool:
        return self._online_vin

    def _set_online_vin(self, enabled: bool) -> None:
        if enabled != self._online_vin:
            self._online_vin = enabled
            self._settings.setValue(ONLINE_VIN_KEY, enabled)
            self._settings.sync()
            self.settingsChanged.emit()

    # Opt-in: die FIN geht nur dann an NHTSA vPIC, wenn hier eingeschaltet
    onlineVinLookup = Property(bool, _get_online_vin, _set_online_vin, notify=settingsChanged)

    def _get_trace(self) -> bool:
        return self._trace

    def _set_trace(self, enabled: bool) -> None:
        if enabled != self._trace:
            self._trace = enabled
            self._backend.set_tracing(enabled)
            self._settings.setValue(TRACE_KEY, enabled)
            self._settings.sync()
            self.settingsChanged.emit()

    # Mitschnitt der Adapter-Kommunikation je Job in eine eigene Datei
    traceAdapter = Property(bool, _get_trace, _set_trace, notify=settingsChanged)

    @Slot()
    def openTraceFolder(self) -> None:
        folder = trace_dir()
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

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
        """Vollständige Diagnose: Fehlercodes, Readiness, Freeze Frame, FIN."""
        port = port.strip()
        if self._busy:
            return
        if not port:
            self._fail("Bitte einen Port angeben, z. B. /dev/ttyUSB0.")
            return
        self._port, self._baud = port, baud
        self._start(f"Verbinde mit {port} und lese Diagnose …")
        self._run_diagnosis(self._scan_done, self._job_failed)

    def _run_diagnosis(
        self, on_success: Callable[[Any], None], on_error: Callable[[Exception], None]
    ) -> None:
        port, baud, online = self._port, self._baud, self._online_vin
        backend = self._backend
        self._runner.run(lambda: backend.diagnose(port, baud, online), on_success, on_error)

    @Slot()
    def clearCodes(self) -> None:
        """Löscht die Codes am zuletzt gescannten Port; die Bestätigung holt die QML-Seite ein.

        Danach wird die Diagnose neu gelesen: Mode 04 setzt auch Readiness und Freeze
        Frame zurück, die alten Angaben wären also überholt.
        """
        if not self.canClear:
            return
        port, baud = self._port, self._baud
        self._start("Sichere Codes und Freeze Frame, lösche Fehlercodes …")
        backend = self._backend
        self._runner.run(lambda: backend.clear(port, baud), self._clear_done, self._clear_failed)

    @Slot()
    def saveSession(self) -> None:
        """Speichert die aktuelle Diagnose als JSON unter ~/.local/share/obd-diag/sessions."""
        if not self.canSave or self._session is None:
            return
        try:
            path = self._backend.save_session(self._session)
        except (OSError, ValueError) as e:
            self._fail(f"Sitzung ließ sich nicht speichern: {_file_error(e)}")
            return
        self._saved_path = str(path)
        self._error = ""
        self._notice = f"Sitzung gespeichert: {path}"
        self.stateChanged.emit()
        self.sessionSaved.emit(str(path))

    @Slot(str)
    def openSession(self, target: str) -> None:
        """Öffnet eine gespeicherte Sitzung zum Ansehen (ohne Fahrzeug)."""
        if self._busy or not target.strip():
            return
        path = local_path(target.strip())
        try:
            session = self._backend.load_session(path)
        except (OSError, ValueError) as e:
            self._fail(f"Sitzung ließ sich nicht öffnen: {_file_error(e)}")
            return
        self._show(session, view_only=True, path=str(path))
        self._error = ""
        self._notice = f"Gespeicherte Sitzung vom {self.createdText} geöffnet (nur ansehen): {path}"
        self.stateChanged.emit()
        self.sessionOpened.emit(str(path))

    @Slot(str)
    def exportPdf(self, target: str) -> None:
        self._export("pdf", target)

    @Slot(str)
    def exportCsv(self, target: str) -> None:
        self._export("csv", target)

    def _export(self, kind: str, target: str) -> None:
        session = self._session
        if self._busy or session is None or not target.strip():
            return
        path = local_path(target.strip())
        if path.suffix.lower() != f".{kind}":
            path = path.with_name(f"{path.name}.{kind}")
        label = _EXPORT_KINDS[kind]
        write = self._backend.export_pdf if kind == "pdf" else self._backend.export_csv

        def job() -> Path:
            write(session, path)
            return path

        self._start(f"Schreibe {label} …")

        def done(result: Any) -> None:
            self._finish()
            self._notice = f"{label} gespeichert: {result}"
            self.stateChanged.emit()
            self.exportFinished.emit(str(result))

        def failed(error: Exception) -> None:
            self._finish()
            self._fail(f"{label} ließ sich nicht schreiben: {_file_error(error)}")

        self._runner.run(job, done, failed)

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

    def _show(self, session: Session, *, view_only: bool = False, path: str = "") -> None:
        self._session = session
        self._view_only = view_only
        self._saved_path = path
        codes = session.scan.codes
        self._codes.set_codes(codes)
        self._monitors.set_monitors(
            session.readiness.monitors if session.readiness is not None else ()
        )
        self._selected = 0 if codes else -1
        self.selectionChanged.emit()

    def _scan_done(self, result: Any) -> None:
        assert isinstance(result, Session)
        self._finish()
        self._show(result)
        self.stateChanged.emit()
        self.scanFinished.emit()

    def _clear_done(self, result: Any) -> None:
        assert isinstance(result, ClearResult)
        # Weiter beschäftigt: gleich im Anschluss die Diagnose neu lesen
        self._busy_text = "Fehlercodes gelöscht, lese Diagnose neu …"
        self.stateChanged.emit()
        self._run_diagnosis(
            lambda session: self._rediagnosis_done(result, session),
            lambda error: self._rediagnosis_failed(result, error),
        )

    def _rediagnosis_done(self, clear: ClearResult, session: Any) -> None:
        assert isinstance(session, Session)
        self._finish()
        self._show(session)
        self._notice = self._clear_notice(clear, session.scan)
        self.stateChanged.emit()
        self.clearSucceeded.emit(str(clear.backup_path))

    def _rediagnosis_failed(self, clear: ClearResult, error: Exception) -> None:
        # Gelöscht ist gelöscht: den Kontroll-Scan zeigen. Readiness und Freeze Frame
        # hat Mode 04 zurückgesetzt, die alten Werte gelten nicht mehr; die FIN bleibt.
        self._finish()
        vehicle = self._session.vehicle if self._session is not None else None
        self._show(Session(created=datetime.now().astimezone(), scan=clear.after, vehicle=vehicle))
        self._notice = self._clear_notice(clear, clear.after)
        self._error = (
            "Readiness und Freeze Frame ließen sich nach dem Löschen nicht neu lesen ("
            f"{user_message(error)}). „Erneut scannen“ versuchen."
        )
        self.stateChanged.emit()
        self.clearSucceeded.emit(str(clear.backup_path))

    @staticmethod
    def _clear_notice(clear: ClearResult, after: ScanResult) -> str:
        notice = f"Fehlercodes gelöscht. Sicherung: {Path(clear.backup_path)}"
        remaining = [c for c in after.codes if c.kind is DtcKind.PERMANENT]
        if remaining:
            notice += (
                f". {len(remaining)} permanente(r) Code(s) bleiben, bis das Steuergerät"
                " den Fehler in Fahrzyklen selbst als behoben erkennt."
            )
        elif after.codes:
            notice += f". Weiterhin {len(after.codes)} Code(s) vorhanden."
        return notice

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
