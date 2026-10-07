"""Live-Daten (Mode 01) fortlaufend lesen und als CSV aufzeichnen; nur lesend.

Ablauf: ``prepare_live`` initialisiert den Adapter und ermittelt die unterstützten
Werte, ``run_live`` fragt die gewählten Werte reihum ab, bis ``should_stop`` True
liefert oder ``max_samples`` erreicht ist. Die Bordspannung wird regelmäßig gelesen;
unter ``LOW_VOLTAGE`` wird die Abfrage gedrosselt.

Gesendet werden nur ``initialize``/``protocol`` (AT-Befehle, ``0100``), ``ATRV`` und
die ``01xx``-Anfragen aus ``protocol.pids``.
"""

import csv
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Self

from obd_diag.protocol import pids as pid_table
from obd_diag.protocol.elm327 import Elm327, ElmError
from obd_diag.protocol.pids import PidSpec
from obd_diag.services.diagnostics import LOW_VOLTAGE
from obd_diag.services.storage import data_dir

DEFAULT_INTERVAL = 1.0  # Sekunden zwischen zwei Abfragerunden
LOW_VOLTAGE_INTERVAL = 5.0  # Sekunden zwischen Runden bei niedriger Bordspannung
VOLTAGE_EVERY = 10  # Bordspannung jede n-te Runde lesen (ATRV)
# Die üblichen Werte, wenn der Nutzer keine Auswahl trifft (sofern unterstützt).
DEFAULT_KEYS = ("rpm", "speed", "coolant_temp", "engine_load", "intake_temp", "control_voltage")

# Längster Schlaf am Stück beim Warten auf die nächste Runde, damit ``should_stop``
# (Strg+C in der GUI, Ende von ``--duration``) schnell greift.
_SLEEP_STEP = 0.1


@dataclass(frozen=True)
class LiveSample:
    elapsed: float  # Sekunden seit Start der Aufzeichnung
    values: dict[str, float | None]  # PidSpec.key -> Wert; None = diesmal nicht lesbar
    voltage: float | None  # zuletzt gelesene Bordspannung (ATRV), None = unbekannt
    throttled: bool  # True, wenn wegen niedriger Spannung gedrosselt wird


@dataclass(frozen=True)
class LiveSetup:
    adapter: str  # Kennung aus ATZ
    protocol: str  # Name laut ATDP
    available: list[PidSpec]  # unterstützt und in PIDS bekannt, nach PID sortiert


def prepare_live(elm: Elm327) -> LiveSetup:
    """Adapter initialisieren, Protokoll aushandeln, unterstützte Live-Werte ermitteln.

    Befehle: ``initialize`` (ATZ ... ATSP0), ``protocol`` (0100, ATDPN, ATDP), dann
    ``pids.read_supported_pids`` (0100, ggf. 0120 ...). Fehler gehen an den Aufrufer.
    """
    adapter = elm.initialize()
    protocol = elm.protocol().name
    supported = pid_table.read_supported_pids(elm)
    available = [pid_table.PIDS[pid] for pid in sorted(supported) if pid in pid_table.PIDS]
    return LiveSetup(adapter, protocol, available)


def select_pids(setup: LiveSetup, keys: Sequence[str] | None) -> list[PidSpec]:
    """Die gewünschten Werte (``None``: ``DEFAULT_KEYS``), soweit unterstützt.

    Unbekannte Schlüssel ergeben ``ValueError``; bekannte, aber nicht unterstützte
    ebenfalls (Meldung nennt sie), damit der Nutzer nicht stumm leere Spalten bekommt.
    Bei ``None`` werden nicht unterstützte Standardwerte stillschweigend weggelassen.
    Reihenfolge: wie in ``keys`` bzw. ``DEFAULT_KEYS``; doppelte Schlüssel einmal.
    Eine leere Auswahl (``keys`` leer) ist ebenfalls ein ``ValueError``; bei ``None``
    kann das Ergebnis leer sein, wenn das Fahrzeug keinen Standardwert unterstützt.
    """
    by_key = {spec.key: spec for spec in setup.available}
    if keys is None:
        return [by_key[key] for key in DEFAULT_KEYS if key in by_key]
    wanted = list(dict.fromkeys(keys))
    if not wanted:
        raise ValueError("keine Werte gewählt")
    unknown: list[str] = []
    unsupported: list[str] = []
    selected: list[PidSpec] = []
    for key in wanted:
        if key in by_key:
            selected.append(by_key[key])
            continue
        try:
            pid_table.pid_by_key(key)
        except KeyError:
            unknown.append(key)
        else:
            unsupported.append(key)
    problems = []
    if unknown:
        problems.append(f"unbekannte Werte: {', '.join(unknown)}")
    if unsupported:
        problems.append(f"vom Fahrzeug nicht unterstützt: {', '.join(unsupported)}")
    if problems:
        offered = ", ".join(by_key) or "keine"
        raise ValueError(f"{'; '.join(problems)} (verfügbar: {offered})")
    return selected


def _csv_number(value: float | None) -> str:
    """Bis zu drei Nachkommastellen, Dezimalkomma, ohne Tausenderpunkt; None: leer."""
    if value is None:
        return ""
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    if text == "-0":
        text = "0"
    return text.replace(".", ",")


class LiveRecorder:
    """Schreibt Live-Werte als CSV (UTF-8 mit BOM, Semikolon, Dezimalkomma wie export).

    Kopfzeile: ``Zeit (s)``, dann je Wert ``Name (Einheit)``, zuletzt ``Bordspannung (V)``.
    Nicht lesbare Werte bleiben leere Zellen. Jede Zeile wird sofort geschrieben
    (flush), damit ein Abbruch nichts verliert. Die Datei wird beim Erzeugen exklusiv
    angelegt; existiert sie schon, gibt es ``FileExistsError`` (nie überschreiben).
    """

    def __init__(self, path: Path, pids: Sequence[PidSpec]) -> None:
        self.path = path
        self.pids = list(pids)
        self._file = path.open("x", encoding="utf-8-sig", newline="")
        self._writer = csv.writer(self._file, delimiter=";", lineterminator="\r\n")
        header = ["Zeit (s)", *(f"{p.name} ({p.unit})" for p in self.pids), "Bordspannung (V)"]
        self._writer.writerow(header)
        self._file.flush()

    def add(self, sample: LiveSample) -> None:
        """Hängt eine Runde als Zeile an (sofort auf die Platte)."""
        row = [
            _csv_number(sample.elapsed),
            *(_csv_number(sample.values.get(p.key)) for p in self.pids),
            _csv_number(sample.voltage),
        ]
        self._writer.writerow(row)
        self._file.flush()

    def close(self) -> None:
        self._file.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


def recording_dir() -> Path:
    """``$XDG_DATA_HOME/obd-diag/recordings``."""
    return data_dir() / "recordings"


def new_recording_path(directory: Path) -> Path:
    """Freier Name ``live-YYYYmmdd-HHMMSS[-n].csv``; legt den Ordner an."""
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"live-{datetime.now():%Y%m%d-%H%M%S}"
    for n in range(1, 1000):
        path = directory / (f"{stem}.csv" if n == 1 else f"{stem}-{n}.csv")
        if not path.exists():
            return path
    raise FileExistsError(f"kein freier Dateiname für {stem} in {directory}")


def _read_voltage(elm: Elm327) -> float | None:
    """Bordspannung (``ATRV``); ``None``, wenn der Adapter sie nicht liefert."""
    try:
        return elm.voltage()
    except (ElmError, ValueError):
        return None


def _read_value(elm: Elm327, spec: PidSpec) -> float | None:
    """Ein Wert; abgelehnt oder unlesbar ergibt ``None``, ``TransportError`` geht durch."""
    try:
        return pid_table.read_value(elm, spec)
    except (ElmError, ValueError):
        return None


def run_live(
    elm: Elm327,
    pids: Sequence[PidSpec],
    *,
    on_sample: Callable[[LiveSample], None],
    should_stop: Callable[[], bool],
    interval: float = DEFAULT_INTERVAL,
    max_samples: int | None = None,
    recorder: LiveRecorder | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Fragt ``pids`` reihum ab und meldet jede Runde über ``on_sample``.

    Erwartet einen mit ``prepare_live`` vorbereiteten Adapter. Ein einzelner nicht
    lesbarer Wert (``ElmError`` oder ``ValueError``) ergibt ``None`` in der Runde
    (kein Abbruch); ``TransportError`` bricht ab. ``should_stop`` wird vor jeder Runde
    und während des Wartens geprüft (in Schritten von höchstens 0,1 s).

    Die Bordspannung wird in Runde 0 und jeder ``VOLTAGE_EVERY``-ten Runde gelesen;
    liegt sie unter ``LOW_VOLTAGE``, beginnen Runden nur alle ``LOW_VOLTAGE_INTERVAL``
    Sekunden (``throttled``). Runden beginnen im Abstand ``interval`` ab Rundenbeginn;
    dauert eine Runde länger, folgt die nächste sofort. Liefert die Anzahl der Runden.
    """
    start = clock()
    count = 0
    voltage: float | None = None
    throttled = False
    while (max_samples is None or count < max_samples) and not should_stop():
        round_start = clock()
        if count % VOLTAGE_EVERY == 0:
            voltage = _read_voltage(elm)
            throttled = voltage is not None and voltage < LOW_VOLTAGE
        values = {spec.key: _read_value(elm, spec) for spec in pids}
        sample = LiveSample(round_start - start, values, voltage, throttled)
        if recorder is not None:
            recorder.add(sample)
        on_sample(sample)
        count += 1
        if max_samples is not None and count >= max_samples:
            break
        deadline = round_start + (max(interval, LOW_VOLTAGE_INTERVAL) if throttled else interval)
        while (remaining := deadline - clock()) > 0 and not should_stop():
            sleep(min(remaining, _SLEEP_STEP))
    return count
