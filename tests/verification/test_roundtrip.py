"""Eigenschaftsbasierte Round-Trip-Tests: Codes -> Adapter-Text (unabhängiger Encoder) ->
``Elm327.command`` -> ``read_dtcs`` -> dieselben Codes.

Abgedeckt: Mode 03/07/0A; Legacy (7-Byte-Zeilen, 3 Codes je Zeile, 00-Füllung, mehrere
Zeilen je Steuergerät); CAN Einzel-Frame mit Zählbyte; CAN mehrteilig ohne Header
(Längenzeile, ``0:``/``1:`` …, Nummern-Überlauf nach F, Füllung im letzten Frame); mit und
ohne Leerzeichen; mehrere Steuergeräte nacheinander und verschränkt; Echo, Statuszeilen,
Leerzeilen und CR bzw. CR LF.
"""

import importlib
import random
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from obd_diag.protocol.elm327 import Elm327
from obd_diag.protocol.frames import split_messages
from obd_diag.protocol.obd import DTC_MODES, read_dtcs
from tests.verification.helpers import (
    Dtc,
    Message,
    RawTransport,
    adapter_output,
    can_message,
    dtc_text,
    interleave,
    legacy_messages,
)

SETTINGS = settings(max_examples=300, deadline=None)

dtcs = st.tuples(st.integers(0, 255), st.integers(0, 255)).filter(lambda d: d != (0, 0))
# bis 70 Codes: 2 + 140 Bytes = 21 Frames, also Überlauf der Frame-Nummer nach F
can_ecus = st.lists(st.lists(dtcs, max_size=70), min_size=1, max_size=4)
legacy_ecus = st.lists(st.lists(dtcs, max_size=10), min_size=1, max_size=4)
modes = st.sampled_from(DTC_MODES)


@st.composite
def framing(draw: st.DrawFn) -> dict[str, Any]:
    """Echo, Statuszeile, Leerzeilen und Zeilenende, wie sie Adapter liefern."""
    return {
        "echo": draw(st.booleans()),
        "status": draw(st.sampled_from([None, "SEARCHING...", "BUS INIT: OK", "BUS INIT: ...OK"])),
        "blank_lines": draw(st.lists(st.integers(0, 30), max_size=3)),
        "newline": draw(st.sampled_from(["\r", "\r\n"])),
    }


def _read(mode: int, lines: list[str], frame: dict[str, Any], *, can: bool) -> list[str]:
    cmd = f"{mode:02X}"
    raw = adapter_output(
        lines,
        echo=cmd if frame["echo"] else None,
        status=frame["status"],
        blank_lines=frame["blank_lines"],
        newline=frame["newline"],
    )
    transport = RawTransport({cmd: raw})
    codes = read_dtcs(Elm327(transport), mode, can=can)
    assert transport.sent == [cmd]
    return codes


@SETTINGS
@given(modes, legacy_ecus, st.booleans(), framing(), st.randoms(use_true_random=False))
def test_legacy_roundtrip(
    mode: int, ecus: list[list[Dtc]], spaces: bool, frame: dict[str, Any], rng: random.Random
) -> None:
    messages = [m for ecu in ecus for m in legacy_messages(mode, ecu, spaces)]
    lines, expected = interleave(messages, rng)
    assert _read(mode, lines, frame, can=False) == expected


@SETTINGS
@given(
    modes,
    can_ecus,
    st.booleans(),
    st.sampled_from([0x00, 0x55, 0xAA, 0xFF]),
    framing(),
    st.randoms(use_true_random=False),
)
def test_can_roundtrip(
    mode: int,
    ecus: list[list[Dtc]],
    spaces: bool,
    pad: int,
    frame: dict[str, Any],
    rng: random.Random,
) -> None:
    messages = [can_message(mode, ecu, spaces, pad) for ecu in ecus]
    lines, expected = interleave(messages, rng)
    assert _read(mode, lines, frame, can=True) == expected


@SETTINGS
@given(modes, st.lists(dtcs, min_size=3, max_size=70), st.booleans())
def test_can_multiframe_message_length(mode: int, ecu: list[Dtc], spaces: bool) -> None:
    message = can_message(mode, ecu, spaces, pad=0xAA)
    (data,) = split_messages("\n".join(message.lines))
    assert len(data) == 2 + 2 * len(ecu)
    assert data[:2] == bytes((0x40 + mode, len(ecu)))


@SETTINGS
@given(st.lists(st.lists(dtcs, min_size=3, max_size=30), min_size=2, max_size=3), st.booleans())
def test_can_two_multiframe_ecus_mixed_are_rejected(ecus: list[list[Dtc]], spaces: bool) -> None:
    """Verschränkte mehrteilige Nachrichten (Datenblatt S. 45) dürfen nie Codes liefern."""
    messages = [can_message(0x03, ecu, spaces, pad=0) for ecu in ecus]
    first, second = messages[0].lines, messages[1].lines
    # zweite Nachricht beginnt, bevor die erste vollständig ist
    lines = [*first[:2], *second, *first[2:]]
    with pytest.raises(ValueError):
        split_messages("\n".join(lines))


def test_encoder_matches_datasheet_examples() -> None:
    # S. 32: 0133 -> P0133, D016 -> U1016, 1131 -> P1131
    assert [dtc_text(d) for d in [(0x01, 0x33), (0xD0, 0x16), (0x11, 0x31)]] == [
        "P0133",
        "U1016",
        "P1131",
    ]
    # S. 32: Legacy-Antwort mit einem Code
    (message,) = legacy_messages(0x03, [(0x01, 0x33)], spaces=True)
    assert message == Message(("43 01 33 00 00 00 00",), ("P0133",), False)
    # S. 46: Format der Frame-Zeilen mit Leerzeichen
    long = can_message(0x03, [(0x01, 0x33)] * 4, spaces=True, pad=0)
    assert long.lines == ("00A", "0: 43 04 01 33 01 33", "1: 01 33 01 33 00 00 00")
    # ELMduino #285 (Vgate iCar Pro, ATS0): ``0:`` ohne Leerzeichen
    assert can_message(0x03, [(0x01, 0x33)] * 4, spaces=False, pad=0).lines[1] == "0:430401330133"


_PLAIN: dict[str, Any] = {"echo": False, "status": None, "blank_lines": [], "newline": "\r"}


@SETTINGS
@given(can_ecus)
def test_can_against_python_obd(ecus: list[list[Dtc]]) -> None:
    """Zweites, fremdes Urteil: python-OBD liest dieselben Antworten mit Headern (ATH1)."""
    try:
        protocols: Any = importlib.import_module("obd.protocols")
        decoders: Any = importlib.import_module("obd.decoders")
    except ImportError:
        pytest.skip("python-OBD nicht installiert")
    for index, ecu in enumerate(ecus):
        payload = [0x43, len(ecu), *(b for d in ecu for b in d)]
        can_id = f"7E{8 + index:X}"
        if len(payload) <= 7:
            frames = [f"{can_id} {len(payload):02X} " + " ".join(f"{b:02X}" for b in payload)]
        else:
            frames = [f"{can_id} 1{len(payload) >> 8:X} {len(payload) & 0xFF:02X} "]
            frames[0] += " ".join(f"{b:02X}" for b in payload[:6])
            rest, seq = payload[6:], 1
            while rest:
                chunk, rest = rest[:7], rest[7:]
                chunk += [0] * (7 - len(chunk))
                frames.append(f"{can_id} 2{seq % 16:X} " + " ".join(f"{b:02X}" for b in chunk))
                seq += 1
        parsed = protocols.ISO_15765_4_11bit_500k([])(frames)
        theirs = [code.upper() for code, _ in decoders.dtc(parsed)]
        lines = list(can_message(0x03, ecu, spaces=True, pad=0).lines)
        assert _read(0x03, lines, _PLAIN, can=True) == theirs
