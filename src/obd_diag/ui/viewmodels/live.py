"""View-Model des Reiters „Live-Daten“: Werte fortlaufend lesen, anzeigen, aufzeichnen.

Die Abfrage läuft als ein langer Job über denselben ``JobRunner`` wie die Diagnose
(ein Thread, also nie zwei Jobs am Adapter). Zwischenwerte kommen aus dem
Worker-Thread über Signale eines ``_LiveRelay``, das im GUI-Thread lebt; Qt stellt sie
dort zu (queued). Gestoppt wird über ein ``threading.Event``, das ``should_stop`` liest.

Jede Abfrage bekommt eine laufende Nummer; Rückmeldungen einer älteren Abfrage, die
erst nach dem Start einer neuen ankommen, werden verworfen.
"""

import math
import threading
from collections import deque
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    Property,
    QAbstractListModel,
    QByteArray,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QSettings,
    Qt,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import QDesktopServices

from obd_diag.protocol.pids import PIDS, PidSpec
from obd_diag.services.live import (
    DEFAULT_INTERVAL,
    DEFAULT_KEYS,
    LiveSample,
    LiveSetup,
    SelectionError,
    recording_dir,
)
from obd_diag.ui.backend import Backend, LiveResult
from obd_diag.ui.jobs import JobRunner
from obd_diag.ui.viewmodels.diagnosis import (
    SETTINGS_APP,
    SETTINGS_ORG,
    DiagnosisViewModel,
    user_message,
)

HISTORY = 120  # Werte je Kurve (bei 1 s also zwei Minuten)
INTERVALS = (0.5, 1.0, 2.0)  # Auswahl in der Oberfläche, Sekunden
NO_VALUE = "–"  # noqa: RUF001  (Anzeige für „nicht lesbar“)

RECORD_KEY = "live/aufzeichnen"
INTERVAL_KEY = "live/intervall"


def number_text(value: float | None) -> str:
    """Zahl mit Dezimalkomma; Nachkommastellen nach Größe, ganze Zahlen ohne."""
    if value is None or math.isnan(value):
        return NO_VALUE
    if value == int(value) and abs(value) < 1e9:
        return str(int(value))
    digits = 0 if abs(value) >= 100 else 1 if abs(value) >= 10 else 2
    return f"{value:.{digits}f}".replace(".", ",")


def elapsed_text(seconds: float) -> str:
    """Laufzeit als ``m:ss`` bzw. ``h:mm:ss``."""
    total = max(0, int(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def spec_entry(spec: PidSpec) -> dict[str, Any]:
    return {"key": spec.key, "name": spec.name, "unit": spec.unit}


class _Value:
    """Laufende Angaben zu einem Wert: aktuell, kleinster/größter gesehener, Verlauf."""

    def __init__(self, spec: PidSpec) -> None:
        self.spec = spec
        self.value: float | None = None
        self.low: float | None = None
        self.high: float | None = None
        self.history: deque[float] = deque(maxlen=HISTORY)  # NaN = nicht lesbar

    def add(self, value: float | None) -> None:
        self.value = value
        self.history.append(math.nan if value is None else value)
        if value is not None:
            self.low = value if self.low is None else min(self.low, value)
            self.high = value if self.high is None else max(self.high, value)

    def entry(self) -> dict[str, Any]:
        spec = self.spec
        return {
            "key": spec.key,
            "name": spec.name,
            "unit": spec.unit,
            "minimum": spec.minimum,
            "maximum": spec.maximum,
            "hasValue": self.value is not None,
            "value": self.value if self.value is not None else math.nan,
            "valueText": number_text(self.value),
            "minimumText": number_text(spec.minimum),
            "maximumText": number_text(spec.maximum),
            "lowText": number_text(self.low),
            "highText": number_text(self.high),
            "history": list(self.history),
        }


_ROLE_NAMES = (
    "key",
    "name",
    "unit",
    "minimum",
    "maximum",
    "hasValue",
    "value",
    "valueText",
    "minimumText",
    "maximumText",
    "lowText",
    "highText",
    "history",
)
_ROLES = {Qt.ItemDataRole.UserRole + 1 + i: name for i, name in enumerate(_ROLE_NAMES)}


class LiveValueModel(QAbstractListModel):
    """Die abgefragten Werte als Kacheln; jede Runde ändert nur die Daten, nicht die Zeilen."""

    countChanged = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._values: list[_Value] = []
        self._entries: list[dict[str, Any]] = []

    def set_pids(self, pids: Sequence[PidSpec]) -> None:
        self.beginResetModel()
        self._values = [_Value(spec) for spec in pids]
        self._entries = [v.entry() for v in self._values]
        self.endResetModel()
        self.countChanged.emit()

    def add_sample(self, values: dict[str, float | None]) -> None:
        for row, item in enumerate(self._values):
            item.add(values.get(item.spec.key))
            self._entries[row] = item.entry()
        if self._values:
            self.dataChanged.emit(self.index(0), self.index(len(self._values) - 1))

    def entry(self, key: str) -> dict[str, Any] | None:
        """Angaben zu ``key`` (für Tests und Python-Code), ``None``, wenn nicht abgefragt."""
        for item in self._entries:
            if item["key"] == key:
                return item
        return None

    @property
    def keys(self) -> list[str]:
        return [v.spec.key for v in self._values]

    def _get_count(self) -> int:
        return len(self._entries)

    count = Property(int, _get_count, notify=countChanged)

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex | None = None) -> int:
        if parent is not None and parent.isValid():
            return 0
        return len(self._entries)

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = 0) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self._entries):
            return None
        entry = self._entries[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return entry["name"]
        name = _ROLES.get(role)
        return entry[name] if name is not None else None

    def roleNames(self) -> dict[int, QByteArray]:
        return {role: QByteArray(name.encode()) for role, name in _ROLES.items()}


class _LiveRelay(QObject):
    """Lebt im GUI-Thread; Signale aus dem Worker kommen dort an (queued)."""

    setup = Signal(int, object)  # Nummer der Abfrage, LiveSetup
    started = Signal(int, object, object)  # Nummer, list[PidSpec], Path | None
    sample = Signal(int, object)  # Nummer, LiveSample


class LiveViewModel(QObject):
    """Zustand und Aktionen des Reiters „Live-Daten“.

    Startet nur, wenn die Diagnose nicht beschäftigt ist; solange Live-Daten laufen,
    ist die Diagnose gesperrt (``DiagnosisViewModel.blocked``).
    """

    stateChanged = Signal()
    availableChanged = Signal()
    selectionChanged = Signal()
    settingsChanged = Signal()
    sampleChanged = Signal()
    liveFinished = Signal(str)  # Pfad der Aufzeichnung oder leer
    jobFailed = Signal(str)

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
        self._settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
        self._values = LiveValueModel(self)
        self._relay = _LiveRelay(self)
        self._relay.setup.connect(self._on_setup, Qt.ConnectionType.QueuedConnection)
        self._relay.started.connect(self._on_started, Qt.ConnectionType.QueuedConnection)
        self._relay.sample.connect(self._on_sample, Qt.ConnectionType.QueuedConnection)
        diagnosis.stateChanged.connect(self.stateChanged)

        self._run_id = 0
        self._stop_event = threading.Event()
        self._running = False
        self._error = ""
        self._notice = ""
        self._connection = ""
        self._recording_path = ""
        self._samples = 0
        self._elapsed = 0.0
        self._voltage: float | None = None
        self._throttled = False

        # Vor der ersten Verbindung: alle bekannten Werte; danach die unterstützten
        self._available: list[PidSpec] = sorted(PIDS.values(), key=lambda s: s.pid)
        self._supported_known = False
        self._selected: list[str] = list(DEFAULT_KEYS)
        self._custom_selection = False  # False: die üblichen Werte, soweit unterstützt

        self._recording = self._settings.value(RECORD_KEY, False, type=bool) is True
        stored = self._settings.value(INTERVAL_KEY, DEFAULT_INTERVAL, type=float)
        self._interval = float(stored) if stored in INTERVALS else DEFAULT_INTERVAL

    # --- Eigenschaften ---------------------------------------------------------

    @Property(bool, notify=stateChanged)
    def running(self) -> bool:
        return self._running

    @Property(bool, notify=stateChanged)
    def stopping(self) -> bool:
        """Stopp angefordert, die laufende Runde wird noch beendet."""
        return self._running and self._stop_event.is_set()

    @Property(bool, notify=stateChanged)
    def canStart(self) -> bool:
        return not self._running and not self._diagnosis.property("busy")

    @Property(QObject, constant=True)
    def values(self) -> LiveValueModel:
        return self._values

    @property
    def value_model(self) -> LiveValueModel:
        """Wie ``values``, aber für Python typisiert."""
        return self._values

    @Property(list, notify=availableChanged)
    def available(self) -> list[dict[str, Any]]:
        return [spec_entry(s) for s in self._available]

    @Property(bool, notify=availableChanged)
    def supportedKnown(self) -> bool:
        """True, sobald das Fahrzeug seine unterstützten Werte gemeldet hat."""
        return self._supported_known

    @Property(list, notify=selectionChanged)
    def selectedKeys(self) -> list[str]:
        return list(self._selected)

    @Property(str, notify=stateChanged)
    def connectionText(self) -> str:
        """Adapter und Protokoll der laufenden bzw. letzten Abfrage."""
        return self._connection

    @Property(int, notify=sampleChanged)
    def sampleCount(self) -> int:
        return self._samples

    @Property(str, notify=sampleChanged)
    def elapsedText(self) -> str:
        return elapsed_text(self._elapsed)

    @Property(bool, notify=sampleChanged)
    def voltageKnown(self) -> bool:
        return self._voltage is not None

    @Property(str, notify=sampleChanged)
    def voltageText(self) -> str:
        if self._voltage is None:
            return "unbekannt"
        return f"{self._voltage:.1f} V".replace(".", ",")

    @Property(bool, notify=sampleChanged)
    def throttled(self) -> bool:
        return self._throttled

    @Property(str, notify=stateChanged)
    def recordingPath(self) -> str:
        return self._recording_path

    @Property(str, notify=stateChanged)
    def errorMessage(self) -> str:
        return self._error

    @Property(str, notify=stateChanged)
    def notice(self) -> str:
        return self._notice

    @Property(list, constant=True)
    def intervals(self) -> list[float]:
        return list(INTERVALS)

    @Property(int, constant=True)
    def historyLength(self) -> int:
        """Länge des Verlaufs je Wert; die Kurve teilt ihre Breite danach auf."""
        return HISTORY

    def _get_recording(self) -> bool:
        return self._recording

    def _set_recording(self, enabled: bool) -> None:
        # Während der Abfrage nicht umschaltbar: die Datei ist dann schon offen oder nicht
        if self._running or enabled == self._recording:
            return
        self._recording = enabled
        self._settings.setValue(RECORD_KEY, enabled)
        self._settings.sync()
        self.settingsChanged.emit()

    # Jede Runde als CSV unter ~/.local/share/obd-diag/recordings mitschreiben
    recording = Property(bool, _get_recording, _set_recording, notify=settingsChanged)

    def _get_interval(self) -> float:
        return self._interval

    def _set_interval(self, seconds: float) -> None:
        if self._running or seconds not in INTERVALS or seconds == self._interval:
            return
        self._interval = seconds
        self._settings.setValue(INTERVAL_KEY, seconds)
        self._settings.sync()
        self.settingsChanged.emit()

    interval = Property(float, _get_interval, _set_interval, notify=settingsChanged)

    # --- Aktionen ------------------------------------------------------------

    @Slot(str, bool)
    def setSelected(self, key: str, selected: bool) -> None:
        """Wert für die nächste Abfrage an- oder abwählen (nicht während der Abfrage)."""
        if self._running or (key in self._selected) == selected:
            return
        if selected:
            self._selected.append(key)
        else:
            self._selected.remove(key)
        self._custom_selection = True
        self.selectionChanged.emit()

    @Slot()
    def resetSelection(self) -> None:
        """Zurück zu den üblichen Werten."""
        if self._running:
            return
        self._selected = self._default_selection()
        self._custom_selection = False
        self.selectionChanged.emit()

    def _default_selection(self) -> list[str]:
        if not self._supported_known:
            return list(DEFAULT_KEYS)
        known = {s.key for s in self._available}
        return [k for k in DEFAULT_KEYS if k in known]

    def _keys(self) -> list[str] | None:
        """Schlüssel für ``select_pids`` in der Reihenfolge der Auswahlliste."""
        if not self._custom_selection:
            return None
        order = [s.key for s in self._available]
        return sorted(self._selected, key=lambda k: order.index(k) if k in order else len(order))

    @Slot(str, int)
    def start(self, port: str, baud: int) -> None:
        """Live-Daten am Port lesen, bis ``stop`` aufgerufen wird."""
        port = port.strip()
        if not self.canStart:
            return
        if not port:
            self._fail("Bitte einen Port angeben, z. B. /dev/ttyUSB0.")
            return
        keys = self._keys()
        if keys is not None and not keys:
            self._fail("Bitte mindestens einen Wert auswählen.")
            return

        self._run_id += 1
        run_id = self._run_id
        stop = threading.Event()
        self._stop_event = stop
        self._running = True
        self._error = ""
        self._notice = ""
        self._recording_path = ""
        self._samples = 0
        self._elapsed = 0.0
        self._voltage = None
        self._throttled = False
        self._values.set_pids([])
        self._diagnosis.set_blocked(True)
        self.stateChanged.emit()
        self.sampleChanged.emit()

        relay = self._relay
        backend = self._backend
        interval, record = self._interval, self._recording
        # Vor der ersten Verbindung zeigt die Liste alle Werte; was das Fahrzeug davon
        # nicht kann, wird dann weggelassen statt die Abfrage abzulehnen.
        skip_unsupported = not self._supported_known

        def job() -> LiveResult:
            return backend.live(
                port,
                baud,
                keys,
                interval,
                record,
                on_setup=lambda setup: relay.setup.emit(run_id, setup),
                on_start=lambda pids, path: relay.started.emit(run_id, pids, path),
                on_sample=lambda sample: relay.sample.emit(run_id, sample),
                should_stop=stop.is_set,
                skip_unsupported=skip_unsupported,
            )

        self._runner.run(
            job,
            lambda result: self._done(run_id, result),
            lambda error: self._failed(run_id, error),
        )

    @Slot()
    def stop(self) -> None:
        """Abfrage beenden; der Job hört nach der laufenden Runde auf."""
        if self._running and not self._stop_event.is_set():
            self._stop_event.set()
            self.stateChanged.emit()

    @Slot()
    def dismissError(self) -> None:
        self._error = ""
        self.stateChanged.emit()

    @Slot()
    def dismissNotice(self) -> None:
        self._notice = ""
        self.stateChanged.emit()

    @Slot()
    def openRecordingFolder(self) -> None:
        folder = recording_dir()
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    # --- Rückmeldungen (im GUI-Thread) ---------------------------------------

    def _on_setup(self, run_id: int, setup: LiveSetup) -> None:
        if run_id != self._run_id:
            return
        self._connection = f"{setup.adapter} · {setup.protocol}"
        self._available = list(setup.available)
        self._supported_known = True
        supported = {s.key for s in self._available}
        if self._custom_selection:
            self._selected = [k for k in self._selected if k in supported]
        else:
            self._selected = self._default_selection()
        self.availableChanged.emit()
        self.selectionChanged.emit()
        self.stateChanged.emit()

    def _on_started(self, run_id: int, pids: list[PidSpec], path: Path | None) -> None:
        if run_id != self._run_id:
            return
        self._values.set_pids(pids)
        self._recording_path = str(path) if path is not None else ""
        self.stateChanged.emit()

    def _on_sample(self, run_id: int, sample: LiveSample) -> None:
        if run_id != self._run_id:
            return
        self._values.add_sample(sample.values)
        self._samples += 1
        self._elapsed = sample.elapsed
        self._voltage = sample.voltage
        self._throttled = sample.throttled
        self.sampleChanged.emit()

    def _finish(self) -> None:
        self._running = False
        self._diagnosis.set_blocked(False)

    def _done(self, run_id: int, result: Any) -> None:
        assert isinstance(result, LiveResult)
        if run_id != self._run_id:
            return
        self._finish()
        notice = f"Live-Daten beendet nach {result.samples} Runde(n)."
        if result.recording is not None:
            notice += f" Aufzeichnung gespeichert: {result.recording}"
            self._recording_path = str(result.recording)
        self._notice = notice
        self.stateChanged.emit()
        self.liveFinished.emit(self._recording_path)

    def _failed(self, run_id: int, error: Exception) -> None:
        if run_id != self._run_id:
            return
        self._finish()
        if isinstance(error, SelectionError):
            message = f"Auswahl nicht möglich: {error}"
        else:
            message = user_message(error)
        if self._recording_path:
            message += f" Bisherige Aufzeichnung: {self._recording_path}"
        self._fail(message)

    def _fail(self, message: str) -> None:
        self._error = message
        self.stateChanged.emit()
        self.jobFailed.emit(message)
