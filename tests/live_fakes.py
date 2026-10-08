"""Ersatz für die PID-Tabelle (``protocol.pids``) in Tests des Live-Dienstes.

Die Tests von ``services.live`` und ``obd-diag live`` hängen so nicht von der
Implementierung der PID-Tabelle ab, nur davon, dass ``PidSpec`` existiert. Die Fakes
senden dieselben Anfragen wie laut J1979 nötig: ``0100`` für die unterstützten PIDs,
``01xx`` je Wert.
"""

import pytest

from obd_diag.protocol import pids
from obd_diag.protocol.elm327 import Elm327
from obd_diag.protocol.pids import PidSpec


def _rpm(b: bytes) -> float:
    return (b[0] * 256 + b[1]) / 4


RPM = PidSpec(0x0C, "rpm", "Motordrehzahl", "1/min", 2, _rpm, 0, 16383.75)
SPEED = PidSpec(0x0D, "speed", "Geschwindigkeit", "km/h", 1, lambda b: float(b[0]), 0, 255)
COOLANT = PidSpec(
    0x05, "coolant_temp", "Kühlmitteltemperatur", "°C", 1, lambda b: b[0] - 40.0, -40, 215
)
LOAD = PidSpec(0x04, "engine_load", "Motorlast", "%", 1, lambda b: b[0] * 100 / 255, 0, 100)
# Bekannt, aber vom Testfahrzeug nicht unterstützt (Bit für 0x10 in 0100 fehlt)
MAF = PidSpec(0x10, "maf", "Luftmassenstrom", "g/s", 2, lambda b: _rpm(b) / 25, 0, 655.35)

TABLE = {spec.pid: spec for spec in (RPM, SPEED, COOLANT, LOAD, MAF)}
# Was das Testfahrzeug laut Fake unterstützt (zusätzlich 0x0B, das TABLE nicht kennt)
SUPPORTED = {0x04, 0x05, 0x0B, 0x0C, 0x0D}

# Live-Antworten eines laufenden Motors: 1726 1/min, 50 km/h, 86 °C, 50,2 % Last
LIVE_VALUES = {"010C": "410C1AF8", "010D": "410D32", "0105": "41057E", "0104": "410480"}


def fake_read_supported_pids(elm: Elm327) -> set[int]:
    return set() if elm.query("0100") is None else set(SUPPORTED)


def fake_read_value(elm: Elm327, spec: PidSpec) -> float | None:
    text = elm.query(f"01{spec.pid:02X}")
    if text is None:
        return None
    try:
        data = bytes.fromhex(text.replace(" ", ""))
    except ValueError:
        return None
    if len(data) < 2 + spec.size or data[:2] != bytes([0x41, spec.pid]):
        return None
    return spec.decode(data[2 : 2 + spec.size])


def fake_pid_by_key(key: str) -> PidSpec:
    for spec in TABLE.values():
        if spec.key == key:
            return spec
    raise KeyError(f"unbekannter Wert {key!r}")


def use_fake_pids(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ersetzt Tabelle und Lesefunktionen in ``protocol.pids``."""
    monkeypatch.setattr(pids, "PIDS", TABLE)
    monkeypatch.setattr(pids, "read_supported_pids", fake_read_supported_pids)
    monkeypatch.setattr(pids, "read_value", fake_read_value)
    monkeypatch.setattr(pids, "pid_by_key", fake_pid_by_key)
