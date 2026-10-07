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

PIDs, die python-OBD nicht kennt (``61``-``63``, ``A6``), prüft ``tests/unit/test_pids.py``
mit Beispielen aus der Norm.
"""

import importlib
import math
from collections.abc import Iterator
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
}
NOT_IN_PYTHON_OBD = {0x61, 0x62, 0x63, 0xA6}


def _python_obd() -> tuple[Any, Any]:
    try:
        commands = importlib.import_module("obd.commands").commands
        message = importlib.import_module("obd.protocols.protocol").Message
    except ImportError:
        pytest.skip("python-OBD nicht installiert (uv run --with obd ...)")
    return commands, message


def _all_payloads(size: int) -> Iterator[bytes]:
    if size == 1:
        yield from (bytes([a]) for a in range(256))
    else:
        yield from (bytes([a, b]) for a in range(256) for b in range(256))


def _theirs(command: Any, message_type: Any, spec: PidSpec, data: bytes) -> Any:
    message = message_type([])
    message.data = bytearray([0x41, spec.pid, *data])
    return command.decode([message])


def test_every_pid_is_compared_or_listed() -> None:
    commands, _ = _python_obd()
    known = {pid for pid in PIDS if pid < len(commands[1]) and commands[1][pid] is not None}
    assert set(PIDS) - known == NOT_IN_PYTHON_OBD


@pytest.mark.parametrize(
    "spec", [s for s in PIDS.values() if s.pid not in NOT_IN_PYTHON_OBD], ids=lambda s: s.key
)
def test_pid_matches_python_obd(spec: PidSpec) -> None:
    commands, message_type = _python_obd()
    command = commands[1][spec.pid]
    assert command.bytes == 2 + spec.size, "Antwortlänge laut python-OBD"
    tolerance = 1e-3 if spec.pid == 0x44 else 1e-9  # siehe Modul-Docstring
    mismatches = []
    for data in _all_payloads(spec.size):
        theirs = _theirs(command, message_type, spec, data)
        assert str(theirs.units) == UNITS[spec.unit], spec.key
        # 32: python-OBD wertet A und B je einzeln vorzeichenbehaftet aus; ohne
        # gesetztes Bit 7 in B stimmen beide Auslegungen überein.
        if spec.pid == 0x32 and data[1] >= 0x80:
            continue
        if not math.isclose(theirs.magnitude, spec.decode(data), rel_tol=tolerance, abs_tol=1e-9):
            mismatches.append((data.hex(), theirs.magnitude, spec.decode(data)))
    assert mismatches == []


def test_evap_pressure_follows_j1979_not_python_obd() -> None:
    spec = PIDS[0x32]
    # 16-Bit-Zweierkomplement, 0,25 Pa/Bit: Grenzen genau wie in J1979
    assert spec.decode(bytes([0x80, 0x00])) == -8192.0
    assert spec.decode(bytes([0x7F, 0xFF])) == 8191.75
    assert spec.decode(bytes([0xFF, 0xFF])) == -0.25
