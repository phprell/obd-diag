"""Fahrzeug-Identifikation: FIN lesen (Mode 09, PID 02) und dekodieren.

Offline liefert eine WMI-Tabelle (FIN-Stellen 1-3) Hersteller und Land. Die
NHTSA-vPIC-Abfrage ist optional, nur nach Opt-in, und wird je FIN lokal gecacht.

Regeln der Dekodierung (``decode_vin``):

- Gültig: genau 17 Zeichen aus 0-9 und A-Z ohne I, O, Q (ISO 3779).
- Prüfziffer (Stelle 9, Gewichte nach 49 CFR 565.15 / ISO 3779 Anhang): vorgeschrieben
  nur für Nordamerika (WMI beginnt mit 1-5) und China (``L``, GB 16735); dort
  ``True``/``False``. Sonst (z. B. Europa, oft ``Z`` an Stelle 9) ``True``, wenn sie
  zufällig oder freiwillig stimmt, und ``None``, wenn nicht: ein Abweichen ist dort
  kein Fehler.
- Modelljahr (Stelle 10): der Code wiederholt sich alle 30 Jahre (``A`` = 1980 oder
  2010). In Nordamerika entscheidet Stelle 7 (49 CFR 565.15: Ziffer = 1980-2009,
  Buchstabe = 2010-2039); das gilt dort für Pkw, MPV und Lkw bis 10.000 lb, nicht
  für schwerere Fahrzeuge, Busse und Motorräder, wird hier aber für alle FIN mit
  ``1``-``5`` angewandt. Sonst gilt als beste Schätzung (``model_year``) das jüngste
  Jahr, das nicht mehr als ein Jahr in der Zukunft liegt (Modelljahre beginnen vor dem
  Kalenderjahr); das 30 Jahre ältere Jahr steht dann in ``model_year_alternatives``,
  sofern es nicht vor 1980 liegt (Beginn der Codes) und zum Protokoll passt (unten).
  Außerhalb Nordamerikas ist Stelle 10 nicht überall als Modelljahr genutzt, die
  Angabe ist dort ein Hinweis, keine Gewissheit.
- Plausibilität über das OBD-Protokoll (``decode_vin(vin, protocol=...)``, nur wenn die
  FIN aus dem Fahrzeug gelesen wurde): Spricht das Fahrzeug ein OBD-II-Protokoll
  (SAE J1850, ISO 9141-2, ISO 14230-4, ISO 15765-4), sind Modelljahre vor 1994
  unplausibel (die OBD-II-Spezifikation entstand um 1994, Pflicht in den USA ab
  Modelljahr 1996, EOBD in der EU ab 2001 Benziner/2004 Diesel). Bei CAN nach
  ISO 15765-4 sind Jahre vor 2000 unplausibel (CAN war für OBD-II in den USA erst ab
  Modelljahr 2003 zulässig; 2000 lässt Spielraum). Quelle: Wikipedia „On-board
  diagnostics“ (https://en.wikipedia.org/wiki/On-board_diagnostics, abgerufen
  2026-10-07). Solche Jahre fallen aus den Alternativen; die beste Schätzung bleibt
  unverändert, sie ist ohnehin das jüngere Jahr. SAE J1939 (Nutzfahrzeuge) und
  unbekannte Protokolle schränken nichts ein.
"""

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import urlopen

from obd_diag.data.wmi import country_for, manufacturer_for
from obd_diag.protocol.elm327 import Elm327, ElmError
from obd_diag.protocol.frames import FrameSequenceError, split_messages
from obd_diag.protocol.obd import read_with_headers

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class VinInfo:
    vin: str
    valid: bool  # 17 Zeichen, erlaubte Zeichen (kein I, O, Q)
    # Prüfziffer Stelle 9: True stimmt; False stimmt nicht, obwohl vorgeschrieben; None
    # stimmt nicht, ist hier aber nicht vorgeschrieben (oder FIN ungültig)
    checksum_ok: bool | None
    wmi: str
    manufacturer: str | None = None
    country: str | None = None
    model_year: int | None = None  # Stelle 10, beste Schätzung im 30-Jahres-Zyklus
    online: dict[str, str] = field(default_factory=dict)  # Zusatzangaben aus vPIC
    # ebenfalls mögliche Modelljahre (30 Jahre früher), leer wenn eindeutig
    model_year_alternatives: tuple[int, ...] = ()


def model_year_text(info: VinInfo) -> str | None:
    """z. B. „2026 oder 1996 (aus Stelle 10, ohne Gewähr)“; ``None`` ohne Modelljahr."""
    if info.model_year is None:
        return None
    years = " oder ".join(str(y) for y in (info.model_year, *info.model_year_alternatives))
    return f"{years} (aus Stelle 10, ohne Gewähr)"


def checksum_text(info: VinInfo) -> str:
    """Prüfziffer als Text, gleich in Kommandozeile, Bericht und Oberfläche.

    „stimmt“ auch dort, wo sie nicht vorgeschrieben ist; „nicht vorgeschrieben“ nur,
    wenn sie nicht stimmt, das aber kein Fehler ist (z. B. in Europa).
    """
    if not info.valid:
        return "nicht prüfbar (FIN ungültig)"
    if info.checksum_ok is None:
        return "nicht vorgeschrieben (passt nicht, kein Fehler)"
    return "stimmt" if info.checksum_ok else "stimmt nicht"


# --- FIN lesen ---

_VIN_LENGTH = 17
_LEGACY_DATA = 4  # Datenbytes je Zeile bei J1850/ISO 9141/KWP (nach 49 02 <Zähler>)
_PADDING = b"\x00\x20\xff"
_PRINTABLE_VIN = re.compile(r"^[0-9A-Z]{17}$")


def _clean(payload: bytes) -> str | None:
    """Entfernt Füllbytes und prüft auf 17 Zeichen; ``None`` bei allem anderen."""
    text = payload.strip(_PADDING)
    try:
        vin = text.decode("ascii").upper()
    except UnicodeDecodeError:
        return None
    return vin if _PRINTABLE_VIN.match(vin) else None


def _from_can(messages: list[bytes]) -> str | None:
    """Mehrteilige ISO-TP-Nachricht ``49 02 01`` + 17 Zeichen, eine je Steuergerät."""
    vins: list[str] = []
    for message in messages:
        payload = message[2:]
        if payload[:1] == b"\x01":  # Zahl der Datenelemente (J1979: immer 1)
            payload = payload[1:]
        vin = _clean(payload)
        if vin is not None and vin not in vins:
            vins.append(vin)
    if len(vins) > 1:
        log.warning("Steuergeräte melden verschiedene FIN %s, nehme die erste", vins)
    return vins[0] if vins else None


def _from_legacy(messages: list[bytes]) -> str | None:
    """Fünf Zeilen ``49 02 <Zähler 1-5> <4 Bytes>``, die erste vorn mit 00 aufgefüllt.

    Antworten mehrere Steuergeräte, kommen Zeilen mit gleichem Zähler doppelt; das ist
    nur in Ordnung, wenn sie gleich sind.
    """
    parts: dict[int, bytes] = {}
    for message in messages:
        if len(message) != 3 + _LEGACY_DATA:
            return None
        counter, data = message[2], message[3:]
        if parts.setdefault(counter, data) != data:
            log.warning("FIN-Zeile %d widersprüchlich (mehrere Steuergeräte?)", counter)
            return None
    if sorted(parts) != list(range(1, len(parts) + 1)):
        return None
    return _clean(b"".join(parts[n] for n in sorted(parts)))


def parse_vin_response(response: str) -> str | None:
    """FIN aus der Antwort auf ``0902`` (CAN mehrteilig oder ältere Protokolle).

    ``None``, wenn die Antwort keine 17-stellige FIN enthält (z. B. nur Füllbytes oder
    abgelehnt); ``ValueError`` bei Zeilen, die keine Hex-Daten sind, und
    ``FrameSequenceError`` (Unterklasse) bei vermischten Frames mehrerer Steuergeräte
    (siehe ``split_messages``).
    """
    return vin_from_messages(split_messages(response))


def vin_from_messages(messages: list[bytes]) -> str | None:
    """Wie ``parse_vin_response``, aber für bereits zerlegte Nachrichten."""
    messages = [m for m in messages if m[:2] == b"\x49\x02" and len(m) > 2]
    if not messages:
        return None
    if any(len(m) > 3 + _LEGACY_DATA for m in messages):
        return _from_can([m for m in messages if len(m) > 3 + _LEGACY_DATA])
    return _from_legacy(messages)


def read_vin(elm: Elm327) -> str | None:
    """``None``, wenn das Fahrzeug Mode 09 PID 02 nicht unterstützt (vor ca. 2005).

    Ebenso ``None`` (mit Log-Eintrag), wenn die Antwort keine gültig aussehende FIN
    enthält; ``ElmError``, wenn sie sich gar nicht zerlegen lässt. Senden mehrere
    Steuergeräte ihre FIN gleichzeitig mehrteilig (ohne Header nicht zuzuordnen), wird
    die Anfrage einmal mit Headern (``ATH1``) wiederholt.
    """
    response = elm.query("0902")
    if response is None:
        return None
    try:
        vin = parse_vin_response(response)
    except FrameSequenceError as e:
        vin = vin_from_messages([m.data for m in read_with_headers(elm, "0902", e)])
    except ValueError as e:
        raise ElmError(f"0902: {e}") from e
    if vin is None:
        log.info("0902: keine FIN in der Antwort %r", response)
    return vin


# --- FIN dekodieren ---

_VALID = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
_WEIGHTS = (8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2)
_VALUES = {
    **{str(n): n for n in range(10)},
    **dict(zip("ABCDEFGH", range(1, 9), strict=True)),
    **dict(zip("JKLMN", range(1, 6), strict=True)),
    "P": 7,
    "R": 9,
    **dict(zip("STUVWXYZ", range(2, 10), strict=True)),
}
# Modelljahr-Codes ab 1980 (bzw. 2010); I, O, Q, U, Z und 0 werden nicht verwendet
_YEAR_CODES = "ABCDEFGHJKLMNPRSTVWXY123456789"


def check_digit(vin: str) -> str:
    """Prüfziffer (``0``-``9`` oder ``X``) einer gültigen FIN nach 49 CFR 565.15."""
    total = sum(_VALUES[c] * w for c, w in zip(vin.upper(), _WEIGHTS, strict=True))
    rest = total % 11
    return "X" if rest == 10 else str(rest)


def _north_america(vin: str) -> bool:
    return vin[:1] in "12345"


def _checksum(vin: str) -> bool | None:
    matches = vin[8] == check_digit(vin)
    if _north_america(vin) or vin[0] == "L":
        return matches
    return True if matches else None


_FIRST_CODE_YEAR = 1980  # erster Zyklus der Modelljahr-Codes
_OBD2_EARLIEST = 1994  # OBD-II-Spezifikation (CARB) um 1994, Pflicht ab Modelljahr 1996
_CAN_EARLIEST = 2000  # CAN (ISO 15765-4) für OBD-II in den USA erst ab Modelljahr 2003
_OBD2_PROTOCOLS = ("J1850", "9141", "14230", "15765")


def earliest_plausible_year(protocol: str | None) -> int:
    """Frühestes plausibles Modelljahr für ein Fahrzeug, das dieses Protokoll spricht.

    ``protocol`` ist die Bezeichnung laut Adapter (``ATDP``, z. B. „ISO 15765-4 (CAN
    11/500)“); ``None``, leer, SAE J1939 oder unbekannt schränken nichts ein.
    """
    if not protocol:
        return _FIRST_CODE_YEAR
    if "15765" in protocol:
        return _CAN_EARLIEST
    if any(name in protocol for name in _OBD2_PROTOCOLS):
        return _OBD2_EARLIEST
    return _FIRST_CODE_YEAR


def model_years(
    vin: str, *, today: date | None = None, earliest: int = _FIRST_CODE_YEAR
) -> tuple[int, ...]:
    """Mögliche Modelljahre aus Stelle 10, beste Schätzung zuerst; ``()`` bei unbekanntem Code.

    Nordamerika: nur das Jahr nach Stelle 7. Sonst das jüngste Jahr bis ein Jahr in der
    Zukunft und, falls nicht vor ``earliest``, das 30 Jahre ältere.
    """
    pos = _YEAR_CODES.find(vin[9])
    if pos < 0:
        return ()
    if _north_america(vin):
        return (_FIRST_CODE_YEAR + pos + (0 if vin[6].isdigit() else 30),)
    limit = (today or date.today()).year + 1
    year = _FIRST_CODE_YEAR + pos
    while year + 30 <= limit:
        year += 30
    older = year - 30
    lowest = max(earliest, _FIRST_CODE_YEAR)
    return (year, older) if older >= lowest else (year,)


def model_year(vin: str, *, today: date | None = None) -> int | None:
    """Modelljahr (beste Schätzung) aus Stelle 10 einer gültigen FIN, ``None`` bei
    unbekanntem Code."""
    years = model_years(vin, today=today)
    return years[0] if years else None


def decode_vin(vin: str, *, protocol: str | None = None, today: date | None = None) -> VinInfo:
    """Rein offline: Gültigkeit, Prüfziffer, WMI-Hersteller/Land, Modelljahr.

    ``protocol``: OBD-Protokoll des Fahrzeugs, aus dem die FIN stammt (Bezeichnung
    laut Adapter); schließt unplausible ältere Modelljahre aus (siehe Moduldoku).
    """
    vin = vin.strip().upper()
    wmi = vin[:3]
    valid = bool(_VALID.match(vin))
    years = (
        model_years(vin, today=today, earliest=earliest_plausible_year(protocol)) if valid else ()
    )
    return VinInfo(
        vin=vin,
        valid=valid,
        checksum_ok=_checksum(vin) if valid else None,
        wmi=wmi,
        manufacturer=manufacturer_for(wmi) if len(wmi) == 3 else None,
        country=country_for(wmi),
        model_year=years[0] if years else None,
        model_year_alternatives=years[1:],
    )


# --- vPIC (online, nur nach Opt-in) ---

VPIC_URL = "https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues/{vin}?format=json"

# Übernommene vPIC-Felder und ihre deutsche Bezeichnung zur Anzeige. Als Schlüssel in
# ``VinInfo.online`` bleiben die vPIC-Namen, wie sie der PDF-Bericht erwartet.
VPIC_FIELDS = {
    "Make": "Hersteller",
    "Model": "Modell",
    "ModelYear": "Modelljahr",
    "Trim": "Ausstattung",
    "BodyClass": "Karosserie",
    "EngineCylinders": "Zylinder",
    "DisplacementL": "Hubraum (l)",
    "EngineHP": "Leistung (PS, US)",
    "FuelTypePrimary": "Kraftstoff",
    "TransmissionStyle": "Getriebe",
    "DriveType": "Antrieb",
    "PlantCountry": "Werk-Land",
    "PlantCity": "Werk-Ort",
}


def default_vpic_cache_dir() -> Path:
    """``$XDG_CACHE_HOME/obd-diag/vpic`` (Standard: ~/.cache/obd-diag/vpic)."""
    cache_home = os.environ.get("XDG_CACHE_HOME", "")
    base = Path(cache_home) if os.path.isabs(cache_home) else Path.home() / ".cache"
    return base / "obd-diag" / "vpic"


def _vpic_fields(data: Any) -> dict[str, str]:
    """Nicht leere Felder aus ``VPIC_FIELDS``; ``{}`` bei unerwarteter Form."""
    try:
        result = data["Results"][0]
    except (KeyError, IndexError, TypeError):
        return {}
    if not isinstance(result, dict):
        return {}
    out: dict[str, str] = {}
    for key in VPIC_FIELDS:
        value = result.get(key)
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            text = str(value).strip()
            if text and text.lower() not in ("0", "not applicable"):
                out[key] = text
    return out


def lookup_vpic(vin: str, *, cache_dir: Path | None = None, timeout: float = 5.0) -> dict[str, str]:
    """Fragt NHTSA vPIC ab (nur aufrufen, wenn Nutzer zugestimmt hat).

    Ergebnisse werden unter ``cache_dir`` (Standard: $XDG_CACHE_HOME/obd-diag/vpic)
    je FIN abgelegt; eine FIN ändert sich nie, ein Abruf pro Fahrzeug reicht.
    Netzwerkfehler ergeben ein leeres dict.
    """
    vin = vin.strip().upper()
    if not _VALID.match(vin):
        return {}  # nichts Ungültiges ins Netz schicken
    directory = cache_dir if cache_dir is not None else default_vpic_cache_dir()
    cache = directory / f"{vin}.json"
    try:
        return _vpic_fields(json.loads(cache.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass  # noch nicht im Cache oder unlesbar: neu abfragen
    try:
        with urlopen(VPIC_URL.format(vin=quote(vin)), timeout=timeout) as response:
            raw = response.read()
        data = json.loads(raw)
    except (URLError, OSError, ValueError) as e:
        log.warning("vPIC-Abfrage fehlgeschlagen: %s", e)
        return {}
    fields = _vpic_fields(data)
    if fields:  # leere Antworten nicht cachen, später erneut versuchen
        try:
            directory.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except OSError as e:
            log.warning("vPIC-Cache nicht beschreibbar: %s", e)
    return fields
