"""Fahrzeug-Identifikation: FIN lesen (Mode 09, PID 02) und dekodieren.

Offline liefert eine WMI-Tabelle (FIN-Stellen 1-3) Hersteller und Land. Die
NHTSA-vPIC-Abfrage ist optional, nur nach Opt-in, und wird je FIN lokal gecacht.
"""

from dataclasses import dataclass, field
from pathlib import Path

from obd_diag.protocol.elm327 import Elm327


@dataclass(frozen=True)
class VinInfo:
    vin: str
    valid: bool  # 17 Zeichen, erlaubte Zeichen (kein I, O, Q)
    checksum_ok: bool | None  # Prüfziffer Stelle 9; None, wo sie nicht vorgeschrieben ist
    wmi: str
    manufacturer: str | None = None
    country: str | None = None
    model_year: int | None = None  # Stelle 10; mehrdeutig im 30-Jahres-Zyklus
    online: dict[str, str] = field(default_factory=dict)  # Zusatzangaben aus vPIC


def read_vin(elm: Elm327) -> str | None:
    """``None``, wenn das Fahrzeug Mode 09 PID 02 nicht unterstützt (vor ca. 2005)."""
    raise NotImplementedError


def decode_vin(vin: str) -> VinInfo:
    """Rein offline: Gültigkeit, Prüfziffer, WMI-Hersteller/Land, Modelljahr."""
    raise NotImplementedError


def lookup_vpic(vin: str, *, cache_dir: Path | None = None, timeout: float = 5.0) -> dict[str, str]:
    """Fragt NHTSA vPIC ab (nur aufrufen, wenn Nutzer zugestimmt hat).

    Ergebnisse werden unter ``cache_dir`` (Standard: $XDG_CACHE_HOME/obd-diag/vpic)
    je FIN abgelegt; eine FIN ändert sich nie, ein Abruf pro Fahrzeug reicht.
    Netzwerkfehler ergeben ein leeres dict.
    """
    raise NotImplementedError
