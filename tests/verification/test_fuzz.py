"""Robustheit gegen beliebige Adapter-Ausgaben.

Erlaubt sind nur die dokumentierten Ausnahmen: ``ElmError`` (samt Unterklassen) aus der
Protokoll-Schicht, ``ValueError`` aus ``parse_dtc_response``/``split_messages`` und
``Elm327.voltage``. Nie ``IndexError``, ``KeyError``, ``UnicodeError`` o. Ä. Gegen
Hängen: alle Schleifen laufen über die Eingabezeilen; die Frist je Beispiel meldet
zusätzlich auffällig langsame Fälle (z. B. katastrophales Backtracking in Regexen).
"""

from contextlib import suppress
from datetime import timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from obd_diag.protocol.dtc_decode import parse_dtc_response
from obd_diag.protocol.elm327 import Elm327, ElmError
from obd_diag.protocol.frames import split_messages
from obd_diag.protocol.obd import (
    DTC_MODES,
    clear_dtcs,
    read_dtcs,
    read_freeze_frame,
    read_pid,
)
from tests.verification.helpers import RawTransport, adapter_output, can_message

SETTINGS = settings(max_examples=300, deadline=timedelta(seconds=2))

# Bausteine, aus denen echte Antworten bestehen: so trifft der Zufall auch tiefe Pfade
_TOKENS = [
    "0",
    "1",
    "7",
    "F",
    "A",
    "00",
    "43",
    "47",
    "4A",
    "44",
    "7F",
    "78",
    " ",
    ":",
    "0:",
    "1:",
    "2:",
    "F:",
    "008",
    "00A",
    "FFF",
    "\r",
    "\n",
    "\r\n",
    "SEARCHING...",
    "BUS INIT: ",
    "...",
    "OK",
    "NO DATA",
    "?",
    "STOPPED",
    "<DATA ERROR",
    "ERR94",
    "\x00",
    "\xfc",
    ">",
]
structured = st.lists(st.sampled_from(_TOKENS), max_size=60).map("".join)
garbage = st.one_of(structured, st.text(max_size=200), st.binary(max_size=200))


def _raw(text: str | bytes) -> bytes:
    data = text if isinstance(text, bytes) else text.encode("utf-8", errors="replace")
    return data.replace(b">", b"") + b"\r\r>"  # genau ein Prompt am Ende


@SETTINGS
@given(garbage, st.sampled_from(DTC_MODES), st.booleans())
def test_command_and_parse_raise_only_documented_errors(
    text: str | bytes, mode: int, can: bool
) -> None:
    cmd = f"{mode:02X}"
    elm = Elm327(RawTransport({cmd: _raw(text)}))
    try:
        response = elm.command(cmd)
    except ElmError:
        return
    with suppress(ValueError):
        parse_dtc_response(response, mode, can=can)


@SETTINGS
@given(garbage, st.sampled_from(DTC_MODES), st.booleans())
def test_read_dtcs_raises_only_elm_error(text: str | bytes, mode: int, can: bool) -> None:
    cmd = f"{mode:02X}"
    try:
        codes = read_dtcs(Elm327(RawTransport({cmd: _raw(text)})), mode, can=can)
    except ElmError:
        return
    assert all(len(code) == 5 for code in codes)


@SETTINGS
@given(st.text(max_size=200))
def test_split_messages_raises_only_value_error(text: str) -> None:
    with suppress(ValueError):
        split_messages(text)


@SETTINGS
@given(
    st.lists(st.tuples(st.integers(1, 255), st.integers(0, 255)), min_size=4, max_size=40),
    st.booleans(),
    st.data(),
)
def test_mutated_multiframe_never_yields_wrong_codes(
    dtcs: list[tuple[int, int]], spaces: bool, data: st.DataObject
) -> None:
    """Fehlende, doppelte oder vertauschte Frames: Fehler, nie stillschweigend falsche Codes."""
    lines = list(can_message(0x03, dtcs, spaces, pad=0).lines)
    i = data.draw(st.integers(1, len(lines) - 1))
    kind = data.draw(st.sampled_from(["drop", "duplicate", "swap"]))
    if kind == "drop":
        del lines[i]
    elif kind == "duplicate":
        lines.insert(i, lines[i])
    else:
        j = data.draw(st.integers(1, len(lines) - 1).filter(lambda j: j != i))
        if lines[i] == lines[j]:
            return
        lines[i], lines[j] = lines[j], lines[i]
    try:
        messages = split_messages("\n".join(lines))
    except ValueError:
        return
    raise AssertionError(f"verfälschte Antwort angenommen: {kind} {i}: {messages}")


@SETTINGS
@given(garbage)
def test_other_services_raise_only_documented_errors(text: str | bytes) -> None:
    raw = _raw(text)
    commands = ["ATZ", "ATRV", "0100", "ATDPN", "ATDP", "010C", "04"]
    commands += [f"02{pid:02X}00" for pid in (0x02, 0x04, 0x05, 0x0C, 0x0D)]
    elm = Elm327(RawTransport(dict.fromkeys(commands, raw)))
    for call in (
        elm.initialize,
        elm.protocol,
        lambda: read_pid(elm, 0x0C),
        lambda: read_freeze_frame(elm),
        lambda: clear_dtcs(elm),
    ):
        with suppress(ElmError):
            call()
    with suppress(ElmError, ValueError):
        elm.voltage()


def test_adapter_output_has_single_prompt() -> None:
    assert adapter_output(["4300"]) == b"4300\r\r>"
