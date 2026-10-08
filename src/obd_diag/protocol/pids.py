"""Mode-01-PIDs für Live-Daten nach SAE J1979: Tabelle, Dekodierung, unterstützte PIDs.

Nur lesend: gesendet werden ausschließlich ``01xx``-Anfragen über ``Elm327.query``.

Die Formeln folgen SAE J1979 (Anhang B, Service $01) in der Schreibweise der
Wikipedia-Tabelle „OBD-II PIDs“: ``A``, ``B``, ``C``, ``D`` sind die Datenbytes nach
``41 <PID>``. ``minimum``/``maximum`` sind der volle Wertebereich der Kodierung (alle
Bits 0 bzw. 1), nicht der physikalisch plausible Bereich. Die Werte werden nicht
gerundet; das ist Sache der Anzeige.

Eine PID kann mehrere Werte liefern (z. B. Lambdasonde: Spannung und Trimm); dann gibt
es je Wert eine ``PidSpec`` mit derselben PID, und ``read_values`` fragt die PID nur
einmal ab. ``decode`` bekommt immer die ersten ``size`` Datenbytes (also auch ein
vorangehendes Statusbyte) und liefert ``None``, wenn der Wert laut Antwort nicht
gilt (Statusbit nicht gesetzt, Sonde nicht im Trimm).

Die Lambdasonden ``14``-``1B``, ``24``-``2B`` und ``34``-``3B`` heißen hier Sonde 1
bis 8 in der Reihenfolge der PIDs. Welche Bank und Position das ist, legt das
Fahrzeug über PID ``13`` (2 Bänke zu je 4) oder ``1D`` (4 Bänke zu je 2) fest;
eine feste Zuordnung wie „B1S1“ wäre bei ``1D`` falsch.

Bewusst nicht aufgenommen (offen):

- PIDs mit Statusbyte, deren Aufbau sich zwischen Ausgaben von J1979 geändert hat
  oder in den frei zugänglichen Quellen nicht eindeutig ist: ``68``
  (Ansauglufttemperatur je Sensor: 2 oder 6 Sensoren), ``69`` (AGR), ``6A``-``6F``,
  ``70`` (Ladedruckregelung), ``71``-``79``, ``7A``-``7C`` (DPF), ``7F`` und höher.
- Bitfelder und Aufzählungen ohne Messwert: ``01``, ``03``, ``12``, ``13``, ``1C``,
  ``1D``, ``1E``, ``41``, ``51``, ``65``.
- ``53``/``54`` (Absolut-/Relativdruck Tankentlüftung): Kodierung von ``54`` ist in
  den frei zugänglichen Quellen widersprüchlich (Zweierkomplement oder Offset).
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from obd_diag.protocol.elm327 import Elm327
from obd_diag.protocol.frames import split_messages

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PidSpec:
    """Ein Live-Wert: PID, Schlüssel, deutscher Name, Einheit, Dekodierung."""

    pid: int  # z. B. 0x0C; mehrere Werte können dieselbe PID haben
    key: str  # stabiler Schlüssel für CSV, CLI und GUI, z. B. "rpm"
    name: str  # deutsch, z. B. "Motordrehzahl"
    unit: str  # z. B. "1/min", "km/h", "°C", "%", "kPa", "g/s", "V", "km"
    size: int  # nötige Datenbytes nach ``41 <pid>`` (vom ersten an gezählt)
    # bekommt genau ``size`` Bytes; None: Wert laut Antwort nicht gültig
    decode: Callable[[bytes], float | None]
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
    decode: Callable[[bytes], float | None],
    bounds: tuple[float, float],
) -> PidSpec:
    return PidSpec(pid, key, name, unit, size, decode, bounds[0], bounds[1])


_LAMBDA = (0.0, 65535 * 2 / 65536)


def _lambda(d: bytes) -> float:
    """2/65536 * (256A + B): Luftverhältnis Lambda, 0 bis knapp 2"""
    return _word(d) * 2 / 65536


def _o2_trim(d: bytes) -> float | None:
    """100/128 * B - 100; B = FF: Sonde geht nicht in den Kraftstofftrimm ein"""
    return None if d[1] == 0xFF else _trim(d[1:2])


def _wide_voltage(d: bytes) -> float:
    """8/65536 * (256C + D): Spannung der Breitbandsonde, 0 bis knapp 8 V"""
    return _word(d[2:4]) * 8 / 65536


def _wide_current(d: bytes) -> float:
    """(256C + D)/256 - 128: Pumpstrom der Breitbandsonde, -128 bis knapp 128 mA"""
    return _word(d[2:4]) / 256 - 128


def _at(index: int, decode: Callable[[bytes], float]) -> Callable[[bytes], float]:
    """Ein-Byte-Formel auf Byte ``index`` (0 = A) anwenden."""
    return lambda d: decode(d[index : index + 1])


def _if_supported(bit: int, decode: Callable[[bytes], float]) -> Callable[[bytes], float | None]:
    """Wert nur, wenn im Statusbyte A das Bit ``bit`` gesetzt ist (Sensor vorhanden)."""
    return lambda d: decode(d) if d[0] & (1 << bit) else None


def _oxygen_sensors() -> list[PidSpec]:
    """Lambdasonden 1-8: Schmalband (14-1B), Breitband mit Spannung (24-2B) bzw. Strom
    (34-3B). Je Sonde meldet ein Fahrzeug höchstens eine dieser drei Arten."""
    specs: list[PidSpec] = []
    for n in range(1, 9):
        specs += [
            # B: Kurzzeit-Kraftstofftrimm, den diese Sonde regelt
            _spec(
                0x13 + n,
                f"o2_s{n}_voltage",
                f"Lambdasonde {n} Spannung",
                "V",
                1,
                lambda d: d[0] / 200,
                (0.0, 1.275),
            ),
            _spec(
                0x13 + n,
                f"o2_s{n}_trim",
                f"Lambdasonde {n} Trimm",
                "%",
                2,
                _o2_trim,
                (-100.0, 98.4375),
            ),
        ]
    for n in range(1, 9):
        specs += [
            _spec(
                0x23 + n, f"o2_s{n}_lambda", f"Breitbandsonde {n}", "Lambda", 2, _lambda, _LAMBDA
            ),
            _spec(
                0x23 + n,
                f"o2_s{n}_wide_voltage",
                f"Breitbandsonde {n} Spannung",
                "V",
                4,
                _wide_voltage,
                (0.0, 65535 * 8 / 65536),
            ),
        ]
    for n in range(1, 9):
        specs += [
            _spec(
                0x33 + n,
                f"o2_s{n}_lambda_current",
                f"Breitbandsonde {n} (Strom)",
                "Lambda",
                2,
                _lambda,
                _LAMBDA,
            ),
            _spec(
                0x33 + n,
                f"o2_s{n}_current",
                f"Breitbandsonde {n} Pumpstrom",
                "mA",
                4,
                _wide_current,
                (-128.0, 65535 / 256 - 128),
            ),
        ]
    return specs


def _secondary_trims() -> list[PidSpec]:
    """55-58: Kraftstofftrimm über die Sonden hinter dem Katalysator (Nachkat), A und B
    für zwei Bänke."""
    specs: list[PidSpec] = []
    for pid, term, key, banks in (
        (0x55, "Kurzzeit", "stft", (1, 3)),
        (0x56, "Langzeit", "ltft", (1, 3)),
        (0x57, "Kurzzeit", "stft", (2, 4)),
        (0x58, "Langzeit", "ltft", (2, 4)),
    ):
        for index, bank in enumerate(banks):
            specs.append(
                _spec(
                    pid,
                    f"{key}_secondary_bank{bank}",
                    f"{term}trimm Nachkat Bank {bank}",
                    "%",
                    index + 1,
                    _at(index, _trim),
                    _TRIM,
                )
            )
    return specs


def _torque_points() -> list[PidSpec]:
    """64: Motordrehmoment im Leerlauf und an vier Stützpunkten (je A - 125 %)."""
    labels = ["Leerlauf", *(f"Stützpunkt {n}" for n in range(1, 5))]
    keys = ["torque_idle", *(f"torque_point{n}" for n in range(1, 5))]
    return [
        _spec(
            0x64,
            key,
            f"Motordrehmoment {label}",
            "%",
            i + 1,
            _at(i, _torque),
            _TORQUE,
        )
        for i, (key, label) in enumerate(zip(keys, labels, strict=True))
    ]


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
    *_oxygen_sensors(),
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
    _spec(0x44, "commanded_lambda", "Lambda-Sollwert", "Lambda", 2, _lambda, _LAMBDA),
    _spec(0x45, "relative_throttle", "Relative Drosselklappenstellung", "%", 1, _percent, _PERCENT),
    _spec(0x46, "ambient_temp", "Umgebungstemperatur", "°C", 1, _temperature, _TEMPERATURE),
    _spec(0x47, "throttle_b", "Drosselklappenstellung B (absolut)", "%", 1, _percent, _PERCENT),
    _spec(0x49, "accelerator_pedal_d", "Fahrpedalstellung D", "%", 1, _percent, _PERCENT),
    _spec(0x4A, "accelerator_pedal_e", "Fahrpedalstellung E", "%", 1, _percent, _PERCENT),
    _spec(0x4C, "commanded_throttle", "Drosselklappensteller Sollwert", "%", 1, _percent, _PERCENT),
    _spec(0x4D, "time_with_mil", "Zeit mit Warnleuchte an", "min", 2, _word_value, _WORD),
    _spec(0x4E, "time_since_clear", "Zeit seit Löschen", "min", 2, _word_value, _WORD),
    _spec(0x52, "ethanol_percent", "Ethanolanteil im Kraftstoff", "%", 1, _percent, _PERCENT),
    *_secondary_trims(),
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
    *_torque_points(),
    # 66/67: Statusbyte A sagt, welche Sensoren es gibt (Bit 0: A bzw. 1, Bit 1: B bzw. 2)
    _spec(
        0x66,
        "maf_a",
        "Luftmasse Sensor A",
        "g/s",
        3,
        _if_supported(0, lambda d: _word(d[1:3]) / 32),
        (0.0, 65535 / 32),
    ),
    _spec(
        0x66,
        "maf_b",
        "Luftmasse Sensor B",
        "g/s",
        5,
        _if_supported(1, lambda d: _word(d[3:5]) / 32),
        (0.0, 65535 / 32),
    ),
    _spec(
        0x67,
        "coolant_temp_1",
        "Kühlmitteltemperatur Sensor 1",
        "°C",
        2,
        _if_supported(0, lambda d: d[1] - 40),
        _TEMPERATURE,
    ),
    _spec(
        0x67,
        "coolant_temp_2",
        "Kühlmitteltemperatur Sensor 2",
        "°C",
        3,
        _if_supported(1, lambda d: d[2] - 40),
        _TEMPERATURE,
    ),
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

# Alle bekannten Live-Werte nach Schlüssel, in der Reihenfolge der PIDs.
PIDS: dict[str, PidSpec] = {spec.key: spec for spec in sorted(_TABLE, key=lambda s: s.pid)}


def pid_by_key(key: str) -> PidSpec:
    """Der Wert zu ``key``; ``KeyError`` mit verständlicher Meldung, wenn unbekannt."""
    try:
        return PIDS[key]
    except KeyError:
        known = ", ".join(sorted(PIDS))
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


def read_values(elm: Elm327, specs: Sequence[PidSpec]) -> dict[str, float | None]:
    """Werte derselben PID mit einer Anfrage, je Schlüssel.

    Jeder Wert kommt vom ersten Steuergerät, das ihn gültig meldet: eine Nachricht
    ``41 <pid>`` mit mindestens ``spec.size`` Datenbytes (überzählige werden
    ignoriert), deren Dekodierung nicht ``None`` ist. ``None`` bei ``NO DATA``,
    Ablehnung (``7F 01 xx``), zu kurzer Antwort oder ungültigem Wert; ``ElmError``
    bei Adapterfehlern geht an den Aufrufer. Verschiedene PIDs in ``specs`` sind ein
    Programmierfehler (``ValueError``), ebenso eine leere Liste.
    """
    pids = {spec.pid for spec in specs}
    if len(pids) != 1:
        raise ValueError(f"read_values braucht Werte genau einer PID, nicht {sorted(pids)}")
    (pid,) = pids
    cmd = f"01{pid:02X}"
    values: dict[str, float | None] = {spec.key: None for spec in specs}
    response = elm.query(cmd)
    if response is None:
        return values
    messages = [m for m in _messages(cmd, response) if m[:2] == bytes([0x41, pid])]
    for spec in specs:
        for message in messages:
            if len(message) >= 2 + spec.size:
                value = spec.decode(message[2 : 2 + spec.size])
                if value is not None:
                    values[spec.key] = value
                    break
    return values


def read_value(elm: Elm327, spec: PidSpec) -> float | None:
    """Ein Wert (siehe ``read_values``)."""
    return read_values(elm, [spec])[spec.key]
