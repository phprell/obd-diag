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
from pathlib import Path
from types import TracebackType
from typing import Self

from obd_diag.protocol import pids as pid_table
from obd_diag.protocol.elm327 import Elm327, ElmError, UnknownCommandError
from obd_diag.protocol.pids import PidSpec
from obd_diag.services.diagnostics import LOW_VOLTAGE, check_low_voltage
from obd_diag.services.storage import data_dir
from obd_diag.transport.trace import new_free_path

DEFAULT_INTERVAL = 1.0  # Sekunden zwischen zwei Abfragerunden
LOW_VOLTAGE_INTERVAL = 5.0  # Sekunden zwischen Runden bei niedriger Bordspannung
VOLTAGE_EVERY = 10  # Bordspannung jede n-te Runde lesen (ATRV)
# So viele Runden nacheinander, in denen jeder Wert mit einem Adapterfehler (CAN ERROR,
# UNABLE TO CONNECT ...) scheitert, beenden die Abfrage: das Fahrzeug ist weg.
MAX_FAILED_ROUNDS = 3
# Die üblichen Werte, wenn der Nutzer keine Auswahl trifft (sofern unterstützt).
DEFAULT_KEYS = ("rpm", "speed", "coolant_temp", "engine_load", "intake_temp", "control_voltage")

# Längster Schlaf am Stück beim Warten auf die nächste Runde, damit ``should_stop``
# (Strg+C in der GUI, Ende von ``--duration``) schnell greift.
_SLEEP_STEP = 0.1


class SelectionError(ValueError):
    """Die gewünschten Werte lassen sich nicht abfragen (unbekannt, nicht unterstützt
    oder leere Auswahl). Andere ``ValueError`` stammen aus kaputten Antworten."""


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
    available = [spec for spec in pid_table.PIDS.values() if spec.pid in supported]
    return LiveSetup(adapter, protocol, available)


def select_pids(
    setup: LiveSetup, keys: Sequence[str] | None, *, skip_unsupported: bool = False
) -> list[PidSpec]:
    """Die gewünschten Werte (``None``: ``DEFAULT_KEYS``), soweit unterstützt.

    Unbekannte Schlüssel ergeben ``SelectionError``; bekannte, aber nicht unterstützte
    ebenfalls (Meldung nennt sie), damit der Nutzer nicht stumm leere Spalten bekommt.
    Bei ``None`` oder ``skip_unsupported`` (Auswahl getroffen, bevor bekannt war, was
    das Fahrzeug kann) werden nicht unterstützte Werte stillschweigend weggelassen.
    Reihenfolge: wie in ``keys`` bzw. ``DEFAULT_KEYS``; doppelte Schlüssel einmal.
    Bleibt kein Wert übrig, ist das ebenfalls ein ``SelectionError``: eine Abfrage
    ohne Werte liest nichts.
    """
    by_key = {spec.key: spec for spec in setup.available}
    offered = ", ".join(by_key) or "keine"
    if keys is None:
        defaults = [by_key[key] for key in DEFAULT_KEYS if key in by_key]
        if not defaults:
            raise SelectionError(
                f"Das Fahrzeug unterstützt keinen der Standardwerte (verfügbar: {offered})"
            )
        return defaults
    wanted = list(dict.fromkeys(keys))
    if not wanted:
        raise SelectionError("keine Werte gewählt")
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
    if unsupported and not skip_unsupported:
        problems.append(f"vom Fahrzeug nicht unterstützt: {', '.join(unsupported)}")
    if problems:
        raise SelectionError(f"{'; '.join(problems)} (verfügbar: {offered})")
    if not selected:
        raise SelectionError(
            f"vom Fahrzeug nicht unterstützt: {', '.join(unsupported)} (verfügbar: {offered})"
        )
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
    return new_free_path(directory, "live", ".csv")


def _read_voltage(elm: Elm327) -> float | None:
    """Bordspannung (``ATRV``, niedrige Werte per ``check_low_voltage`` geprüft);
    ``None``, wenn der Adapter sie nicht liefert."""
    try:
        voltage = elm.voltage()
    except (ElmError, ValueError):
        return None
    return check_low_voltage(elm, voltage)


def _read_values(
    elm: Elm327, specs: list[PidSpec]
) -> tuple[dict[str, float | None], ElmError | None]:
    """Die Werte einer PID (eine Anfrage) und ggf. der Adapterfehler, an dem sie scheiterten.

    Abgelehnt, ``?`` oder unlesbar ergibt ``None`` je Wert ohne Fehler; Adapterfehler
    wie ``CAN ERROR`` ``None`` je Wert und den Fehler. ``TransportError`` geht durch.
    """
    nothing: dict[str, float | None] = {spec.key: None for spec in specs}
    try:
        return pid_table.read_values(elm, specs), None
    except (UnknownCommandError, ValueError):
        return nothing, None
    except ElmError as e:
        return nothing, e


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

    Erwartet einen mit ``prepare_live`` vorbereiteten Adapter. Jede PID wird je Runde
    einmal abgefragt, auch wenn mehrere Werte aus ihr stammen. Ein einzelner nicht
    lesbarer Wert (``ElmError`` oder ``ValueError``) ergibt ``None`` in der Runde
    (kein Abbruch); ``TransportError`` bricht ab. Scheitert in ``MAX_FAILED_ROUNDS``
    Runden nacheinander jede Abfrage an einem Adapterfehler (Bus weg, Zündung aus), endet
    die Abfrage mit ``ElmError``. ``should_stop`` wird vor jeder Runde
    und während des Wartens geprüft (in Schritten von höchstens 0,1 s).

    Die Bordspannung wird in Runde 0 und jeder ``VOLTAGE_EVERY``-ten Runde gelesen;
    liegt sie unter ``LOW_VOLTAGE``, beginnen Runden nur alle ``LOW_VOLTAGE_INTERVAL``
    Sekunden (``throttled``). Ist sie einmal nicht lesbar, bleibt die Drosselung, wie
    sie war (eine schwache Batterie wird davon nicht besser). Runden beginnen im Abstand
    ``interval`` ab Rundenbeginn; dauert eine Runde länger, folgt die nächste sofort.
    Liefert die Anzahl der Runden.
    """
    groups: dict[int, list[PidSpec]] = {}
    for spec in pids:
        groups.setdefault(spec.pid, []).append(spec)
    start = clock()
    count = 0
    voltage: float | None = None
    throttled = False
    failed_rounds = 0
    while (max_samples is None or count < max_samples) and not should_stop():
        round_start = clock()
        if count % VOLTAGE_EVERY == 0:
            voltage = _read_voltage(elm)
            if voltage is not None:
                throttled = voltage < LOW_VOLTAGE
        read: dict[str, float | None] = {}
        errors: list[ElmError] = []
        for group in groups.values():
            group_values, error = _read_values(elm, group)
            read.update(group_values)
            if error is not None:
                errors.append(error)
        values = {spec.key: read[spec.key] for spec in pids}
        failed_rounds = failed_rounds + 1 if groups and len(errors) == len(groups) else 0
        if failed_rounds >= MAX_FAILED_ROUNDS:
            raise ElmError(
                f"keine Antwort vom Fahrzeug in {failed_rounds} Runden nacheinander "
                f"(Zündung aus?): {errors[-1]}"
            )
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
