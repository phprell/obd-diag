"""Schreibt den DTC-Katalog (SQLite) aus bereits geparsten OBDex-Einträgen.

Das Parsen der YAML-Quellen übernimmt ``tools/build_dtc_db.py``; dieses Modul kennt
nur Python-Dicts und die Standardbibliothek, damit es ohne PyYAML testbar bleibt.
"""

import os
import sqlite3
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1"

SCHEMA = """
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE dtc (
    code                 TEXT PRIMARY KEY,  -- Großbuchstaben, z. B. P0420
    category             TEXT NOT NULL,
    title_en             TEXT NOT NULL,
    title_de             TEXT,
    description_en       TEXT,
    description_de       TEXT,
    repair_difficulty    TEXT,              -- easy | medium | hard | shop_only
    diy_possible         INTEGER,           -- 0/1, NULL = unbekannt
    cost_eur_min         INTEGER,
    cost_eur_max         INTEGER,
    hours_min            REAL,
    hours_max            REAL,
    mil                  INTEGER,
    emissions_relevant   INTEGER,
    drive_cycle_required INTEGER,
    limp_mode_possible   INTEGER,
    related_codes        TEXT               -- kommagetrennt
);
CREATE TABLE cause (
    code       TEXT NOT NULL REFERENCES dtc(code),
    pos        INTEGER NOT NULL,
    cause_id   TEXT NOT NULL,
    likelihood TEXT NOT NULL,              -- high | medium | low
    label_en   TEXT NOT NULL,
    label_de   TEXT,
    PRIMARY KEY (code, pos)
);
CREATE TABLE symptom (
    code    TEXT NOT NULL REFERENCES dtc(code),
    pos     INTEGER NOT NULL,
    text_en TEXT NOT NULL,
    text_de TEXT,
    PRIMARY KEY (code, pos)
);
"""


class CatalogBuildError(ValueError):
    """Ein Quelleintrag ist unvollständig oder doppelt."""


def _text(value: Any, lang: str) -> str | None:
    if isinstance(value, Mapping):
        text = value.get(lang)
        return str(text) if text else None
    return None


def _flag(value: Any) -> int | None:
    return None if value is None else int(bool(value))


def _pair(value: Any) -> tuple[Any, Any]:
    if isinstance(value, list | tuple) and len(value) == 2:
        return value[0], value[1]
    return None, None


def _dtc_row(entry: Mapping[str, Any]) -> tuple[Any, ...]:
    code = entry.get("code")
    category = entry.get("category")
    title_en = _text(entry.get("title"), "en")
    if not code or not category or not title_en:
        raise CatalogBuildError(f"Eintrag unvollständig (code/category/title.en): {code!r}")
    repair = entry.get("repair") or {}
    flags = entry.get("flags") or {}
    cost_min, cost_max = _pair(repair.get("estimated_cost_eur"))
    hours_min, hours_max = _pair(repair.get("estimated_hours"))
    related = entry.get("related_codes") or []
    return (
        str(code).upper(),
        str(category),
        title_en,
        _text(entry.get("title"), "de"),
        _text(entry.get("description"), "en"),
        _text(entry.get("description"), "de"),
        repair.get("difficulty"),
        _flag(repair.get("diy_possible")),
        None if cost_min is None else round(cost_min),
        None if cost_max is None else round(cost_max),
        hours_min,
        hours_max,
        _flag(flags.get("mil")),
        _flag(flags.get("emissions_relevant")),
        _flag(flags.get("drive_cycle_required")),
        _flag(flags.get("limp_mode_possible")),
        ",".join(str(c).upper() for c in related) or None,
    )


def _write(con: sqlite3.Connection, entries: Iterable[Mapping[str, Any]]) -> int:
    count = 0
    for entry in entries:
        row = _dtc_row(entry)
        code = row[0]
        try:
            con.execute(f"INSERT INTO dtc VALUES ({','.join('?' * len(row))})", row)
        except sqlite3.IntegrityError as exc:
            raise CatalogBuildError(f"Code doppelt: {code}") from exc
        for pos, cause in enumerate(entry.get("common_causes") or []):
            label_en = _text(cause.get("label"), "en")
            if label_en is None:
                continue
            con.execute(
                "INSERT INTO cause VALUES (?, ?, ?, ?, ?, ?)",
                (
                    code,
                    pos,
                    cause.get("id", ""),
                    cause.get("likelihood", "low"),
                    label_en,
                    _text(cause.get("label"), "de"),
                ),
            )
        for pos, symptom in enumerate(entry.get("symptoms") or []):
            text_en = _text(symptom, "en")
            if text_en is None:
                continue
            con.execute(
                "INSERT INTO symptom VALUES (?, ?, ?, ?)",
                (code, pos, text_en, _text(symptom, "de")),
            )
        count += 1
    return count


def build(
    entries: Iterable[Mapping[str, Any]],
    out_path: Path,
    meta: Mapping[str, str] | None = None,
) -> int:
    """Baut den Katalog nach ``out_path`` und gibt die Anzahl der Codes zurück.

    Geschrieben wird in eine temporäre Datei daneben, die erst nach Erfolg atomar
    umbenannt wird; ein abgebrochener Lauf hinterlässt keinen halben Katalog.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".dtc_catalog-", suffix=".tmp", dir=out_path.parent)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        con = sqlite3.connect(tmp)
        try:
            with con:
                con.executescript(SCHEMA)
                count = _write(con, entries)
                all_meta = {"schema_version": SCHEMA_VERSION, "code_count": str(count)}
                all_meta.update(meta or {})
                con.executemany("INSERT INTO meta VALUES (?, ?)", all_meta.items())
            con.execute("VACUUM")
        finally:
            con.close()
        tmp.chmod(0o644)
        tmp.replace(out_path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return count
