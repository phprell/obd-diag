"""Kurze Online-Erklärungen für Fehlercodes ohne Katalogtext (nur nach Opt-in).

Quelle ist die DTC-Sammlung von Wal33D (https://github.com/Wal33D/dtc-database,
MIT-Lizenz), fest auf einen Commit gepinnt: Je Hersteller und je Codefamilie gibt es
eine Textdatei mit Zeilen ``CODE - Beschreibung`` (englisch). Geladen wird die ganze
Datei von raw.githubusercontent.com; ins Netz geht also nur, *welche* Datei gebraucht
wird (z. B. ``mercedes_codes.txt``), weder Fehlercode noch FIN. Wegen des gepinnten
Commits ändert sich eine Datei nie, sie wird einmal geladen und dauerhaft unter
``$XDG_CACHE_HOME/obd-diag/dtc-online/<commit>/`` abgelegt.

Die Texte sind ungeprüft (Herkunft nicht belegt, Qualität schwankt) und werden immer
mit Quelle und Link gezeigt. Herstellerspezifische Codes werden nur in der Datei des
Herstellers gesucht: Derselbe Code bedeutet bei einem anderen Hersteller etwas anderes
(U1218 ist bei Ford „SCP (J1850) Invalid or Missing Data for External Lamps“), daher
wird die gemischte Datei ``other_codes.txt`` nie benutzt. Genormte Codes (SAE J2012)
kommen aus den allgemeinen Dateien. Netzwerkfehler ergeben einfach keine Erklärung.
"""

import logging
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from urllib.error import URLError
from urllib.parse import quote_plus
from urllib.request import urlopen

log = logging.getLogger(__name__)

SOURCE_NAME = "Wal33D/dtc-database (MIT)"
SOURCE_REPO = "https://github.com/Wal33D/dtc-database"
SOURCE_COMMIT = "04c43d72e7db7197658b6f72fe582c5076d9eee8"
RAW_URL = f"https://raw.githubusercontent.com/Wal33D/dtc-database/{SOURCE_COMMIT}/data/source-data/{{file}}"
BLOB_URL = f"{SOURCE_REPO}/blob/{SOURCE_COMMIT}/data/source-data/{{file}}#L{{line}}"

# Hersteller laut FIN-Dekodierung (``data/wmi.py``) → Datei der Quelle. Verglichen
# wird der Anfang des Namens, damit z. B. „Mercedes-Benz (SUV)“ mitzählt.
BRAND_FILES: tuple[tuple[str, str], ...] = (
    ("Acura", "acura_codes.txt"),
    ("Audi", "audi_codes.txt"),
    ("BMW", "bmw_codes.txt"),
    ("Buick", "buick_codes.txt"),
    ("Cadillac", "cadillac_codes.txt"),
    ("Chevrolet", "chevy_codes.txt"),
    ("Chrysler", "chrysler_codes.txt"),
    ("Dodge", "dodge_codes.txt"),
    ("Ford", "ford_codes.txt"),
    ("GMC", "gmc_codes.txt"),
    ("Honda", "honda_codes.txt"),
    ("Infiniti", "infiniti_codes.txt"),
    ("Jaguar", "jaguar_codes.txt"),
    ("Jeep", "jeep_codes.txt"),
    ("Kia", "kia_codes.txt"),
    ("Lexus", "lexus_codes.txt"),
    ("Lincoln", "lincoln_codes.txt"),
    ("Mazda", "mazda_codes.txt"),
    ("Mercedes-Benz", "mercedes_codes.txt"),
    ("Mercury", "mercury_codes.txt"),
    ("Mitsubishi", "mitsubishi_codes.txt"),
    ("Nissan", "nissan_codes.txt"),
    ("Subaru", "subaru_codes.txt"),
    ("Suzuki", "suzuki_codes.txt"),
    ("Toyota", "toyota_codes.txt"),
    ("Volkswagen", "volkswagen_codes.txt"),
)

GENERIC_FILES = {"P": "p_codes.txt", "B": "b_codes.txt", "C": "c_codes.txt", "U": "u_codes.txt"}

SEARCH_URL = "https://duckduckgo.com/?q={query}"

_CODE = re.compile(r"^[PBCU][0-3][0-9A-F]{3}$")
_LINE = re.compile(r"^([PBCU][0-3][0-9A-F]{3})\s+-\s+(.+)$")
MAX_TEXT = 300  # Zeichen; eine Zeile der Quelle ist eine Kurzbeschreibung


@dataclass(frozen=True)
class OnlineExplanation:
    """Ungeprüfte Kurzbeschreibung aus dem Netz, mit Herkunft."""

    text: str  # englisch, wie in der Quelle
    source: str  # z. B. "Wal33D/dtc-database (MIT), mercedes_codes.txt"
    url: str  # Link auf die Zeile in der Quelle
    manufacturer: str | None = None  # Herstellerdatei, sonst None (genormter Code)


def is_generic(code: str) -> bool:
    """Genormter Code nach SAE J2012 (gleiche Bedeutung bei allen Herstellern).

    Herstellerspezifisch sind P1, P30 bis P33, B1/B2, C1/C2, U1/U2; alles andere legt die
    Norm fest (P34 bis P39 und B3/C3/U3 sind für sie reserviert).
    """
    code = code.upper()
    family, digit = code[0], code[1]
    if digit in "03" and family in "BCU":
        return True
    if family == "P":
        return digit in "02" or (digit == "3" and code[2] in "456789")
    return False


def brand_file(manufacturer: str | None) -> str | None:
    """Datei der Quelle für den Hersteller aus der FIN, sonst ``None``."""
    if not manufacturer:
        return None
    for prefix, file in BRAND_FILES:
        if manufacturer == prefix or manufacturer.startswith(prefix + " "):
            return file
    return None


def search_url(code: str, manufacturer: str | None = None) -> str:
    """Link für eine Websuche im Browser; wird nie von obd-diag selbst abgerufen."""
    brand = (manufacturer or "").split(" (")[0].strip()
    query = f"{code} {brand}".strip() if brand else f"OBD {code}"
    return SEARCH_URL.format(query=quote_plus(query))


def default_cache_dir() -> Path:
    """``$XDG_CACHE_HOME/obd-diag/dtc-online`` (Standard: ~/.cache/obd-diag/dtc-online)."""
    cache_home = os.environ.get("XDG_CACHE_HOME", "")
    base = Path(cache_home) if os.path.isabs(cache_home) else Path.home() / ".cache"
    return base / "obd-diag" / "dtc-online"


def parse_source(text: str) -> dict[str, tuple[str, int]]:
    """``CODE - Beschreibung``-Zeilen → Code: (Beschreibung, Zeilennummer); der erste zählt."""
    entries: dict[str, tuple[str, int]] = {}
    for number, line in enumerate(text.splitlines(), 1):
        match = _LINE.match(line.strip())
        if match is None:
            continue
        code, description = match.group(1), " ".join(match.group(2).split())
        if len(description) > MAX_TEXT:
            description = description[: MAX_TEXT - 1].rstrip() + "…"
        entries.setdefault(code, (description, number))
    return entries


def _load(file: str, cache_dir: Path, timeout: float) -> dict[str, tuple[str, int]] | None:
    """Datei aus dem Cache oder dem Netz; ``None``, wenn sie nicht zu bekommen ist."""
    cache = cache_dir / SOURCE_COMMIT / file
    try:
        return parse_source(cache.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        pass  # noch nicht im Cache oder unlesbar: neu laden
    try:
        with urlopen(RAW_URL.format(file=file), timeout=timeout) as response:
            raw: bytes = response.read()
        text = raw.decode("utf-8")
    except (URLError, OSError, UnicodeDecodeError, ValueError) as e:
        log.warning("Online-Erklärungen (%s) nicht ladbar: %s", file, e)
        return None
    entries = parse_source(text)
    if entries:  # leere oder fremde Antworten nicht cachen
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache.with_suffix(".tmp")
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(cache)
        except OSError as e:
            log.warning("Cache für Online-Erklärungen nicht beschreibbar: %s", e)
    return entries


def lookup_online(
    codes: Iterable[str],
    manufacturer: str | None = None,
    *,
    cache_dir: Path | None = None,
    timeout: float = 5.0,
) -> dict[str, OnlineExplanation]:
    """Kurzbeschreibungen für ``codes`` (nur aufrufen, wenn der Nutzer zugestimmt hat).

    ``manufacturer`` ist der Hersteller aus der FIN (``VinInfo.manufacturer``). Ohne
    ihn bleiben herstellerspezifische Codes ohne Erklärung. Codes ohne Treffer fehlen
    im Ergebnis; Netzwerkfehler auch (die Diagnose läuft normal weiter).
    """
    directory = cache_dir if cache_dir is not None else default_cache_dir()
    brand = brand_file(manufacturer)
    loaded: dict[str, dict[str, tuple[str, int]] | None] = {}
    result: dict[str, OnlineExplanation] = {}
    for code in dict.fromkeys(c.strip().upper() for c in codes):
        if not _CODE.match(code):
            continue  # nichts Unerwartetes nachschlagen
        # Herstellerdatei zuerst (sie ist genauer), genormte Codes notfalls allgemein
        files = [brand] if brand is not None else []
        if is_generic(code):
            files.append(GENERIC_FILES[code[0]])
        for file in files:
            if file not in loaded:
                loaded[file] = _load(file, directory, timeout)
            entries = loaded[file]
            if entries is None or code not in entries:
                continue
            text, line = entries[code]
            result[code] = OnlineExplanation(
                text=text,
                source=f"{SOURCE_NAME}, {file}",
                url=BLOB_URL.format(file=file, line=line),
                manufacturer=manufacturer if file == brand else None,
            )
            break
    return result
