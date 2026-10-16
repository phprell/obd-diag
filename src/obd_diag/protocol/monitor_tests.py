"""Mode 06: Ergebnisse der On-Board-Überwachungstests mit Grenzwerten (nur CAN).

Aufbau nach SAE J1979 / ISO 15031-5 für ISO 15765-4 (CAN):

- ``06 00``, ``06 20`` … ``06 E0``: unterstützte Monitor-IDs (OBDMID), Antwort
  ``46 <MID> AA BB CC DD`` wie bei Mode 01 PID 00; das letzte Bit meldet den nächsten
  Bereich.
- ``06 <MID>``: Antwort ``46`` und je Test 9 Bytes ``<MID> <TID> <UASID> <Wert 2>
  <Min 2> <Max 2>``. Die Test-ID (TID) ist unter $80 genormt, ab $80 legt sie der
  Hersteller fest. Die UASID (Unit and Scaling ID) sagt, wie die drei Zahlen
  umzurechnen sind (``UNITS``): $01 bis $7F ohne Vorzeichen, $80 bis $FE mit Vorzeichen
  (Zweierkomplement). Bestanden ist ein Test, wenn Min ≤ Wert ≤ Max.

Die Namen der Monitor-IDs folgen J1979 Anhang D, die Umrechnungen Anhang E; beides
gegengeprüft mit python-OBD (``obd/commands.py`` Mode 06, ``obd/UnitsAndScaling.py``),
siehe ``tests/verification/test_monitor_tests_differential.py``.

Ältere Protokolle (J1850, ISO 9141, KWP) haben für Mode 06 ein anderes, nicht genormt
umrechenbares Format; ``read_test_results`` liefert dort ``None``.
"""

import logging
from dataclasses import dataclass
from enum import StrEnum

from obd_diag.i18n import N_, tr
from obd_diag.protocol.elm327 import BufferFullError, Elm327, ElmError
from obd_diag.protocol.headers import (
    EcuMessage,
    parse_header_response,
    parse_truncated_header_response,
)

log = logging.getLogger(__name__)

SID = 0x46  # positive Antwort auf Service $06
RECORD_SIZE = 9  # MID, TID, UASID, Wert, Min, Max
_CAN_HEADER_LENGTHS = (3, 8)
_RANGES = tuple(range(0x00, 0x100, 0x20))  # 00, 20, … E0: Abfragen der unterstützten MIDs


@dataclass(frozen=True)
class Unit:
    """Umrechnung einer UASID: Wert = Rohwert · ``scale`` + ``offset``."""

    scale: float
    unit: str = ""  # Einheit wie angezeigt; leer für Zahlen ohne Einheit
    offset: float = 0.0


# J1979 Anhang E (Unit and Scaling IDs). Vorzeichen ergibt sich aus der ID (ab $80).
UNITS: dict[int, Unit] = {
    0x01: Unit(1),
    0x02: Unit(0.1),
    0x03: Unit(0.01),
    0x04: Unit(0.001),
    0x05: Unit(0.0000305),
    0x06: Unit(0.000305),
    0x07: Unit(0.25, "1/min"),
    0x08: Unit(0.01, "km/h"),
    0x09: Unit(1, "km/h"),
    0x0A: Unit(0.122, "mV"),
    0x0B: Unit(0.001, "V"),
    0x0C: Unit(0.01, "V"),
    0x0D: Unit(0.00390625, "mA"),
    0x0E: Unit(0.001, "A"),
    0x0F: Unit(0.01, "A"),
    0x10: Unit(1, "ms"),
    0x11: Unit(100, "ms"),
    0x12: Unit(1, "s"),
    0x13: Unit(1, "mΩ"),
    0x14: Unit(1, "Ω"),
    0x15: Unit(1, "kΩ"),
    0x16: Unit(0.1, "°C", -40.0),
    0x17: Unit(0.01, "kPa"),
    0x18: Unit(0.0117, "kPa"),
    0x19: Unit(0.079, "kPa"),
    0x1A: Unit(1, "kPa"),
    0x1B: Unit(10, "kPa"),
    0x1C: Unit(0.01, "°"),
    0x1D: Unit(0.5, "°"),
    0x1E: Unit(0.0000305),  # Verhältnis (z. B. Lambda)
    0x1F: Unit(0.05),  # Verhältnis
    0x20: Unit(0.00390625),  # Verhältnis
    0x21: Unit(1, "mHz"),
    0x22: Unit(1, "Hz"),
    0x23: Unit(1, "kHz"),
    0x24: Unit(1),  # Zähler
    0x25: Unit(1, "km"),
    0x26: Unit(0.1, "mV/ms"),
    0x27: Unit(0.01, "g/s"),
    0x28: Unit(1, "g/s"),
    0x29: Unit(0.25, "Pa/s"),
    0x2A: Unit(0.001, "kg/h"),
    0x2B: Unit(1),  # Wechsel (Zähler)
    0x2C: Unit(0.01, "g"),  # je Zylinder
    0x2D: Unit(0.01, "mg"),  # je Hub
    0x2E: Unit(1),  # Wahrheitswert (0/1)
    0x2F: Unit(0.01, "%"),
    0x30: Unit(0.001526, "%"),
    0x31: Unit(0.001, "l"),
    0x32: Unit(0.0000305, "in"),
    0x33: Unit(0.00024414),  # Verhältnis
    0x34: Unit(1, "min"),
    0x35: Unit(10, "ms"),
    0x36: Unit(0.01, "g"),
    0x37: Unit(0.1, "g"),
    0x38: Unit(1, "g"),
    0x39: Unit(0.01, "%", -327.68),
    0x3A: Unit(0.001, "g"),
    0x3B: Unit(0.0001, "g"),
    0x3C: Unit(0.1, "µs"),
    0x3D: Unit(0.01, "mA"),
    0x3E: Unit(0.00006103516, "mm²"),
    0x3F: Unit(0.01, "l"),
    0x40: Unit(1, "ppm"),
    0x41: Unit(0.01, "µA"),
    # mit Vorzeichen
    0x81: Unit(1),
    0x82: Unit(0.1),
    0x83: Unit(0.01),
    0x84: Unit(0.001),
    0x85: Unit(0.0000305),
    0x86: Unit(0.000305),
    0x87: Unit(1, "ppm"),
    0x8A: Unit(0.122, "mV"),
    0x8B: Unit(0.001, "V"),
    0x8C: Unit(0.01, "V"),
    0x8D: Unit(0.00390625, "mA"),
    0x8E: Unit(0.001, "A"),
    0x90: Unit(1, "ms"),
    0x96: Unit(0.1, "°C"),
    0x99: Unit(0.1, "kPa"),
    0x9C: Unit(0.01, "°"),
    0x9D: Unit(0.5, "°"),
    0xA8: Unit(1, "g/s"),
    0xA9: Unit(0.25, "Pa/s"),
    0xAD: Unit(0.01, "mg"),  # je Hub
    0xAE: Unit(0.1, "mg"),  # je Hub
    0xAF: Unit(0.01, "%"),
    0xB0: Unit(0.003052, "%"),
    0xB1: Unit(2, "mV/s"),
    0xFC: Unit(0.01, "kPa"),
    0xFD: Unit(0.001, "kPa"),
    0xFE: Unit(0.25, "Pa"),
}


def is_signed(uasid: int) -> bool:
    """UASID $80 bis $FF: Werte mit Vorzeichen (J1979 Anhang E)."""
    return uasid >= 0x80


# Monitor-IDs nach J1979 Anhang D: (erste MID, letzte MID, Bezeichnung). ``{n}`` ist die
# laufende Nummer ab der ersten MID (Bank bzw. Zylinder), bei Lambdasonden stehen Bank
# und Sensor (je vier Sensoren pro Bank).
_O2_SENSOR = N_("Lambdasonde Bank {bank} Sensor {sensor}")
_O2_HEATER = N_("Lambdasondenheizung Bank {bank} Sensor {sensor}")
_MID_RANGES: tuple[tuple[int, int, str], ...] = (
    (0x21, 0x24, N_("Katalysator Bank {n}")),
    (0x31, 0x34, N_("Abgasrückführung (AGR) Bank {n}")),
    (0x35, 0x38, N_("Nockenwellenverstellung (VVT) Bank {n}")),
    (0x61, 0x64, N_("Beheizter Katalysator Bank {n}")),
    (0x71, 0x74, N_("Sekundärluft {n}")),
    (0x81, 0x84, N_("Kraftstoffsystem Bank {n}")),
    (0x85, 0x86, N_("Ladedruckregelung Bank {n}")),
    (0x90, 0x91, N_("NOx-Speicherkatalysator Bank {n}")),
    (0x98, 0x99, N_("NOx-/SCR-Katalysator Bank {n}")),
    (0xA2, 0xAD, N_("Verbrennungsaussetzer Zylinder {n}")),
    (0xB0, 0xB1, N_("Partikelfilter Bank {n}")),
)
_MID_NAMES: dict[int, str] = {
    0x39: N_("Tankentlüftung: Deckel offen / 0,150 Zoll"),
    0x3A: N_("Tankentlüftung: Leck 0,090 Zoll"),
    0x3B: N_("Tankentlüftung: Leck 0,040 Zoll"),
    0x3C: N_("Tankentlüftung: Leck 0,020 Zoll"),
    0x3D: N_("Spülluftstrom Tankentlüftung"),
    0xA1: N_("Verbrennungsaussetzer allgemein"),
}

# Genormte Test-IDs (J1979; Lambdasonden 01-0A, Aussetzer 0B/0C)
TEST_NAMES: dict[int, str] = {
    0x01: N_("Spannungsschwelle fett nach mager"),
    0x02: N_("Spannungsschwelle mager nach fett"),
    0x03: N_("Untere Spannung für Umschaltzeit"),
    0x04: N_("Obere Spannung für Umschaltzeit"),
    0x05: N_("Umschaltzeit fett nach mager"),
    0x06: N_("Umschaltzeit mager nach fett"),
    0x07: N_("Kleinste Spannung im Testzyklus"),
    0x08: N_("Größte Spannung im Testzyklus"),
    0x09: N_("Zeit zwischen Umschaltungen"),
    0x0A: N_("Sensorperiode"),
    0x0B: N_("Aussetzer, Mittel der letzten zehn Fahrzyklen"),
    0x0C: N_("Aussetzer im letzten/aktuellen Fahrzyklus"),
}


def mid_name(mid: int) -> str:
    """Bezeichnung einer Monitor-ID in der eingestellten Sprache."""
    if 0x01 <= mid <= 0x10:
        return tr(_O2_SENSOR).format(bank=(mid - 1) // 4 + 1, sensor=(mid - 1) % 4 + 1)
    if 0x41 <= mid <= 0x50:
        return tr(_O2_HEATER).format(bank=(mid - 0x41) // 4 + 1, sensor=(mid - 0x41) % 4 + 1)
    if mid in _MID_NAMES:
        return tr(_MID_NAMES[mid])
    for first, last, label in _MID_RANGES:
        if first <= mid <= last:
            return tr(label).format(n=mid - first + 1)
    return tr("Monitor {mid:02X}").format(mid=mid)


def tid_name(tid: int) -> str:
    """Bezeichnung einer Test-ID; ab $80 herstellerspezifisch."""
    if tid in TEST_NAMES:
        return tr(TEST_NAMES[tid])
    if tid >= 0x80:
        return tr("Herstellertest {tid:02X}").format(tid=tid)
    return tr("Test {tid:02X}").format(tid=tid)


class TestStatus(StrEnum):
    __test__ = False  # kein pytest-Testfall trotz des Namens

    PASSED = "passed"
    FAILED = "failed"
    NO_RESULT = "no_result"  # Wert und Grenzen 0: seit dem Löschen noch nicht gelaufen


# deutsch; zur Anzeige ``tr(STATUS_LABELS[status])``
STATUS_LABELS = {
    TestStatus.PASSED: N_("bestanden"),
    TestStatus.FAILED: N_("nicht bestanden"),
    TestStatus.NO_RESULT: N_("kein Ergebnis"),
}
_MAX_DECIMALS = 5


@dataclass(frozen=True)
class TestResult:
    """Ein Testergebnis aus Mode 06, roh gespeichert und bei Bedarf umgerechnet."""

    __test__ = False  # kein pytest-Testfall trotz des Namens

    ecu: str  # Absender wie empfangen ("7E8", "18DAF110")
    mid: int
    tid: int
    uasid: int
    value_raw: int  # die zwei Bytes ohne Vorzeichen, wie gesendet
    min_raw: int
    max_raw: int

    def _number(self, raw: int) -> int:
        return raw - 0x10000 if is_signed(self.uasid) and raw >= 0x8000 else raw

    def _scaled(self, raw: int) -> float | None:
        unit = UNITS.get(self.uasid)
        if unit is None:
            return None
        return self._number(raw) * unit.scale + unit.offset

    @property
    def value(self) -> float | None:
        """Umgerechneter Wert; ``None`` bei unbekannter UASID."""
        return self._scaled(self.value_raw)

    @property
    def minimum(self) -> float | None:
        return self._scaled(self.min_raw)

    @property
    def maximum(self) -> float | None:
        return self._scaled(self.max_raw)

    @property
    def unit(self) -> str:
        unit = UNITS.get(self.uasid)
        return unit.unit if unit is not None else ""

    @property
    def decimals(self) -> int:
        """Sinnvolle Nachkommastellen zur Anzeige: so viele, wie die Auflösung hat."""
        unit = UNITS.get(self.uasid)
        if unit is None:
            return 0
        fraction = f"{unit.scale:.10f}".rstrip("0").partition(".")[2]
        return min(len(fraction), _MAX_DECIMALS)

    @property
    def status(self) -> TestStatus:
        """``TestStatus``; verglichen wird mit den Rohwerten samt Vorzeichen, so geht das
        auch bei unbekannter UASID (die Umrechnung ist immer steigend)."""
        if self.value_raw == self.min_raw == self.max_raw == 0:
            return TestStatus.NO_RESULT
        value = self._number(self.value_raw)
        if self._number(self.min_raw) <= value <= self._number(self.max_raw):
            return TestStatus.PASSED
        return TestStatus.FAILED


def parse_supported(message: EcuMessage, base: int) -> tuple[int, ...] | None:
    """MIDs aus ``46 <base> AA BB CC DD``; ``None``, wenn die Nachricht nicht passt."""
    data = message.data
    if len(data) != 6 or data[:2] != bytes([SID, base]):
        return None
    bits = int.from_bytes(data[2:6])
    # das letzte Bit von E0 („weiter“) zeigt auf keinen Bereich mehr: MIDs enden bei FF
    return tuple(base + n for n in range(1, 0x21) if bits & 1 << (32 - n) and base + n <= 0xFF)


def parse_results(message: EcuMessage, mid: int) -> list[TestResult]:
    """Testergebnisse aus ``46`` + Blöcken zu 9 Bytes; ``ValueError``, wenn die Länge
    nicht aufgeht oder ein Block eine andere MID trägt als angefragt."""
    data = message.data
    if data[:1] != bytes([SID]) or len(data) < 1 + RECORD_SIZE or (len(data) - 1) % RECORD_SIZE:
        raise ValueError(
            tr("06{mid:02X}: Antwort passt nicht zum Format: {data}").format(
                mid=mid, data=data.hex(" ").upper()
            )
        )
    results = []
    for i in range(1, len(data), RECORD_SIZE):
        block = data[i : i + RECORD_SIZE]
        if block[0] != mid:
            raise ValueError(
                tr("06{mid:02X}: Ergebnis für MID {other:02X}").format(mid=mid, other=block[0])
            )
        results.append(
            TestResult(
                ecu=message.header,
                mid=mid,
                tid=block[1],
                uasid=block[2],
                value_raw=int.from_bytes(block[3:5]),
                min_raw=int.from_bytes(block[5:7]),
                max_raw=int.from_bytes(block[7:9]),
            )
        )
    return results


def _messages(elm: Elm327, cmd: str) -> list[EcuMessage]:
    """Nachrichten mit Header; bei ``BUFFER FULL`` die, die vorher vollständig ankamen."""
    try:
        try:
            response = elm.query(cmd)
        except BufferFullError as e:
            messages, cut = parse_truncated_header_response(e.partial)
            log.warning(
                "%s: Adapterpuffer voll (BUFFER FULL), Antwort unvollständig von %s",
                cmd,
                ", ".join(cut) or "?",
            )
            return messages
        return parse_header_response(response) if response is not None else []
    except ValueError as e:
        raise ElmError(f"{cmd}: {e}") from e


def _supported_mids(elm: Elm327) -> list[int] | None:
    """Alle von irgendeinem Steuergerät gemeldeten MIDs; ``None`` ohne CAN-Antwort."""
    mids: set[int] = set()
    answered = False
    for base in _RANGES:
        messages = _messages(elm, f"06{base:02X}")
        if any(len(m.header) not in _CAN_HEADER_LENGTHS for m in messages):
            log.info("Mode 06: kein CAN, Testergebnisse werden nicht gelesen")
            return None
        found = [s for m in messages if (s := parse_supported(m, base)) is not None]
        answered = answered or bool(found)
        mids.update(mid for s in found for mid in s)
        if base + 0x20 not in mids:
            break
    return sorted(m for m in mids if m not in _RANGES) if answered else None


def read_test_results(elm: Elm327) -> list[TestResult] | None:
    """Mode 06 aller Steuergeräte, nach Absender, MID und Reihenfolge der Antwort.

    ``None``, wenn kein Steuergerät Mode 06 beantwortet oder das Protokoll kein CAN
    ist; eine leere Liste, wenn MIDs gemeldet sind, aber keine lesbaren Ergebnisse
    kommen. Alles läuft in einem Block mit Headern (``ATH1`` … ``ATH0``), damit jedes
    Ergebnis seinem Steuergerät zugeordnet ist. Lehnt ein Steuergerät eine MID ab
    (``7F 06 xx``) oder ist eine Antwort nicht normgerecht, fehlt nur diese (mit
    Log-Eintrag). ``ElmError``, wenn schon die unterstützten MIDs unlesbar sind.
    """
    results: list[TestResult] = []
    with elm.headers():
        mids = _supported_mids(elm)
        if mids is None:
            return None
        for mid in mids:
            cmd = f"06{mid:02X}"
            try:
                messages = _messages(elm, cmd)
            except ElmError as e:
                log.warning("%s nicht lesbar: %s", cmd, e)
                continue
            for message in messages:
                if message.data[:1] != bytes([SID]):
                    continue  # Ablehnung 7F 06 xx
                try:
                    results.extend(parse_results(message, mid))
                except ValueError as e:
                    log.warning("%s von %s: %s", cmd, message.header, e)
    return sorted(results, key=lambda r: (r.ecu, r.mid))
