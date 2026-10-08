"""Mode-01-PIDs für Live-Daten nach SAE J1979: Tabelle, Dekodierung, unterstützte PIDs.

Nur lesend: gesendet werden ausschließlich ``01xx``-Anfragen über ``Elm327.query``.

Die Formeln folgen SAE J1979 (Anhang B, Service $01) in der Schreibweise der
Wikipedia-Tabelle „OBD-II PIDs“: ``A``, ``B``, ``C``, ``D`` sind die Datenbytes nach
``41 <PID>``. ``minimum``/``maximum`` sind der volle Wertebereich der Kodierung (alle
Bits 0 bzw. 1), nicht der physikalisch plausible Bereich. Die Werte werden nicht
gerundet; das ist Sache der Anzeige.

Bewusst nicht aufgenommen (offen):

- PIDs mit mehreren Werten in einer Antwort: Lambdasonden ``14``-``1B``,
  ``24``-``2B``, ``34``-``3B`` (Spannung/Strom und Trimm bzw. Lambda), ``55``-``58``
  (Sekundärluft-Trimm zweier Bänke), ``64`` (Drehmomentstufen).
- PIDs mit Statusbyte, das angibt, welche der folgenden Werte gültig sind: ``66``
  (Luftmasse A/B), ``67``/``68`` (Kühlmittel-/Ansauglufttemperatur je Sensor), ``69``
  (AGR), ``6A``-``6F``, ``70`` (Ladedruckregelung), ``71``-``7B``, ``7A``-``7C``
  (DPF), ``7F`` und höher. Sauber dekodierbar erst mit einer Auswahl des Teilwerts;
  das passt nicht zu ``PidSpec`` (ein Wert je PID).
- Bitfelder und Aufzählungen ohne Messwert: ``01``, ``03``, ``12``, ``13``, ``1C``,
  ``1D``, ``1E``, ``41``, ``51``, ``65``.
- ``53``/``54`` (Absolut-/Relativdruck Tankentlüftung): Kodierung von ``54`` ist in
  den frei zugänglichen Quellen widersprüchlich (Zweierkomplement oder Offset).
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass

from obd_diag.protocol.elm327 import Elm327
from obd_diag.protocol.frames import split_messages

log = logging.getLogger(__name__)


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


# --- Umrechnungen nach J1979 -----------------------------------------------------


def _word(d: bytes) -> int:
    """256*A + B"""
    return d[0] * 256 + d[1]


def _percent(d: bytes) -> float:
    """100*A/255: 0 bis 100 %"""
    return d[0] * 100 / 255


def _temperature(d: bytes) -> float:
    """A - 40: -40 bis 215 °C"""
    return d[0] - 40


def _trim(d: bytes) -> float:
    """100*A/128 - 100: -100 bis 99,22 % (Kraftstofftrimm, AGR-Abweichung)"""
    return d[0] * 100 / 128 - 100


def _byte(d: bytes) -> float:
    """A"""
    return d[0]


def _word_value(d: bytes) -> float:
    """256*A + B"""
    return _word(d)


def _torque(d: bytes) -> float:
    """A - 125: -125 bis 130 %"""
    return d[0] - 125


_PERCENT = (0.0, 100.0)
_TEMPERATURE = (-40.0, 215.0)
_TRIM = (-100.0, 99.21875)
_BYTE = (0.0, 255.0)
_WORD = (0.0, 65535.0)
_TORQUE = (-125.0, 130.0)


def _spec(
    pid: int,
    key: str,
    name: str,
    unit: str,
    size: int,
    decode: Callable[[bytes], float],
    bounds: tuple[float, float],
) -> PidSpec:
    return PidSpec(pid, key, name, unit, size, decode, bounds[0], bounds[1])


_TABLE = [
    _spec(0x04, "engine_load", "Berechnete Motorlast", "%", 1, _percent, _PERCENT),
    _spec(0x05, "coolant_temp", "Kühlmitteltemperatur", "°C", 1, _temperature, _TEMPERATURE),
    _spec(0x06, "stft_bank1", "Kurzzeit-Kraftstofftrimm Bank 1", "%", 1, _trim, _TRIM),
    _spec(0x07, "ltft_bank1", "Langzeit-Kraftstofftrimm Bank 1", "%", 1, _trim, _TRIM),
    _spec(0x08, "stft_bank2", "Kurzzeit-Kraftstofftrimm Bank 2", "%", 1, _trim, _TRIM),
    _spec(0x09, "ltft_bank2", "Langzeit-Kraftstofftrimm Bank 2", "%", 1, _trim, _TRIM),
    _spec(
        0x0A,
        "fuel_pressure",
        "Kraftstoffdruck (Überdruck)",
        "kPa",
        1,
        lambda d: d[0] * 3,
        (0.0, 765.0),
    ),
    _spec(0x0B, "intake_pressure", "Saugrohrdruck (absolut)", "kPa", 1, _byte, _BYTE),
    _spec(
        0x0C,
        "rpm",
        "Motordrehzahl",
        "1/min",
        2,
        lambda d: _word(d) / 4,
        (0.0, 16383.75),
    ),
    _spec(0x0D, "speed", "Geschwindigkeit", "km/h", 1, _byte, _BYTE),
    _spec(
        0x0E,
        "timing_advance",
        "Zündzeitpunkt (vor OT, Zylinder 1)",
        "°",
        1,
        lambda d: d[0] / 2 - 64,
        (-64.0, 63.5),
    ),
    _spec(0x0F, "intake_temp", "Ansauglufttemperatur", "°C", 1, _temperature, _TEMPERATURE),
    _spec(
        0x10,
        "maf",
        "Luftmasse (Luftmassenmesser)",
        "g/s",
        2,
        lambda d: _word(d) / 100,
        (0.0, 655.35),
    ),
    _spec(0x11, "throttle", "Drosselklappenstellung", "%", 1, _percent, _PERCENT),
    _spec(0x1F, "run_time", "Laufzeit seit Motorstart", "s", 2, _word_value, _WORD),
    _spec(0x21, "distance_with_mil", "Strecke mit Warnleuchte an", "km", 2, _word_value, _WORD),
    _spec(
        0x22,
        "fuel_rail_pressure_relative",
        "Kraftstoffverteilerdruck (relativ zum Saugrohr)",
        "kPa",
        2,
        lambda d: _word(d) * 0.079,
        (0.0, 5177.265),
    ),
    _spec(
        0x23,
        "fuel_rail_pressure",
        "Kraftstoffverteilerdruck (Überdruck)",
        "kPa",
        2,
        lambda d: _word(d) * 10,
        (0.0, 655350.0),
    ),
    _spec(0x2C, "commanded_egr", "AGR-Sollwert", "%", 1, _percent, _PERCENT),
    _spec(0x2D, "egr_error", "AGR-Abweichung", "%", 1, _trim, _TRIM),
    _spec(0x2E, "commanded_evap_purge", "Tankentlüftung Sollwert", "%", 1, _percent, _PERCENT),
    _spec(0x2F, "fuel_level", "Tankfüllstand", "%", 1, _percent, _PERCENT),
    _spec(0x30, "warmups_since_clear", "Warmläufe seit Löschen", "", 1, _byte, _BYTE),
    _spec(0x31, "distance_since_clear", "Strecke seit Löschen", "km", 2, _word_value, _WORD),
    _spec(
        0x32,
        "evap_pressure",
        "Dampfdruck Tankentlüftung",
        "Pa",
        2,
        lambda d: int.from_bytes(d[:2], "big", signed=True) / 4,
        (-8192.0, 8191.75),
    ),
    _spec(0x33, "baro_pressure", "Luftdruck (absolut)", "kPa", 1, _byte, _BYTE),
    *(
        _spec(
            pid,
            f"catalyst_temp_{sensor}",
            f"Katalysatortemperatur {label}",
            "°C",
            2,
            lambda d: _word(d) / 10 - 40,
            (-40.0, 6513.5),
        )
        for pid, sensor, label in (
            (0x3C, "b1s1", "Bank 1, Sensor 1"),
            (0x3D, "b2s1", "Bank 2, Sensor 1"),
            (0x3E, "b1s2", "Bank 1, Sensor 2"),
            (0x3F, "b2s2", "Bank 2, Sensor 2"),
        )
    ),
    _spec(
        0x42,
        "control_voltage",
        "Steuergerätespannung",
        "V",
        2,
        lambda d: _word(d) / 1000,
        (0.0, 65.535),
    ),
    _spec(
        0x43,
        "absolute_load",
        "Absolute Motorlast",
        "%",
        2,
        lambda d: _word(d) * 100 / 255,
        (0.0, 25700.0),
    ),
    _spec(
        0x44,
        "commanded_lambda",
        "Lambda-Sollwert",
        "Lambda",
        2,
        lambda d: _word(d) * 2 / 65536,
        (0.0, 65535 * 2 / 65536),
    ),
    _spec(0x45, "relative_throttle", "Relative Drosselklappenstellung", "%", 1, _percent, _PERCENT),
    _spec(0x46, "ambient_temp", "Umgebungstemperatur", "°C", 1, _temperature, _TEMPERATURE),
    _spec(0x47, "throttle_b", "Drosselklappenstellung B (absolut)", "%", 1, _percent, _PERCENT),
    _spec(0x49, "accelerator_pedal_d", "Fahrpedalstellung D", "%", 1, _percent, _PERCENT),
    _spec(0x4A, "accelerator_pedal_e", "Fahrpedalstellung E", "%", 1, _percent, _PERCENT),
    _spec(0x4C, "commanded_throttle", "Drosselklappensteller Sollwert", "%", 1, _percent, _PERCENT),
    _spec(0x4D, "time_with_mil", "Zeit mit Warnleuchte an", "min", 2, _word_value, _WORD),
    _spec(0x4E, "time_since_clear", "Zeit seit Löschen", "min", 2, _word_value, _WORD),
    _spec(0x52, "ethanol_percent", "Ethanolanteil im Kraftstoff", "%", 1, _percent, _PERCENT),
    _spec(
        0x5A,
        "relative_accelerator_pedal",
        "Relative Fahrpedalstellung",
        "%",
        1,
        _percent,
        _PERCENT,
    ),
    _spec(0x5B, "hybrid_battery_level", "Ladezustand Hybridbatterie", "%", 1, _percent, _PERCENT),
    _spec(0x5C, "oil_temp", "Motoröltemperatur", "°C", 1, _temperature, _TEMPERATURE),
    _spec(
        0x5D,
        "injection_timing",
        "Einspritzzeitpunkt",
        "°",
        2,
        lambda d: _word(d) / 128 - 210,
        (-210.0, 65535 / 128 - 210),
    ),
    _spec(
        0x5E,
        "fuel_rate",
        "Kraftstoffverbrauch",
        "L/h",
        2,
        lambda d: _word(d) / 20,
        (0.0, 3276.75),
    ),
    _spec(0x61, "demand_torque", "Fahrerwunsch-Drehmoment", "%", 1, _torque, _TORQUE),
    _spec(0x62, "actual_torque", "Ist-Drehmoment", "%", 1, _torque, _TORQUE),
    _spec(0x63, "reference_torque", "Bezugsdrehmoment des Motors", "Nm", 2, _word_value, _WORD),
    _spec(
        0xA6,
        "odometer",
        "Kilometerstand",
        "km",
        4,
        lambda d: int.from_bytes(d[:4], "big") / 10,
        (0.0, 0xFFFFFFFF / 10),
    ),
]

# Alle unterstützten Live-Werte, nach PID.
PIDS: dict[int, PidSpec] = {spec.pid: spec for spec in _TABLE}

_BY_KEY: dict[str, PidSpec] = {spec.key: spec for spec in _TABLE}


def pid_by_key(key: str) -> PidSpec:
    """Die PID zu ``key``; ``KeyError`` mit verständlicher Meldung, wenn unbekannt."""
    try:
        return _BY_KEY[key]
    except KeyError:
        known = ", ".join(sorted(_BY_KEY))
        raise KeyError(f"unbekannter Live-Wert {key!r}; bekannt sind: {known}") from None


# --- unterstützte PIDs (Bitmasken 0100, 0120, …) -----------------------------------

_LAST_BASE = 0xC0  # höchster abgefragter Block: 01C0 (PIDs C1-E0)


def parse_supported(base: int, data: bytes) -> set[int]:
    """PIDs aus der Bitmaske einer Antwort auf ``01<base>`` (base = 0x00, 0x20, …).

    ``data`` sind die vier Datenbytes; Bit 7 des ersten Bytes steht für ``base + 1``,
    Bit 0 des vierten für ``base + 0x20`` (der nächste Block wird unterstützt).
    Weitere Bytes werden ignoriert. ``ValueError`` bei weniger als vier Bytes oder
    einer ``base``, die kein Vielfaches von 0x20 zwischen 0x00 und 0xE0 ist.
    """
    if base % 0x20 or not 0 <= base <= 0xE0:
        raise ValueError(f"keine Basis für unterstützte PIDs: {base:#04x}")
    if len(data) < 4:
        raise ValueError(f"Bitmaske braucht vier Datenbytes, nicht {len(data)}")
    mask = int.from_bytes(data[:4], "big")
    return {base + i + 1 for i in range(32) if mask & (0x80000000 >> i)}


def _messages(cmd: str, response: str) -> list[bytes]:
    """Nachrichten der Antwort; Zeilen ohne Hex-Daten werden einzeln übersprungen."""
    try:
        return split_messages(response)
    except ValueError as e:
        log.info("%s: %s; lese Zeile für Zeile", cmd, e)
    messages: list[bytes] = []
    for line in response.splitlines():
        try:
            messages.extend(split_messages(line))
        except ValueError:
            log.info("%s: Zeile %r übersprungen", cmd, line)
    return messages


def read_supported_pids(elm: Elm327) -> set[int]:
    """Alle PIDs, die mindestens ein Steuergerät unterstützt (Vereinigung).

    Fragt ``0100`` und folgt der Kette (``0120``, ``0140`` …), solange ein Steuergerät
    das Bit für den nächsten Block setzt, höchstens bis ``01C0``. ``NO DATA`` ergibt
    eine leere Menge (bzw. beendet die Kette). Jede Nachricht zählt einzeln; Antworten
    ohne ``41 <base>`` oder mit weniger als vier Datenbytes (z. B. ``7F 01 12``)
    werden ignoriert. Die Block-PIDs selbst (0x20, 0x40, …) sind im Ergebnis enthalten.
    ``ElmError`` bei Adapterfehlern geht an den Aufrufer.
    """
    supported: set[int] = set()
    base = 0x00
    while True:
        cmd = f"01{base:02X}"
        response = elm.query(cmd)
        block: set[int] = set()
        if response is not None:
            for message in _messages(cmd, response):
                if len(message) >= 6 and message[0] == 0x41 and message[1] == base:
                    block |= parse_supported(base, message[2:6])
        supported |= block
        following = base + 0x20
        if following > _LAST_BASE or following not in block:
            return supported
        base = following


def read_value(elm: Elm327, spec: PidSpec) -> float | None:
    """Ein Live-Wert vom ersten Steuergerät, das ``spec.pid`` gültig beantwortet.

    Gültig ist eine Nachricht ``41 <pid>`` mit mindestens ``spec.size`` Datenbytes
    (überzählige werden ignoriert). ``None`` bei ``NO DATA``, Ablehnung
    (``7F 01 xx``) oder zu kurzer Antwort; ``ElmError`` bei Adapterfehlern geht an
    den Aufrufer.
    """
    cmd = f"01{spec.pid:02X}"
    response = elm.query(cmd)
    if response is None:
        return None
    for message in _messages(cmd, response):
        if len(message) >= 2 + spec.size and message[0] == 0x41 and message[1] == spec.pid:
            return spec.decode(message[2 : 2 + spec.size])
    return None
