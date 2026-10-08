"""Differenzialtest der Live-Werte (``protocol.pids``) gegen python-OBD.

Jede PID, die python-OBD kennt, wird für alle Byte-Werte (1 Byte: 256, 2 Byte: 65536)
verglichen, ebenso die Einheit. python-OBD steht unter GPL-2.0 und darf nur in Tests
vorkommen, nie in ``src/``; ist es nicht installiert (derzeit indirekt über
ELM327-emulator in der dev-Gruppe), wird der Test übersprungen.

Zwei bekannte Abweichungen, beide gegen J1979 geprüft, python-OBD liegt daneben:

- ``32`` Dampfdruck Tankentlüftung: J1979 nennt ein 16-Bit-Zweierkomplement mit
  0,25 Pa/Bit, Bereich -8192 bis 8191,75 Pa. python-OBD wertet A und B je einzeln
  als vorzeichenbehaftet aus (Bereich -8224 bis 8159,75 Pa, also außerhalb der Norm).
- ``44`` Lambda-Sollwert: J1979 skaliert mit 2/65536; python-OBD nimmt den gerundeten
  Faktor 0,0000305 aus der Einheitentabelle (Abweichung < 0,1 %).
- ``24``-``2B`` Spannung der Breitbandsonde: J1979 skaliert mit 8/65536, python-OBD
  mit 8/65535 (Abweichung < 0,002 %).

Teilen sich mehrere Werte eine PID, kennt python-OBD oft nur einen davon (Lambdasonden
``14``-``1B``: Spannung; ``24``-``2B``/``34``-``3B``: Spannung bzw. Strom, nicht
Lambda; ``55``-``58``: nur Bank in Byte A). Verglichen wird dann dieser Teil über alle
Bytewerte, die übrigen Bytes stehen auf 0.

Was python-OBD nicht kennt (``61``-``64``, ``66``, ``67``, ``A6`` und die übrigen
Teilwerte), prüft ``tests/unit/test_pids.py`` mit Beispielen aus der Norm.
"""

import importlib
import math
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest

from obd_diag.protocol.pids import PIDS, PidSpec

# Unsere Einheit -> Einheit von python-OBD (pint)
UNITS = {
    "%": "percent",
    "°C": "degree_Celsius",
    "kPa": "kilopascal",
    "1/min": "revolutions_per_minute",
    "km/h": "kilometer_per_hour",
    "°": "degree",
    "g/s": "gps",
    "s": "second",
    "km": "kilometer",
    "Pa": "pascal",
    "V": "volt",
    "Lambda": "ratio",
    "min": "minute",
    "L/h": "lph",
    "": "count",
    "mA": "milliampere",
}
NOT_IN_PYTHON_OBD = {0x61, 0x62, 0x63, 0x64, 0x66, 0x67, 0xA6}
# Teilwerte, die python-OBD bei einer bekannten PID nicht auswertet
NOT_DECODED_BY_PYTHON_OBD = {
    *(f"o2_s{n}_trim" for n in range(1, 9)),
    *(f"o2_s{n}_lambda" for n in range(1, 9)),
    *(f"o2_s{n}_lambda_current" for n in range(1, 9)),
    *(f"{t}_secondary_bank{b}" for t in ("stft", "ltft") for b in (3, 4)),
}


@dataclass(frozen=True)
class Window:
    """Welche Bytes der Antwort für einen Wert verglichen werden."""

    total: int  # Datenbytes der Antwort laut python-OBD
    offset: int  # erstes variiertes Byte
    width: int  # Anzahl variierter Bytes (1 oder 2)


def _window(spec: PidSpec) -> Window:
    shared = [s.pid for s in PIDS.values()].count(spec.pid) > 1
    if not shared:
        return Window(spec.size, 0, spec.size)
    if spec.key.endswith(("_wide_voltage", "_current")):
        return Window(4, 2, 2)
    return Window(2, 0, 1)  # o2_sN_voltage, *_secondary_bank1/2: Byte A


def _compared() -> list[PidSpec]:
    return [
        s
        for s in PIDS.values()
        if s.pid not in NOT_IN_PYTHON_OBD and s.key not in NOT_DECODED_BY_PYTHON_OBD
    ]


def _python_obd() -> tuple[Any, Any]:
    try:
        commands = importlib.import_module("obd.commands").commands
        message = importlib.import_module("obd.protocols.protocol").Message
    except ImportError:
        pytest.skip("python-OBD nicht installiert (uv run --with obd ...)")
    return commands, message


def _all_payloads(window: Window) -> Iterator[bytes]:
    before, after = bytes(window.offset), bytes(window.total - window.offset - window.width)
    if window.width == 1:
        yield from (before + bytes([a]) + after for a in range(256))
    else:
        yield from (before + bytes([a, b]) + after for a in range(256) for b in range(256))


def _theirs(command: Any, message_type: Any, spec: PidSpec, data: bytes) -> Any:
    message = message_type([])
    message.data = bytearray([0x41, spec.pid, *data])
    return command.decode([message])


def test_every_pid_is_compared_or_listed() -> None:
    commands, _ = _python_obd()
    ours = {s.pid for s in PIDS.values()}
    known = {pid for pid in ours if pid < len(commands[1]) and commands[1][pid] is not None}
    assert ours - known == NOT_IN_PYTHON_OBD
    # Jede PID, die python-OBD kennt, wird mit mindestens einem Teilwert verglichen
    assert {s.pid for s in _compared()} == known


@pytest.mark.parametrize("spec", _compared(), ids=lambda s: s.key)
def test_pid_matches_python_obd(spec: PidSpec) -> None:
    commands, message_type = _python_obd()
    command = commands[1][spec.pid]
    window = _window(spec)
    assert command.bytes == 2 + window.total, "Antwortlänge laut python-OBD"
    # siehe Modul-Docstring
    tolerance = 1e-3 if spec.pid == 0x44 or spec.key.endswith("_wide_voltage") else 1e-9
    mismatches = []
    for data in _all_payloads(window):
        theirs = _theirs(command, message_type, spec, data)
        assert str(theirs.units) == UNITS[spec.unit], spec.key
        # 32: python-OBD wertet A und B je einzeln vorzeichenbehaftet aus; ohne
        # gesetztes Bit 7 in B stimmen beide Auslegungen überein.
        if spec.pid == 0x32 and data[1] >= 0x80:
            continue
        ours = spec.decode(data[: spec.size])
        assert ours is not None, (spec.key, data.hex())
        if not math.isclose(theirs.magnitude, ours, rel_tol=tolerance, abs_tol=1e-9):
            mismatches.append((data.hex(), theirs.magnitude, ours))
    assert mismatches == []


def test_evap_pressure_follows_j1979_not_python_obd() -> None:
    spec = PIDS["evap_pressure"]
    # 16-Bit-Zweierkomplement, 0,25 Pa/Bit: Grenzen genau wie in J1979
    assert spec.decode(bytes([0x80, 0x00])) == -8192.0
    assert spec.decode(bytes([0x7F, 0xFF])) == 8191.75
    assert spec.decode(bytes([0xFF, 0xFF])) == -0.25
