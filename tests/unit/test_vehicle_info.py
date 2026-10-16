"""Mode 09 je Steuergerät (``protocol/vehicle_info.py``): unterstützte InfoTypes,
Kalibrierungs-IDs, CVN, Steuergerätename und Überwachungszähler."""

import pytest

from obd_diag.protocol.elm327 import Elm327, ElmError
from obd_diag.protocol.headers import EcuMessage, parse_header_response
from obd_diag.protocol.vehicle_info import (
    COMPRESSION_COUNTERS,
    SPARK_COUNTERS,
    EcuInfo,
    PerformanceCounter,
    parse_calibration_ids,
    parse_cvns,
    parse_ecu_name,
    parse_performance,
    parse_supported,
    read_ecu_info,
)
from obd_diag.transport import TransportError
from obd_diag.transport.trace import ReplayTransport
from tests.fakes import CAN_CAR_FULL, ECU_INFO_HEADERS, IPT_SPARK, FakeTransport, can_lines


def _msgs(text: str) -> list[EcuMessage]:
    return parse_header_response(text)


def _read(headers: dict[str, str]) -> tuple[list[EcuInfo] | None, FakeTransport]:
    transport = FakeTransport(CAN_CAR_FULL, headers_on=headers)
    return read_ecu_info(Elm327(transport)), transport


# --- unterstützte InfoTypes ----------------------------------------------------------


def test_supported_info_types_from_the_bitmask() -> None:
    # 55 40: 02, 04, 06, 08 und 0A (Bit 7 von A = InfoType 01)
    (message,) = _msgs(can_lines("7E8", "490055400000"))
    assert parse_supported(message) == (0x02, 0x04, 0x06, 0x08, 0x0A)


def test_supported_info_types_legacy_with_message_number() -> None:
    # J1850/ISO 9141/KWP: 49 00 01 und vier Bytes
    (message,) = _msgs("48 6B 10 49 00 01 54 40 00 00 9A")
    assert parse_supported(message) == (0x02, 0x04, 0x06, 0x0A)


@pytest.mark.parametrize("payload", ["7F0912", "4900554000", "49025540000000", "41005540000000"])
def test_other_answers_are_no_bitmask(payload: str) -> None:
    (message,) = _msgs(can_lines("7E8", payload))
    assert parse_supported(message) is None


# --- Inhalte -----------------------------------------------------------------------


def test_calibration_ids_from_the_datasheet_example() -> None:
    # ELM327DSJ S. 43: zwei Steuergeräte, Frames vermischt, mit Headern zugeordnet
    by_ecu: dict[str, list[EcuMessage]] = {}
    for message in _msgs(ECU_INFO_HEADERS["0904"]):
        by_ecu.setdefault(message.header, []).append(message)
    assert parse_calibration_ids(by_ecu["7E8"]) == ("56028949AC",)
    assert parse_calibration_ids(by_ecu["7E9"]) == ("56028954AC",)


def test_several_calibration_ids_and_empty_ones_skipped() -> None:
    first = b"ABC123".ljust(16, b"\0").hex()
    empty = "00" * 16
    second = b"XYZ-9 7".ljust(16, b"\0").hex()
    messages = _msgs(can_lines("18DAF110", "490403" + first + empty + second))
    assert parse_calibration_ids(messages) == ("ABC123", "XYZ-9 7")


def test_unprintable_characters_are_marked() -> None:
    calid = (b"AB\x01C" + b"\xff").ljust(16, b"\0").hex()
    assert parse_calibration_ids(_msgs(can_lines("7E8", "490401" + calid))) == ("AB?C?",)


def test_cvns_as_hex() -> None:
    messages = _msgs(can_lines("7E8", "4906021234ABCD00000F3A"))
    assert parse_cvns(messages) == ("1234ABCD", "00000F3A")


def test_cvns_legacy_by_message_number() -> None:
    # Nummern vertauscht angekommen: nach Nummer sortiert
    text = "48 6B 10 49 06 02 00 00 0F 3A 00\r48 6B 10 49 06 01 12 34 AB CD 00"
    assert parse_cvns(_msgs(text)) == ("1234ABCD", "00000F3A")


def test_ecu_name() -> None:
    name = b"ECM\0-EngineControl".ljust(20, b"\0").hex()
    assert parse_ecu_name(_msgs(can_lines("7E8", "490A01" + name))) == "ECM-EngineControl"


def test_empty_ecu_name_is_none() -> None:
    assert parse_ecu_name(_msgs(can_lines("7E8", "490A01" + "00" * 20))) is None


@pytest.mark.parametrize(
    ("payload", "parse"),
    [
        ("490402" + "41" * 16, parse_calibration_ids),  # zwei angekündigt, eine geliefert
        ("4906011234AB", parse_cvns),  # CVN zu kurz
        ("4906", parse_cvns),  # ohne Anzahl
        ("490A02" + "41" * 40, parse_ecu_name),  # zwei Namen
        ("490401" + "41" * 15, parse_calibration_ids),
    ],
)
def test_length_must_match_the_count(payload: str, parse: object) -> None:
    with pytest.raises(ValueError, match="49"):
        parse(_msgs(can_lines("7E8", payload)))  # type: ignore[operator]


def test_legacy_gaps_and_duplicates_are_rejected() -> None:
    gap = "48 6B 10 49 06 01 12 34 AB CD 00\r48 6B 10 49 06 03 00 00 0F 3A 00"
    duplicate = "48 6B 10 49 06 01 12 34 AB CD 00\r48 6B 10 49 06 01 12 34 AB CD 00"
    for text in (gap, duplicate):
        with pytest.raises(ValueError, match="49 06"):
            parse_cvns(_msgs(text))


# --- Überwachungszähler ----------------------------------------------------------------


def test_spark_performance_counters() -> None:
    (message,) = _msgs(ECU_INFO_HEADERS["0908"])
    ipt = parse_performance(0x08, [message])
    assert not ipt.compression_ignition
    assert (ipt.obd_conditions, ipt.ignition_cycles) == (20, 50)
    assert [c.key for c in ipt.counters] == [key for key, _ in SPARK_COUNTERS]
    assert ipt.counters[0] == PerformanceCounter("catalyst_b1", 15, 20)
    assert ipt.counters[0].ratio == 0.75
    assert ipt.counters[1].ratio is None  # Bank 2: keine Bedingungen, keine Quote
    assert ipt.counters[0].name == "Katalysator Bank 1"
    assert len(IPT_SPARK) == 20


def test_compression_counters_with_16_values() -> None:
    # ältere Diesel: 16 Werte, also ohne das Paar „Kraftstoffsystem“
    values = "".join(f"{v:04X}" for v in range(1, 17))
    ipt = parse_performance(0x0B, _msgs(can_lines("18DAF110", "490B10" + values)))
    assert ipt.compression_ignition
    assert [c.key for c in ipt.counters] == [key for key, _ in COMPRESSION_COUNTERS[:7]]
    assert ipt.counters[-1] == PerformanceCounter("boost_pressure", 15, 16)


@pytest.mark.parametrize("count", [1, 3, 22])
def test_counter_count_must_fit_j1979(count: int) -> None:
    values = "0001" * count
    with pytest.raises(ValueError, match="J1979"):
        parse_performance(0x08, _msgs(can_lines("7E8", f"4908{count:02X}{values}")))


# --- Lesen ------------------------------------------------------------------------------


def test_read_ecu_info_per_control_unit() -> None:
    ecus, _ = _read(ECU_INFO_HEADERS)
    assert ecus is not None
    engine, gearbox = ecus
    assert engine.ecu == "7E8"
    assert engine.name == "ECM-EngineControl"
    assert engine.calibration_ids == ("56028949AC",)
    assert engine.cvns == ("1234ABCD",)
    assert engine.performance is not None
    assert engine.performance.ignition_cycles == 50
    assert engine.supported == (0x02, 0x04, 0x06, 0x08, 0x0A)
    assert gearbox == EcuInfo(
        ecu="7E9",
        name="TCM-TransmissionCtl",
        calibration_ids=("56028954AC",),
        cvns=("00000F3A",),
        supported=(0x04, 0x06, 0x0A),
    )


def test_headers_are_switched_off_again() -> None:
    _, transport = _read(ECU_INFO_HEADERS)
    assert transport.sent[0] == "ATH1"
    assert transport.sent[-1] == "ATH0"
    assert not transport.headers


def test_no_mode_09_answer_is_none() -> None:
    ecus, transport = _read({})  # 0900: NO DATA
    assert ecus is None
    assert transport.sent == ["ATH1", "0900", "ATH0"]


def test_rejected_0900_is_none() -> None:
    ecus, _ = _read({"0900": can_lines("7E8", "7F0911")})
    assert ecus is None


def test_unreadable_0900_is_an_elm_error_and_headers_go_off() -> None:
    transport = FakeTransport(CAN_CAR_FULL, headers_on={"0900": "ZZ ZZ"})
    with pytest.raises(ElmError, match="0900"):
        read_ecu_info(Elm327(transport))
    assert transport.sent[-1] == "ATH0"


def test_a_broken_part_leaves_only_that_part_empty(caplog: pytest.LogCaptureFixture) -> None:
    headers = {
        **ECU_INFO_HEADERS,
        "0906": can_lines("7E8", "490602" + "1234ABCD") + "\r" + can_lines("7E9", "7F0612"),
        "090A": "CAN ERROR",
    }
    with caplog.at_level("WARNING", logger="obd_diag.protocol.vehicle_info"):
        ecus, _ = _read(headers)
    assert ecus is not None
    assert [e.cvns for e in ecus] == [(), ()]  # 7E8 kaputt, 7E9 lehnt ab
    assert [e.name for e in ecus] == [None, None]  # 090A unlesbar
    assert [e.calibration_ids for e in ecus] == [("56028949AC",), ("56028954AC",)]
    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "0906 von 7E8" in messages
    assert "090A nicht lesbar" in messages


def test_lost_connection_aborts() -> None:
    class Unplugged(FakeTransport):
        def write(self, data: bytes) -> None:
            if data == b"0904\r":
                raise TransportError("Verbindung unterbrochen")
            super().write(data)

    with pytest.raises(TransportError):
        read_ecu_info(Elm327(Unplugged(CAN_CAR_FULL, headers_on=ECU_INFO_HEADERS)))


# --- BUFFER FULL (W177, 2026-10-16) --------------------------------------------------

_SUPPORTED = "18DAF15906490010400000\r18DAF15806490010400000"  # beide: 04 (CALID), 0A (Name)
_NAME_58 = can_lines("18DAF158", "490A01" + "45434D002D456E67696E65436F6E74726F6C0000").replace(
    " ", ""
)
_NAME_59 = can_lines("18DAF159", "490A01" + "50434D002D506F776572747261696E4374726C00").replace(
    " ", ""
)


def _replay(*exchanges: tuple[str, str]) -> Elm327:
    entries = [(">>", b"ATH1\r"), ("<<", b"OK\r\r>")]
    for cmd, answer in exchanges:
        entries += [(">>", f"{cmd}\r".encode()), ("<<", f"{answer}\r\r>".encode())]
    entries += [(">>", b"ATH0\r"), ("<<", b"OK\r\r>")]
    return Elm327(ReplayTransport(entries))


def _cut(lines: str, keep: int, tail: str) -> str:
    """Die ersten ``keep`` Zeilen, ein abgeschnittener Rest und BUFFER FULL."""
    return "\r".join([*lines.split("\r")[:keep], tail, "BUFFER FULL"])


def test_buffer_full_keeps_complete_answers_and_retries_once() -> None:
    both = f"{_NAME_59}\r{_NAME_58}"
    elm = _replay(
        ("0900", _SUPPORTED),
        ("0904", "NO DATA"),
        ("090A", _cut(both, 5, "18DAF")),  # 59 vollständig (4 Frames), 58 nur erster Frame
        ("090A", _cut(_NAME_58 + "\r" + _NAME_59, 4, "18DAF15")),  # jetzt 58 vollständig
    )
    infos = read_ecu_info(elm)
    assert infos is not None
    assert {i.ecu: i.name for i in infos} == {
        "18DAF158": "ECM-EngineControl",
        "18DAF159": "PCM-PowertrainCtrl",
    }


def test_buffer_full_retry_does_not_replace_complete_answers() -> None:
    other_59 = _NAME_59.replace("506F776572", "506F776573")  # „Powes…“: darf nicht gelten
    elm = _replay(
        ("0900", _SUPPORTED),
        ("0904", "NO DATA"),
        ("090A", _cut(f"{_NAME_59}\r{_NAME_58}", 5, "18DAF158")),
        ("090A", _cut(f"{other_59}\r{_NAME_58}", 4, "18DAF15")),  # 58 wieder unvollständig
    )
    infos = read_ecu_info(elm)
    assert infos is not None
    assert {i.ecu: i.name for i in infos} == {"18DAF158": None, "18DAF159": "PCM-PowertrainCtrl"}


def test_buffer_full_with_all_expected_complete_is_not_retried() -> None:
    both = f"{_NAME_59}\r{_NAME_58}"
    elm = _replay(
        ("0900", _SUPPORTED),
        ("0904", "NO DATA"),
        ("090A", f"{both}\r18DAF15A10\rBUFFER FULL"),  # 18DAF15A meldet 0A nicht
    )
    infos = read_ecu_info(elm)
    assert infos is not None
    assert all(i.name for i in infos)


def test_missing_ecu_without_buffer_full_is_not_retried() -> None:
    elm = _replay(("0900", _SUPPORTED), ("0904", "NO DATA"), ("090A", _NAME_59))
    infos = read_ecu_info(elm)
    assert infos is not None
    assert {i.ecu: i.name for i in infos} == {"18DAF158": None, "18DAF159": "PCM-PowertrainCtrl"}


def test_buffer_full_with_a_gap_is_not_salvaged() -> None:
    """Fehlt ein Frame mitten in der Antwort, bleibt es ein Fehler: lieber nichts als
    falsch zusammengesetzt."""
    lines = f"{_NAME_59}\r{_NAME_58}".split("\r")
    del lines[1]
    elm = _replay(
        ("0900", _SUPPORTED), ("0904", "NO DATA"), ("090A", "\r".join([*lines, "BUFFER FULL"]))
    )
    infos = read_ecu_info(elm)
    assert infos is not None
    assert all(i.name is None for i in infos)
