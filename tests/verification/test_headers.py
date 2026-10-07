"""Verifikation der Header-Rückfallebene: vermischte mehrteilige Antworten mehrerer
Steuergeräte (ELM327DS S. 45) werden mit ``ATH1`` neu gelesen und je CAN-ID
zusammengesetzt.

Der unabhängige Encoder (``tests/verification/helpers.py``: ``isotp_frames``,
``header_line``, ``mix_frames``) erzeugt dieselben gemischten Frames einmal so, wie der
ELM327 sie ohne Header zeigt (``LLL``/``0:``/``N:``), und einmal mit Header
(``7E8 10 0A …`` bzw. ``18 DA F1 10 …``). Erwartet wird: Codes bzw. FIN aller
Steuergeräte, geordnet nach Steuergeräte-Adresse (11 Bit: CAN-ID, 29 Bit: Quelladresse),
wenn die Rückfallebene greift; sonst in der Reihenfolge, in der die Nachrichten beginnen.
"""

import random

import pytest
from hypothesis import event, given, settings
from hypothesis import strategies as st

from obd_diag.protocol.elm327 import Elm327, ElmError
from obd_diag.protocol.headers import EcuMessage, HeaderFormat, parse_header_response
from obd_diag.protocol.obd import DTC_MODES, read_dtcs
from obd_diag.services.vehicle import read_vin
from tests.verification.helpers import (
    Dtc,
    Frame,
    HeaderRawTransport,
    adapter_output,
    dtc_text,
    header_line,
    isotp_frames,
    mix_frames,
)

SETTINGS = settings(max_examples=300, deadline=None)

dtcs = st.tuples(st.integers(0, 255), st.integers(0, 255)).filter(lambda d: d != (0, 0))


@st.composite
def ecu_ids(draw: st.DrawFn, count: int) -> list[tuple[int, str]]:
    """``count`` verschiedene Absender als (Adresse, CAN-ID), alle 11 oder alle 29 Bit."""
    if draw(st.booleans()):
        ids = draw(st.lists(st.integers(0x7E8, 0x7EF), min_size=count, max_size=count, unique=True))
        return [(i, f"{i:03X}") for i in ids]
    sources = draw(st.lists(st.integers(0, 255), min_size=count, max_size=count, unique=True))
    return [(s, f"18DAF1{s:02X}") for s in sources]


def _texts(mixed: list[Frame], spaces: bool) -> tuple[list[str], list[str]]:
    off = [line for frame in mixed for line in frame.off_lines]
    on = [header_line(frame, spaces) for frame in mixed]
    return off, on


def _start_order(mixed: list[Frame]) -> list[str]:
    order: list[str] = []
    for frame in mixed:
        if frame.can_id not in order:
            order.append(frame.can_id)
    return order


@SETTINGS
@given(
    st.sampled_from(DTC_MODES),
    st.lists(st.lists(dtcs, max_size=30), min_size=2, max_size=5),
    st.booleans(),
    st.booleans(),
    st.sampled_from([0x00, 0x55, 0xAA]),
    st.data(),
)
def test_mixed_dtcs_recovered_per_ecu(
    mode: int,
    ecus: list[list[Dtc]],
    spaces: bool,
    pad_single: bool,
    pad: int,
    data: st.DataObject,
) -> None:
    ids = data.draw(ecu_ids(len(ecus)))
    rng = data.draw(st.randoms(use_true_random=False))
    codes_by_id: dict[str, list[str]] = {}
    frames = []
    for (_, can_id), ecu in zip(ids, ecus, strict=True):
        payload = [0x40 + mode, len(ecu), *(b for d in ecu for b in d)]
        frames.append(isotp_frames(can_id, payload, spaces, pad, pad_single))
        codes_by_id[can_id] = [dtc_text(d) for d in ecu]
    mixed = mix_frames(frames, rng)
    off, on = _texts(mixed, spaces)
    cmd = f"{mode:02X}"
    transport = HeaderRawTransport({cmd: adapter_output(off)}, {cmd: adapter_output(on)})

    codes = read_dtcs(Elm327(transport), mode, can=True)

    event("mit Headern neu gelesen" if "ATH1" in transport.sent else "ohne Header eindeutig")
    if "ATH1" in transport.sent:
        assert transport.sent == [cmd, "ATH1", cmd, "ATH0"]
        order = [can_id for _, can_id in sorted(ids)]
    else:
        # ohne Header eindeutig (keine mehrteiligen Nachrichten überlappen)
        assert transport.sent == [cmd]
        order = _start_order(mixed)
    assert codes == [code for can_id in order for code in codes_by_id[can_id]]


_VIN_CHARS = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"
vins = st.text(alphabet=_VIN_CHARS, min_size=17, max_size=17)


@SETTINGS
@given(st.lists(vins, min_size=2, max_size=4), st.booleans(), st.data())
def test_mixed_vins_recovered(vin_list: list[str], spaces: bool, data: st.DataObject) -> None:
    ids = data.draw(ecu_ids(len(vin_list)))
    rng = data.draw(st.randoms(use_true_random=False))
    frames = [
        isotp_frames(can_id, b"\x49\x02\x01" + vin.encode("ascii"), spaces, 0x00)
        for (_, can_id), vin in zip(ids, vin_list, strict=True)
    ]
    mixed = mix_frames(frames, rng)
    off, on = _texts(mixed, spaces)
    transport = HeaderRawTransport({"0902": adapter_output(off)}, {"0902": adapter_output(on)})

    vin = read_vin(Elm327(transport))

    event("mit Headern neu gelesen" if "ATH1" in transport.sent else "ohne Header eindeutig")
    by_id = {can_id: v for (_, can_id), v in zip(ids, vin_list, strict=True)}
    # mit Headern: niedrigste Adresse zuerst, sonst die zuerst begonnene Nachricht
    first = min(ids)[1] if "ATH1" in transport.sent else _start_order(mixed)[0]
    assert vin == by_id[first]


@SETTINGS
@given(
    st.lists(st.lists(st.integers(0, 255), min_size=1, max_size=60), min_size=1, max_size=5),
    st.booleans(),
    st.booleans(),
    st.data(),
)
def test_parse_header_response_groups_by_sender(
    payloads: list[list[int]], spaces: bool, pad_single: bool, data: st.DataObject
) -> None:
    ids = data.draw(ecu_ids(len(payloads)))
    rng = data.draw(st.randoms(use_true_random=False))
    frames = [
        isotp_frames(can_id, payload, spaces, 0xAA, pad_single)
        for (_, can_id), payload in zip(ids, payloads, strict=True)
    ]
    _, on = _texts(mix_frames(frames, rng), spaces)
    expected = sorted(
        (EcuMessage(ecu, can_id, bytes(p)) for (ecu, can_id), p in zip(ids, payloads, strict=True)),
        key=lambda m: m.ecu,
    )
    assert parse_header_response("\r".join(on)) == expected


def _legacy_line(source: int, payload: list[int], spaces: bool) -> str:
    """Header ``48 6B <Quelle>``, Nutzdaten, Prüfbyte (ELM327DS S. 44).

    Als Prüfbyte dient die Summe wie bei ISO 9141-2/KWP; J1850 nutzt eine CRC. Der
    Parser prüft keines von beiden, er entfernt nur das letzte Byte.
    """
    data = [0x48, 0x6B, source, *payload]
    data.append(sum(data) & 0xFF)
    return (" " if spaces else "").join(f"{b:02X}" for b in data)


@SETTINGS
@given(
    st.lists(
        st.tuples(st.integers(0x10, 0x1F), st.lists(st.integers(0, 255), min_size=1, max_size=7)),
        min_size=1,
        max_size=6,
    ),
    st.booleans(),
)
def test_legacy_headers(lines: list[tuple[int, list[int]]], spaces: bool) -> None:
    text = "\r".join(_legacy_line(src, payload, spaces) for src, payload in lines)
    expected = sorted(
        (EcuMessage(src, f"486B{src:02X}", bytes(p)) for src, p in lines), key=lambda m: m.ecu
    )
    assert parse_header_response(text) == expected
    assert parse_header_response(text, can=False) == expected


def test_datasheet_header_example() -> None:
    # ELM327DS S. 44: dieselbe Antwort mit Headern; FA ist die J1850-CRC (nicht geprüft)
    (message,) = parse_header_response("48 6B 10 41 00 BE 3E B8 11 FA")
    assert message == EcuMessage(0x10, "486B10", bytes.fromhex("4100BE3EB811"))


def test_datasheet_two_ecus_mixed_calid() -> None:
    """ELM327DS S. 45: zwei Steuergeräte senden 09 04 gleichzeitig mehrteilig.

    Ohne Header ist die Antwort nicht zuzuordnen (siehe elm327_datasheet.yaml,
    ``calid-two-ecus-mixed``); mit Headern (hier nachgebaut, gleiche Bytes, IDs 7E8/7E9
    angenommen) gelingt es.
    """
    mixed_on = (
        "7E9 10 13 49 04 01 35 36 30\r"
        "7E9 21 32 38 39 34 39 41 43\r"
        "7E8 10 13 49 04 01 35 36 30\r"
        "7E9 22 00 00 00 00 00 00 31\r"
        "7E8 21 32 38 39 35 34 41 43\r"
        "7E8 22 00 00 00 00 00 00 00"
    )
    first, second = parse_header_response(mixed_on)
    assert (first.header, second.header) == ("7E8", "7E9")
    assert first.data == bytes.fromhex("490401353630") + b"28954AC" + bytes(6)
    # Länge 0x13: das letzte Byte 31 des Folge-Frames 2 ist Füllung
    assert second.data == bytes.fromhex("490401353630") + b"28949AC" + bytes(6)


@pytest.mark.parametrize(
    "response",
    [
        "7E8 21 01 02 03 04 05 06 07",  # Folge-Frame ohne ersten Frame
        "7E8 10 0A 43 04 01 33 03 00\r7E8 22 01 71 00 00 00 00 00",  # Lücke
        "7E8 10 0A 43 04 01 33 03 00",  # unvollständig
        "7E8 10 0A 43 04 01 33 03 00\r7E8 03 43 01 33",  # SF mitten in mehrteiliger
        "7E8 08 01 02 03 04 05 06 07 08",  # SF-Länge > 7
        "7E8 03 43 01",  # SF zu kurz
        "7E8 10 05 43 01 01 33 00 00",  # FF mit Länge < 8
        "7E8 45 00",  # unbekanntes PCI
        "7E8",  # ohne Daten
        "OK",
        "48 6B 10",  # Legacy ohne Daten
        "7E8 0",  # ungerade
    ],
)
def test_parse_header_response_rejects(response: str) -> None:
    with pytest.raises(ValueError):
        parse_header_response(response)


def test_flow_control_frames_are_skipped() -> None:
    response = "7E8 10 08 43 03 01 33 03 00\r7E0 30 00 00\r7E8 21 01 71 00 00 00 00 00"
    (message,) = parse_header_response(response)
    assert message.data == bytes.fromhex("4303013303000171")


def test_header_retry_failure_is_elm_error() -> None:
    mixed_off = "00A\r0:430401330300\r00A\r0:430401330300\r1:01710000000000\r1:01710000000000"
    transport = HeaderRawTransport(
        {"03": adapter_output(mixed_off.split("\r"))},
        {"03": adapter_output(["7E8 21 01 02 03 04 05 06 07"])},
    )
    with pytest.raises(ElmError, match="auch mit Headern"):
        read_dtcs(Elm327(transport), 0x03, can=True)
    assert transport.sent == ["03", "ATH1", "03", "ATH0"]


@pytest.mark.parametrize(
    ("line", "fmt"),
    [
        ("7E8 06 41 00 BE 3F A8 13", HeaderFormat.CAN_11),
        ("7E8064100BE3FA813", HeaderFormat.CAN_11),
        ("18 DA F1 10 06 41 00 BE 3F A8 13", HeaderFormat.CAN_29),
        ("18DAF110064100BE3FA813", HeaderFormat.CAN_29),
        ("48 6B 10 41 00 BE 3E B8 11 FA", HeaderFormat.LEGACY),
        ("86F1104100BE3EB8118D", HeaderFormat.LEGACY),
    ],
)
def test_header_formats(line: str, fmt: HeaderFormat) -> None:
    from obd_diag.protocol.headers import header_format

    assert header_format(line) is fmt


def test_rng_mix_is_order_preserving() -> None:
    a = isotp_frames("7E8", list(range(20)), True, 0)
    b = isotp_frames("7E9", list(range(30)), True, 0)
    mixed = mix_frames([a, b], random.Random(1))
    assert [f for f in mixed if f.can_id == "7E8"] == a
    assert [f for f in mixed if f.can_id == "7E9"] == b


_HEADER_TOKENS = ["7E8", "7E9", "18DAF110", "486B10", " ", "\r", "10", "21", "22", "0A", "06"]
_HEADER_TOKENS += ["03", "43", "00", "FF", "1F", "30", "2F", "OK", "?", "7F", "78"]


@SETTINGS
@given(
    st.one_of(st.text(max_size=200), st.lists(st.sampled_from(_HEADER_TOKENS)).map("".join)),
    st.sampled_from([None, True, False]),
)
def test_parse_header_response_raises_only_value_error(text: str, can: bool | None) -> None:
    try:
        messages = parse_header_response(text, can=can)
    except ValueError:
        return
    assert all(isinstance(m.data, bytes) for m in messages)
