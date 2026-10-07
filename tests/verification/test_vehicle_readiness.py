"""Verifikation von FIN (Mode 09 PID 02) und Readiness (Mode 01 PID 01).

Round-Trip: FIN bzw. Monitorzustände -> Adapter-Text nach unabhängigem Encoder
(ELM327-Datenblatt S. 44-46, ISO 15765-2; Bit-Tabelle aus Wikipedia „OBD-II PIDs“,
Service 01 PID 01) -> ``Elm327.command`` -> ``read_vin``/``read_readiness``.
Fuzz: beliebige Ausgaben ergeben nur ``ElmError`` bzw. ``None``, die Decoder nur
``ValueError``.
"""

from contextlib import suppress
from datetime import timedelta
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from obd_diag.protocol.elm327 import Elm327, ElmError
from obd_diag.services.readiness import MonitorState, decode_readiness, read_readiness
from obd_diag.services.vehicle import decode_vin, parse_vin_response, read_vin
from tests.verification.helpers import RawTransport, adapter_output
from tests.verification.test_fuzz import garbage

SETTINGS = settings(max_examples=300, deadline=timedelta(seconds=2))

_VIN_CHARS = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"
vins = st.text(alphabet=_VIN_CHARS, min_size=17, max_size=17)


def _hex(data: bytes, spaces: bool) -> str:
    return (" " if spaces else "").join(f"{b:02X}" for b in data)


def isotp_lines(payload: bytes, spaces: bool, pad: int) -> list[str]:
    """Mehrteilige Nachricht so, wie sie der ELM327 mit ATH0/CAF1 zeigt (S. 44-46)."""
    sep = ": " if spaces else ":"
    lines = [f"{len(payload):03X}", "0" + sep + _hex(payload[:6], spaces)]
    rest = payload[6:]
    seq = 1
    while rest:
        chunk, rest = rest[:7], rest[7:]
        chunk += bytes([pad]) * (7 - len(chunk))
        lines.append(f"{seq % 16:X}{sep}{_hex(chunk, spaces)}")
        seq += 1
    return lines


def legacy_vin_lines(vin: str, spaces: bool) -> list[str]:
    """J1850/ISO 9141/KWP: 5 Zeilen ``49 02 <n>`` + 4 Bytes, vorn mit 00 auf 20 aufgefüllt."""
    data = b"\x00\x00\x00" + vin.encode("ascii")
    return [_hex(bytes((0x49, 0x02, n + 1)) + data[4 * n : 4 * n + 4], spaces) for n in range(5)]


@st.composite
def framing(draw: st.DrawFn) -> dict[str, Any]:
    return {
        "echo": draw(st.booleans()),
        "status": draw(st.sampled_from([None, "SEARCHING...", "BUS INIT: ...OK"])),
        "newline": draw(st.sampled_from(["\r", "\r\n"])),
    }


def _elm(cmd: str, lines: list[str], frame: dict[str, Any]) -> Elm327:
    raw = adapter_output(
        lines,
        echo=cmd if frame["echo"] else None,
        status=frame["status"],
        newline=frame["newline"],
    )
    return Elm327(RawTransport({cmd: raw}))


@SETTINGS
@given(vins, st.booleans(), st.sampled_from([0x00, 0x55, 0xAA]), st.integers(0, 3), framing())
def test_vin_can_round_trip(
    vin: str, spaces: bool, pad: int, nuls: int, frame: dict[str, Any]
) -> None:
    payload = b"\x49\x02\x01" + b"\x00" * nuls + vin.encode("ascii")
    assert read_vin(_elm("0902", isotp_lines(payload, spaces, pad), frame)) == vin


@SETTINGS
@given(vins, st.booleans(), st.booleans(), framing())
def test_vin_legacy_round_trip(vin: str, spaces: bool, twice: bool, frame: dict[str, Any]) -> None:
    lines = legacy_vin_lines(vin, spaces)
    if twice:  # zweites Steuergerät mit derselben FIN
        lines += legacy_vin_lines(vin, spaces)
    assert read_vin(_elm("0902", lines, frame)) == vin


_SPARK_BITS = [
    "catalyst",
    "heated_catalyst",
    "evap",
    "secondary_air",
    "ac_refrigerant",
    "oxygen_sensor",
    "oxygen_sensor_heater",
    "egr",
]
_DIESEL_BITS = [
    "nmhc_catalyst",
    "nox_scr",
    None,
    "boost_pressure",
    None,
    "exhaust_gas_sensor",
    "pm_filter",
    "egr",
]
_CONTINUOUS = ["misfire", "fuel_system", "components"]
states = st.sampled_from(list(MonitorState))


@SETTINGS
@given(
    st.booleans(),
    st.integers(0, 127),
    st.booleans(),
    st.lists(states, min_size=11, max_size=11),
    st.booleans(),
    framing(),
)
def test_readiness_round_trip(
    mil: bool,
    count: int,
    diesel: bool,
    chosen: list[MonitorState],
    spaces: bool,
    frame: dict[str, Any],
) -> None:
    keys = _CONTINUOUS + [k for k in (_DIESEL_BITS if diesel else _SPARK_BITS) if k]
    expected = dict(zip(keys, chosen, strict=False))
    a = (0x80 if mil else 0) | count
    b = 0x08 if diesel else 0
    for bit, key in enumerate(_CONTINUOUS):
        if expected[key] is not MonitorState.NOT_SUPPORTED:
            b |= 1 << bit
        if expected[key] is MonitorState.INCOMPLETE:
            b |= 1 << (bit + 4)
    c = d = 0
    for bit, maybe in enumerate(_DIESEL_BITS if diesel else _SPARK_BITS):
        if maybe is None:
            continue
        if expected[maybe] is not MonitorState.NOT_SUPPORTED:
            c |= 1 << bit
        if expected[maybe] is MonitorState.INCOMPLETE:
            d |= 1 << bit
    line = _hex(bytes((0x41, 0x01, a, b, c, d)), spaces)
    status = read_readiness(_elm("0101", [line], frame))
    assert status is not None
    assert (status.mil_on, status.dtc_count, status.compression_ignition) == (mil, count, diesel)
    assert {m.key: m.state for m in status.monitors} == expected
    assert status.ready == (MonitorState.INCOMPLETE not in expected.values())


def _raw(text: str | bytes) -> bytes:
    data = text if isinstance(text, bytes) else text.encode("utf-8", errors="replace")
    return data.replace(b">", b"") + b"\r\r>"


@SETTINGS
@given(garbage)
def test_read_vin_and_readiness_garbage(text: str | bytes) -> None:
    raw = _raw(text)
    elm = Elm327(RawTransport({"0902": raw, "0101": raw}))
    with suppress(ElmError):
        vin = read_vin(elm)
        assert vin is None or (len(vin) == 17 and vin.isalnum() and vin.isascii())
    with suppress(ElmError):
        read_readiness(elm)


@SETTINGS
@given(st.text(max_size=300))
def test_parse_vin_response_raises_only_value_error(text: str) -> None:
    with suppress(ValueError):
        parse_vin_response(text)


@SETTINGS
@given(st.binary(max_size=10))
def test_decode_readiness_bytes(data: bytes) -> None:
    try:
        status = decode_readiness(data)
    except ValueError:
        assert len(data) < 4
        return
    assert len(status.monitors) in (9, 11)
    assert 0 <= status.dtc_count <= 127


@SETTINGS
@given(st.one_of(st.text(max_size=40), vins))
def test_decode_vin_never_raises(text: str) -> None:
    info = decode_vin(text)
    if not info.valid:
        assert info.checksum_ok is None and info.model_year is None
    else:
        assert info.model_year is None or info.model_year >= 1980
        assert len(info.wmi) == 3
