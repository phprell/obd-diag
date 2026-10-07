"""Offline-Katalog der Fehlercode-Texte (SQLite, erzeugt von tools/build_dtc_db.py).

Datenquelle: OBDex (https://github.com/foerbsnavi/OBDex), Daten unter CC0-1.0.
"""

from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATH = Path(__file__).with_name("dtc_catalog.sqlite")


@dataclass(frozen=True)
class Cause:
    label: str
    likelihood: str  # "high" | "medium" | "low"


@dataclass(frozen=True)
class DtcInfo:
    code: str
    title: str
    description: str | None = None
    causes: tuple[Cause, ...] = ()
    symptoms: tuple[str, ...] = ()
    mil: bool | None = None
    emissions_relevant: bool | None = None
    repair_difficulty: str | None = None
    cost_eur: tuple[int, int] | None = None


class DtcCatalog:
    """Schlägt Fehlercodes nach. ``lang`` ist ``"de"`` oder ``"en"``; fehlt eine
    Übersetzung, wird auf Englisch zurückgefallen."""

    def __init__(self, path: Path = DEFAULT_PATH) -> None:
        self.path = path

    @classmethod
    def default(cls) -> "DtcCatalog | None":
        """Der mitgelieferte Katalog, oder ``None``, wenn er noch nicht gebaut wurde."""
        return cls(DEFAULT_PATH) if DEFAULT_PATH.exists() else None

    def lookup(self, code: str, lang: str = "de") -> DtcInfo | None:
        raise NotImplementedError

    def close(self) -> None:
        pass
