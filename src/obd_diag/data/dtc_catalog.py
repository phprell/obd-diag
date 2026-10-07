"""Offline-Katalog der Fehlercode-Texte (SQLite, erzeugt von tools/build_dtc_db.py).

Datenquelle: OBDex (https://github.com/foerbsnavi/OBDex), Daten unter CC0-1.0.
"""

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

DEFAULT_PATH = Path(__file__).with_name("dtc_catalog.sqlite")

_LANGS = ("de", "en")
_LIKELIHOOD_ORDER = "CASE likelihood WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END"


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


def _bool(value: int | None) -> bool | None:
    return None if value is None else bool(value)


class DtcCatalog:
    """Schlägt Fehlercodes nach. ``lang`` ist ``"de"`` oder ``"en"``; fehlt eine
    Übersetzung, wird auf Englisch zurückgefallen."""

    def __init__(self, path: Path = DEFAULT_PATH) -> None:
        self.path = path
        self._con: sqlite3.Connection | None = None

    @classmethod
    def default(cls) -> "DtcCatalog | None":
        """Der mitgelieferte Katalog, oder ``None``, wenn er noch nicht gebaut wurde."""
        return cls(DEFAULT_PATH) if DEFAULT_PATH.exists() else None

    def _connection(self) -> sqlite3.Connection:
        if self._con is None:
            # Nur lesend öffnen; eine fehlende Datei wird so nicht leer angelegt.
            uri = f"{self.path.resolve().as_uri()}?mode=ro"
            self._con = sqlite3.connect(uri, uri=True)
        return self._con

    def lookup(self, code: str, lang: str = "de") -> DtcInfo | None:
        if lang not in _LANGS:
            lang = "en"
        code = code.strip().upper()
        con = self._connection()
        # lang ist oben auf bekannte Werte begrenzt, daher sind die Spaltennamen sicher.
        row = con.execute(
            f"SELECT title_en, title_{lang}, description_en, description_{lang},"
            " mil, emissions_relevant, repair_difficulty, cost_eur_min, cost_eur_max"
            " FROM dtc WHERE code = ?",
            (code,),
        ).fetchone()
        if row is None:
            return None
        title_en, title, desc_en, desc, mil, emissions, difficulty, cost_min, cost_max = row
        causes = tuple(
            Cause(label=label_l or label_en, likelihood=likelihood)
            for label_en, label_l, likelihood in con.execute(
                f"SELECT label_en, label_{lang}, likelihood FROM cause WHERE code = ?"
                f" ORDER BY {_LIKELIHOOD_ORDER}, pos",
                (code,),
            )
        )
        symptoms = tuple(
            text_l or text_en
            for text_en, text_l in con.execute(
                f"SELECT text_en, text_{lang} FROM symptom WHERE code = ? ORDER BY pos",
                (code,),
            )
        )
        cost = (cost_min, cost_max) if cost_min is not None and cost_max is not None else None
        return DtcInfo(
            code=code,
            title=title or title_en,
            description=desc or desc_en,
            causes=causes,
            symptoms=symptoms,
            mil=_bool(mil),
            emissions_relevant=_bool(emissions),
            repair_difficulty=difficulty,
            cost_eur=cost,
        )

    def meta(self) -> dict[str, str]:
        """Herkunftsangaben des Katalogs (Quelle, Commit, Lizenz, Bauzeit)."""
        return dict(self._connection().execute("SELECT key, value FROM meta").fetchall())

    def close(self) -> None:
        if self._con is not None:
            self._con.close()
            self._con = None

    def __enter__(self) -> "DtcCatalog":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
