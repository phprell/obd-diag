"""Differenzialtest von Mode 06 (``protocol.monitor_tests``) gegen python-OBD.

Verglichen werden:

- jede Unit-and-Scaling-ID (J1979 Anhang E), die python-OBD kennt, über alle 65536
  Werte der zwei Datenbytes, dazu das Vorzeichen (ab $80) und die Einheit;
- ganze Antworten ``46 <MID> …`` mit beliebigen Bytes: dieselben Tests mit denselben
  Werten und Grenzen;
- die Monitor-IDs: jede MID, für die python-OBD einen Befehl hat, bekommt bei uns einen
  eigenen Namen (nicht nur „Monitor xx“).

python-OBD steht unter GPL-2.0 und darf nur in Tests vorkommen, nie in ``src/``; ist es
nicht installiert, wird der Test übersprungen. Bekannte Abweichung: UASID $2E
(Wahrheitswert) gibt python-OBD als ``bool`` zurück, wir als Zahl 0 oder 1 (Rohwert).
"""

import importlib
import math
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from obd_diag.protocol.headers import EcuMessage
from obd_diag.protocol.monitor_tests import (
    UNITS,
    TestResult,
    is_signed,
    mid_name,
    parse_results,
)

# Unsere Einheit -> Einheit von python-OBD (pint), wie sie ``str(quantity.units)`` zeigt
PINT_UNITS = {
    "": ("count", "dimensionless", "ratio"),
    "1/min": ("revolutions_per_minute",),
    "km/h": ("kilometer_per_hour",),
    "mV": ("millivolt",),
    "V": ("volt",),
    "mA": ("milliampere",),
    "A": ("ampere",),
    "ms": ("millisecond",),
    "s": ("second",),
    "mΩ": ("milliohm",),
    "Ω": ("ohm",),
    "kΩ": ("kiloohm",),
    "°C": ("degree_Celsius",),
    "kPa": ("kilopascal",),
    "°": ("degree",),
    "mHz": ("millihertz",),
    "Hz": ("hertz",),
    "kHz": ("kilohertz",),
    "km": ("kilometer",),
    "mV/ms": ("millivolt / millisecond",),
    "g/s": ("gps",),
    "Pa/s": ("pascal / second",),
    "kg/h": ("kilogram / hour",),
    "g": ("gram",),
    "mg": ("milligram",),
    "%": ("percent",),
    "l": ("liter",),
    "in": ("inch",),
    "min": ("minute",),
    "mm²": ("millimeter ** 2",),
    "ppm": ("ppm",),
    "µA": ("microampere",),
    "µs": ("microsecond",),
    "mV/s": ("millivolt / second",),
    "Pa": ("pascal",),
}
BOOLEAN = 0x2E  # python-OBD: bool statt Zahl


def _python_obd() -> tuple[dict[int, Any], Any, Any]:
    try:
        uas = importlib.import_module("obd.UnitsAndScaling").UAS_IDS
        commands = importlib.import_module("obd.commands").commands
        message = importlib.import_module("obd.protocols.protocol").Message
    except ImportError:
        pytest.skip("python-OBD nicht installiert (uv run --with obd ...)")
    return uas, commands, message


def test_same_unit_and_scaling_ids() -> None:
    theirs, _, _ = _python_obd()
    assert set(UNITS) == set(theirs)


@pytest.mark.parametrize("uasid", sorted(set(UNITS) - {BOOLEAN}))
def test_every_raw_value_scales_like_python_obd(uasid: int) -> None:
    theirs, _, _ = _python_obd()
    convert = theirs[uasid]
    assert convert.signed == is_signed(uasid)
    for raw in range(0x10000):
        ours = TestResult("7E8", 1, 1, uasid, raw, 0, 0).value
        quantity = convert(raw.to_bytes(2))
        assert ours is not None
        assert math.isclose(ours, quantity.magnitude, rel_tol=1e-9, abs_tol=1e-9), (
            uasid,
            raw,
        )
    unit = TestResult("7E8", 1, 1, uasid, 0, 0, 0).unit
    assert str(quantity.units) in PINT_UNITS[unit], (uasid, unit, quantity.units)


def test_boolean_unit() -> None:
    theirs, _, _ = _python_obd()
    for raw in (0, 1, 0x0100, 0xFFFF):
        assert bool(TestResult("7E8", 1, 1, BOOLEAN, raw, 0, 0).value) == theirs[BOOLEAN](
            raw.to_bytes(2)
        )


def _their_tests(commands: Any, message_type: Any, data: bytes) -> list[Any]:
    message = message_type([])
    message.data = bytearray(data)
    monitor = commands[6][data[1]].decode([message])
    return list(monitor.tests)


@given(
    mid=st.sampled_from([0x01, 0x21, 0x31, 0x85, 0x98, 0xA2, 0xB0]),
    blocks=st.lists(
        st.tuples(
            st.integers(0, 0xFF),
            st.sampled_from(sorted(set(UNITS) - {BOOLEAN})),
            st.binary(min_size=6, max_size=6),
        ),
        min_size=1,
        max_size=5,
        unique_by=lambda block: block[0],  # python-OBD hält je TID nur einen Test
    ),
)
def test_whole_answers_decode_like_python_obd(
    mid: int, blocks: list[tuple[int, int, bytes]]
) -> None:
    _, commands, message_type = _python_obd()
    data = bytes([0x46]) + b"".join(bytes([mid, tid, uas]) + raw for tid, uas, raw in blocks)
    ours = parse_results(EcuMessage(0x7E8, "7E8", data), mid)
    theirs = _their_tests(commands, message_type, data)
    assert len(ours) == len(theirs)
    # python-OBD ordnet nach TID-Tabelle, nicht nach der Antwort
    theirs.sort(key=lambda t: t.tid)
    for mine, other in zip(sorted(ours, key=lambda t: t.tid), theirs, strict=True):
        assert mine.tid == other.tid
        for value, quantity in (
            (mine.value, other.value),
            (mine.minimum, other.min),
            (mine.maximum, other.max),
        ):
            assert value is not None
            assert math.isclose(value, quantity.magnitude, rel_tol=1e-9, abs_tol=1e-9)


def test_every_known_monitor_id_has_a_name() -> None:
    _, commands, _ = _python_obd()
    mids = [
        int(c.command[2:], 16)
        for c in commands[6]
        if c is not None and c.command[2:] not in (b"00", b"20", b"40", b"60", b"80", b"A0")
    ]
    assert len(mids) > 80
    unnamed = [f"{mid:02X}" for mid in mids if mid_name(mid).startswith("Monitor ")]
    assert unnamed == []
