"""Live-Daten (Mode 01) fortlaufend lesen und als CSV aufzeichnen; nur lesend.

Ablauf: ``prepare_live`` initialisiert den Adapter und ermittelt die unterstützten
Werte, ``run_live`` fragt die gewählten Werte reihum ab, bis ``should_stop`` True
liefert oder ``max_samples`` erreicht ist. Die Bordspannung wird regelmäßig gelesen;
unter ``LOW_VOLTAGE`` wird die Abfrage gedrosselt.
"""

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Self

from obd_diag.protocol.elm327 import Elm327
from obd_diag.protocol.pids import PidSpec

DEFAULT_INTERVAL = 1.0  # Sekunden zwischen zwei Abfragerunden
LOW_VOLTAGE_INTERVAL = 5.0  # Sekunden zwischen Runden bei niedriger Bordspannung
VOLTAGE_EVERY = 10  # Bordspannung jede n-te Runde lesen (ATRV)
# Die üblichen Werte, wenn der Nutzer keine Auswahl trifft (sofern unterstützt).
DEFAULT_KEYS = ("rpm", "speed", "coolant_temp", "engine_load", "intake_temp", "control_voltage")


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
    """Adapter initialisieren, Protokoll aushandeln, unterstützte Live-Werte ermitteln."""
    raise NotImplementedError


def select_pids(setup: LiveSetup, keys: Sequence[str] | None) -> list[PidSpec]:
    """Die gewünschten Werte (``None``: ``DEFAULT_KEYS``), soweit unterstützt.

    Unbekannte Schlüssel ergeben ``ValueError``; bekannte, aber nicht unterstützte
    ebenfalls (Meldung nennt sie), damit der Nutzer nicht stumm leere Spalten bekommt.
    Bei ``None`` werden nicht unterstützte Standardwerte stillschweigend weggelassen.
    """
    raise NotImplementedError


class LiveRecorder:
    """Schreibt Live-Werte als CSV (UTF-8 mit BOM, Semikolon, Dezimalkomma wie export).

    Kopfzeile: ``Zeit (s)``, dann je Wert ``Name (Einheit)``, zuletzt ``Bordspannung (V)``.
    Jede Zeile wird sofort geschrieben (flush), damit ein Abbruch nichts verliert.
    Vorhandene Dateien werden nie überschrieben.
    """

    def __init__(self, path: Path, pids: Sequence[PidSpec]) -> None:
        self.path = path
        self.pids = list(pids)

    def write(self, sample: LiveSample) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

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
    raise NotImplementedError


def new_recording_path(directory: Path) -> Path:
    """Freier Name ``live-YYYYmmdd-HHMMSS[-n].csv``; legt den Ordner an."""
    raise NotImplementedError


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
    lesbarer Wert ergibt ``None`` in der Runde (kein Abbruch); ``TransportError``
    bricht ab. ``should_stop`` wird vor jeder Runde und während des Wartens geprüft.
    Liefert die Anzahl der Runden.
    """
    raise NotImplementedError
