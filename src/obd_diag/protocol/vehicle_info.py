"""Mode 09 (Fahrzeuginformationen) je Steuergerät: Name, Kalibrierungs-IDs, CVN und
Überwachungszähler (In-Use Performance Tracking). Die FIN (InfoType 02) liest
``services.vehicle.read_vin``.

Aufbau der Antworten nach SAE J1979 / ISO 15031-5 (Übersicht in Wikipedia „OBD-II PIDs“,
Service 09, abgerufen 2026-10-11; das Beispiel ``09 04`` im Datenblatt ELM327DSJ S. 43):

- ``49 00 AA BB CC DD``: unterstützte InfoTypes 01 bis 20, Bit 7 von A = 01.
- ``49 04 NN`` + NN Kalibrierungs-IDs (CALID) zu je 16 ASCII-Zeichen, hinten mit 00
  aufgefüllt.
- ``49 06 NN`` + NN Kalibrierungs-Prüfnummern (CVN) zu je 4 Bytes, als Hex angezeigt.
- ``49 08 NN`` bzw. ``49 0B NN`` + NN Zähler zu je 2 Bytes (Otto- bzw. Dieselmotor):
  OBDCOND, IGNCNTR, dann je Monitor ein Paar „Test abgeschlossen“ / „Bedingungen
  erfüllt“ (``SPARK_COUNTERS``, ``COMPRESSION_COUNTERS``).
- ``49 0A 01`` + 20 ASCII-Zeichen Steuergerätename (``ECM`` + 00, ``-``, Langname).

NN ist bei CAN die Zahl der Datenelemente (ISO 15765-4). Bei den älteren Protokollen
(J1850, ISO 9141, KWP) steht an dieser Stelle eine laufende Nummer, und jede Nachricht
trägt 4 Datenbytes; die Daten werden dann nach Nummer aneinandergehängt.

Weil mehrere Steuergeräte antworten und ohne Header die Reihenfolge der Antworten
wechselt (beim W177 bei jeder Anfrage anders), liest ``read_ecu_info`` alles mit Headern
(``ATH1``) und ordnet nach Absender.
"""

import logging
from dataclasses import dataclass

from obd_diag.i18n import N_, tr
from obd_diag.protocol.elm327 import BufferFullError, Elm327, ElmError
from obd_diag.protocol.headers import (
    EcuMessage,
    parse_header_response,
    parse_truncated_header_response,
)

log = logging.getLogger(__name__)

SID = 0x49  # positive Antwort auf Service $09

INFO_SUPPORTED = 0x00
INFO_CALIBRATION_ID = 0x04
INFO_CVN = 0x06
INFO_IPT_SPARK = 0x08
INFO_ECU_NAME = 0x0A
INFO_IPT_COMPRESSION = 0x0B
# Was ``read_ecu_info`` abfragt, sofern ein Steuergerät es als unterstützt meldet
READ_INFO_TYPES = (
    INFO_CALIBRATION_ID,
    INFO_CVN,
    INFO_IPT_SPARK,
    INFO_ECU_NAME,
    INFO_IPT_COMPRESSION,
)

CALID_SIZE = 16
CVN_SIZE = 4
COUNTER_SIZE = 2
ECU_NAME_SIZE = 20
_LEGACY_DATA = 4  # Datenbytes je Nachricht bei J1850/ISO 9141/KWP
_CAN_HEADER_LENGTHS = (3, 8)  # "7E8" bzw. "18DAF110"; Legacy: 6 ("486B10")

# Überwachungszähler nach J1979: Schlüssel und Bezeichnung je Paar (abgeschlossen,
# Bedingungen erfüllt), in der Reihenfolge der Antwort nach OBDCOND und IGNCNTR.
SPARK_COUNTERS: tuple[tuple[str, str], ...] = (
    ("catalyst_b1", N_("Katalysator Bank 1")),
    ("catalyst_b2", N_("Katalysator Bank 2")),
    ("o2_sensor_b1", N_("Lambdasonde Bank 1")),
    ("o2_sensor_b2", N_("Lambdasonde Bank 2")),
    ("egr", N_("Abgasrückführung (AGR) / VVT")),
    ("secondary_air", N_("Sekundärluft")),
    ("evap", N_("Tankentlüftung (EVAP)")),
    ("secondary_o2_b1", N_("Lambdasonde nach Kat Bank 1")),
    ("secondary_o2_b2", N_("Lambdasonde nach Kat Bank 2")),
)
COMPRESSION_COUNTERS: tuple[tuple[str, str], ...] = (
    ("nmhc_catalyst", N_("NMHC-Katalysator")),
    ("nox_catalyst", N_("NOx-/SCR-Katalysator")),
    ("nox_adsorber", N_("NOx-Speicherkatalysator")),
    ("pm_filter", N_("Partikelfilter")),
    ("exhaust_gas_sensor", N_("Abgassensor")),
    ("egr", N_("Abgasrückführung (AGR) / VVT")),
    ("boost_pressure", N_("Ladedruck")),
    ("fuel", N_("Kraftstoffsystem")),
)
COUNTER_NAMES = dict(SPARK_COUNTERS) | dict(COMPRESSION_COUNTERS)


@dataclass(frozen=True)
class PerformanceCounter:
    """Ein Monitor im In-Use Performance Tracking: wie oft der Test abgeschlossen wurde
    und wie oft die Fahrbedingungen dafür gegeben waren."""

    key: str  # Schlüssel aus ``SPARK_COUNTERS``/``COMPRESSION_COUNTERS``
    completed: int
    conditions: int

    @property
    def name(self) -> str:
        """Deutsche Bezeichnung (msgid); zur Anzeige ``tr(counter.name)``."""
        return COUNTER_NAMES.get(self.key, self.key)

    @property
    def ratio(self) -> float | None:
        """Überwachungsquote abgeschlossen / Bedingungen; ``None`` ohne Bedingungen."""
        return self.completed / self.conditions if self.conditions else None


@dataclass(frozen=True)
class InUsePerformance:
    compression_ignition: bool  # InfoType 0B (Diesel) statt 08 (Otto)
    obd_conditions: int  # OBDCOND: Fahrzyklen mit erfüllten allgemeinen Bedingungen
    ignition_cycles: int  # IGNCNTR: Zündungszyklen
    counters: tuple[PerformanceCounter, ...] = ()


@dataclass(frozen=True)
class EcuInfo:
    """Was ein Steuergerät über Mode 09 meldet; nicht gemeldete Angaben bleiben leer."""

    ecu: str  # Absender wie empfangen: "7E8" (CAN 11 Bit), "18DAF110", "486B10"
    name: str | None = None
    calibration_ids: tuple[str, ...] = ()
    cvns: tuple[str, ...] = ()
    performance: InUsePerformance | None = None
    supported: tuple[int, ...] = ()  # gemeldete InfoTypes (aus 0900)


def _is_can(message: EcuMessage) -> bool:
    return len(message.header) in _CAN_HEADER_LENGTHS


def parse_supported(message: EcuMessage) -> tuple[int, ...] | None:
    """InfoTypes aus ``49 00 AA BB CC DD`` (Legacy mit Nummer davor); sonst ``None``."""
    data = message.data
    if data[:2] != bytes([SID, INFO_SUPPORTED]):
        return None
    if not _is_can(message) and len(data) == 3 + _LEGACY_DATA:
        data = data[:2] + data[3:]
    if len(data) != 6:
        return None
    bits = int.from_bytes(data[2:6])
    return tuple(n for n in range(1, 0x21) if bits & 1 << (32 - n))


def _payload(info_type: int, messages: list[EcuMessage]) -> bytes:
    """Datenbytes eines Steuergeräts zu ``info_type`` ohne Kopf; ``ValueError`` sonst.

    ``messages``: alle Nachrichten dieses Steuergeräts zu dieser Anfrage. Bei CAN genau
    eine (``49 <Typ> NN …``); bei älteren Protokollen eine je 4 Datenbytes mit
    laufender Nummer, die hier in Reihenfolge gebracht werden.
    """
    head = bytes([SID, info_type])
    ours = [m for m in messages if m.data[:2] == head]
    if not ours:
        raise ValueError(tr("keine Antwort 49 {type:02X}").format(type=info_type))
    if _is_can(ours[0]):
        if len(ours) > 1:
            raise ValueError(
                tr("mehrere Antworten 49 {type:02X} vom selben Steuergerät").format(type=info_type)
            )
        return ours[0].data[2:]
    parts: dict[int, bytes] = {}
    for m in ours:
        if len(m.data) != 3 + _LEGACY_DATA or m.data[2] in parts:
            raise ValueError(
                tr("Nachricht 49 {type:02X} passt nicht zum Format: {data}").format(
                    type=info_type, data=m.data.hex(" ").upper()
                )
            )
        parts[m.data[2]] = m.data[3:]
    if sorted(parts) != list(range(1, len(parts) + 1)):
        raise ValueError(tr("Nachrichten 49 {type:02X} unvollständig").format(type=info_type))
    return b"".join(parts[n] for n in sorted(parts))


def _items(info_type: int, messages: list[EcuMessage], size: int) -> list[bytes]:
    """Die Datenelemente zu je ``size`` Bytes; bei CAN laut Anzahl NN geprüft."""
    payload = _payload(info_type, messages)
    if _is_can(messages[0]):
        if not payload:
            raise ValueError(tr("Antwort 49 {type:02X} ohne Anzahl").format(type=info_type))
        count, payload = payload[0], payload[1:]
        if len(payload) != count * size:
            raise ValueError(
                tr(
                    "49 {type:02X}: {count} Elemente zu {size} Bytes angekündigt, {got} Bytes"
                ).format(type=info_type, count=count, size=size, got=len(payload))
            )
    elif len(payload) % size:
        raise ValueError(
            tr("49 {type:02X}: {got} Bytes sind kein Vielfaches von {size}").format(
                type=info_type, got=len(payload), size=size
            )
        )
    return [payload[i : i + size] for i in range(0, len(payload), size)]


def _text(raw: bytes) -> str:
    """ASCII ohne Füllbytes; nicht druckbare Zeichen als ``?``."""
    chars = (chr(b) if 0x20 <= b < 0x7F else "?" for b in raw if b != 0)
    return "".join(chars).strip()


def parse_calibration_ids(messages: list[EcuMessage]) -> tuple[str, ...]:
    """InfoType 04: Kalibrierungs-IDs eines Steuergeräts (leere übersprungen)."""
    texts = (_text(item) for item in _items(INFO_CALIBRATION_ID, messages, CALID_SIZE))
    return tuple(t for t in texts if t)


def parse_cvns(messages: list[EcuMessage]) -> tuple[str, ...]:
    """InfoType 06: Kalibrierungs-Prüfnummern als 8 Hex-Ziffern (z. B. ``1A2B3C4D``)."""
    return tuple(item.hex().upper() for item in _items(INFO_CVN, messages, CVN_SIZE))


def parse_ecu_name(messages: list[EcuMessage]) -> str | None:
    """InfoType 0A: z. B. ``ECM-EngineControl``; ``None`` bei leerem Namen."""
    items = _items(INFO_ECU_NAME, messages, ECU_NAME_SIZE)
    if len(items) != 1:
        raise ValueError(tr("49 0A: {count} Namen statt einem").format(count=len(items)))
    return _text(items[0]) or None


def parse_performance(info_type: int, messages: list[EcuMessage]) -> InUsePerformance:
    """InfoType 08 (Otto) oder 0B (Diesel): Überwachungszähler.

    Gelesen werden OBDCOND, IGNCNTR und so viele Paare, wie gemeldet sind (J1979:
    16, 18 oder 20 Werte, ältere Steuergeräte weniger); mehr Paare als bekannt oder eine
    ungerade Zahl von Werten sind ein ``ValueError``.
    """
    compression = info_type == INFO_IPT_COMPRESSION
    table = COMPRESSION_COUNTERS if compression else SPARK_COUNTERS
    values = [int.from_bytes(item) for item in _items(info_type, messages, COUNTER_SIZE)]
    if len(values) < 2 or len(values) % 2 or (len(values) - 2) // 2 > len(table):
        raise ValueError(
            tr("49 {type:02X}: {count} Zählerwerte passen nicht zu J1979").format(
                type=info_type, count=len(values)
            )
        )
    counters = tuple(
        PerformanceCounter(key, values[2 + 2 * i], values[3 + 2 * i])
        for i, (key, _) in enumerate(table[: (len(values) - 2) // 2])
    )
    return InUsePerformance(compression, values[0], values[1], counters)


def _by_ecu(messages: list[EcuMessage]) -> dict[str, list[EcuMessage]]:
    grouped: dict[str, list[EcuMessage]] = {}
    for message in messages:
        grouped.setdefault(message.header, []).append(message)
    return grouped


def _query(elm: Elm327, cmd: str) -> dict[str, list[EcuMessage]]:
    """Antworten je Absender; ``ElmError``, wenn der Adapter ablehnt oder sie unlesbar sind."""
    return _query_once(elm, cmd)[0]


def _query_once(elm: Elm327, cmd: str) -> tuple[dict[str, list[EcuMessage]], bool]:
    """Wie ``_query``, dazu ob der Adapterpuffer übergelaufen ist (``BUFFER FULL``).

    Dann zählen die Nachrichten, die vorher vollständig ankamen; wessen Antwort
    abgeschnitten ist, der fehlt (Log-Eintrag).
    """
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
            return _by_ecu(messages), True
        messages = parse_header_response(response) if response is not None else []
        return _by_ecu(messages), False
    except ValueError as e:
        raise ElmError(f"{cmd}: {e}") from e


def _query_all(elm: Elm327, cmd: str, expected: set[str]) -> dict[str, list[EcuMessage]]:
    """Wie ``_query``; lief der Puffer über und fehlen Steuergeräte aus ``expected``,
    wird die Anfrage einmal wiederholt.

    Am W177 läuft bei ``0904`` und ``090A`` der Puffer des Adapters über, weil vier
    Steuergeräte gleichzeitig mehrteilig antworten. Die Reihenfolge wechselt bei jeder
    Anfrage, daher kommt beim zweiten Mal oft durch, was beim ersten fehlte; die
    Antworten werden je Steuergerät zusammengeführt (die erste vollständige gilt).
    """
    answers, overflow = _query_once(elm, cmd)
    missing = expected - answers.keys()
    if not overflow or not missing:
        return answers
    try:
        again = _query(elm, cmd)
    except ElmError as e:
        log.warning("%s (Wiederholung) nicht lesbar: %s", cmd, e)
        return answers
    return answers | {ecu: again[ecu] for ecu in sorted(missing) if ecu in again}


def read_ecu_info(elm: Elm327) -> list[EcuInfo] | None:
    """Mode 09 je Steuergerät, sortiert nach Adresse; ``None`` ohne Mode-09-Antwort.

    Fragt ``0900`` und dann jeden InfoType aus ``READ_INFO_TYPES``, den mindestens ein
    Steuergerät als unterstützt meldet, alles in einem Block mit Headern (``ATH1`` …
    ``ATH0``). Lehnt ein Steuergerät ab oder ist seine Antwort nicht normgerecht, fehlt
    nur diese Angabe (mit Log-Eintrag). Bei ``BUFFER FULL`` bleibt, was vollständig ankam,
    und die Anfrage wird einmal wiederholt (``_query_all``). ``ElmError``, wenn schon
    ``0900`` unlesbar ist; ``TransportError`` bricht ab.
    """
    with elm.headers():
        supported: dict[str, tuple[int, ...]] = {}
        for ecu, messages in _query(elm, "0900").items():
            for message in messages:
                found = parse_supported(message)
                if found is not None:
                    supported[ecu] = found
        if not supported:
            return None
        wanted = [t for t in READ_INFO_TYPES if any(t in s for s in supported.values())]
        answers: dict[int, dict[str, list[EcuMessage]]] = {}
        for info_type in wanted:
            cmd = f"09{info_type:02X}"
            expected = {ecu for ecu, types in supported.items() if info_type in types}
            try:
                answers[info_type] = _query_all(elm, cmd, expected)
            except ElmError as e:
                log.warning("%s nicht lesbar: %s", cmd, e)
    return [_ecu_info(ecu, supported[ecu], answers) for ecu in sorted(supported)]


def _ecu_info(
    ecu: str, supported: tuple[int, ...], answers: dict[int, dict[str, list[EcuMessage]]]
) -> EcuInfo:
    name: str | None = None
    calids: tuple[str, ...] = ()
    cvns: tuple[str, ...] = ()
    performance: InUsePerformance | None = None
    for info_type, by_ecu in answers.items():
        messages = by_ecu.get(ecu)
        if not messages or not any(m.data[:1] == bytes([SID]) for m in messages):
            continue  # keine oder nur ablehnende Antwort (7F 09 xx)
        try:
            if info_type == INFO_CALIBRATION_ID:
                calids = parse_calibration_ids(messages)
            elif info_type == INFO_CVN:
                cvns = parse_cvns(messages)
            elif info_type == INFO_ECU_NAME:
                name = parse_ecu_name(messages)
            elif performance is None:
                performance = parse_performance(info_type, messages)
        except ValueError as e:
            log.warning("09%02X von %s: %s", info_type, ecu, e)
    return EcuInfo(ecu, name, calids, cvns, performance, supported)
