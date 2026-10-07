"""Mode-01-PIDs für Live-Daten nach SAE J1979: Tabelle, Dekodierung, unterstützte PIDs.

Nur lesend: gesendet werden ausschließlich ``01xx``-Anfragen über ``Elm327.query``.
"""

from collections.abc import Callable
from dataclasses import dataclass

from obd_diag.protocol.elm327 import Elm327


@dataclass(frozen=True)
class PidSpec:
    """Ein Live-Wert: PID, Schlüssel, deutscher Name, Einheit, Dekodierung."""

    pid: int  # z. B. 0x0C
    key: str  # stabiler Schlüssel für CSV, CLI und GUI, z. B. "rpm"
    name: str  # deutsch, z. B. "Motordrehzahl"
    unit: str  # z. B. "1/min", "km/h", "°C", "%", "kPa", "g/s", "V", "km"
    size: int  # Anzahl Datenbytes nach ``41 <pid>``
    decode: Callable[[bytes], float]  # bekommt genau ``size`` Bytes
    minimum: float  # Wertebereich laut J1979, für Skalen in der Anzeige
    maximum: float


# Alle unterstützten Live-Werte, nach PID. Inhalt: siehe Implementierung.
PIDS: dict[int, PidSpec] = {}


def pid_by_key(key: str) -> PidSpec:
    """Die PID zu ``key``; ``KeyError`` mit verständlicher Meldung, wenn unbekannt."""
    raise NotImplementedError


def parse_supported(base: int, data: bytes) -> set[int]:
    """PIDs aus der Bitmaske einer Antwort auf ``01<base>`` (base = 0x00, 0x20, …).

    ``data`` sind die vier Datenbytes; Bit 7 des ersten Bytes steht für ``base + 1``.
    """
    raise NotImplementedError


def read_supported_pids(elm: Elm327) -> set[int]:
    """Alle PIDs, die mindestens ein Steuergerät unterstützt (Vereinigung).

    Fragt ``0100`` und folgt der Kette (``0120``, ``0140`` …), solange ein Steuergerät
    das Bit für den nächsten Block setzt. ``NO DATA`` ergibt eine leere Menge.
    """
    raise NotImplementedError


def read_value(elm: Elm327, spec: PidSpec) -> float | None:
    """Ein Live-Wert vom ersten Steuergerät, das ``spec.pid`` gültig beantwortet.

    ``None`` bei ``NO DATA``, Ablehnung oder zu kurzer Antwort; ``ElmError`` bei
    Adapterfehlern geht an den Aufrufer.
    """
    raise NotImplementedError
