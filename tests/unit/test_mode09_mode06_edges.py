"""Randfälle zu Mode 09 je Steuergerät (``protocol/vehicle_info.py``) und Mode 06
(``protocol/monitor_tests.py``): ISO-TP mit Headern (11 und 29 Bit), Füllbytes,
mehrere CALIDs/CVNs, Überwachungszähler nach J1979 InfoType 08/0B, UASIDs mit
Vorzeichen, Grenzen genau am Rand, Teilausfälle, ``ATH0``-Fehler, Kommandozeile,
Sitzung und PDF."""

import dataclasses
import json
import re
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pypdf import PdfReader

from obd_diag import cli
from obd_diag.cli import main
from obd_diag.data.dtc_catalog import DtcCatalog
from obd_diag.export import report
from obd_diag.i18n import set_language
from obd_diag.protocol.elm327 import Elm327
from obd_diag.protocol.headers import EcuMessage, parse_header_response
from obd_diag.protocol.monitor_tests import (
    UNITS,
    TestResult,
    TestStatus,
    read_test_results,
)
from obd_diag.protocol.monitor_tests import parse_results as parse_mode06_results
from obd_diag.protocol.monitor_tests import parse_supported as parse_mids
from obd_diag.protocol.vehicle_info import (
    COMPRESSION_COUNTERS,
    SPARK_COUNTERS,
    EcuInfo,
    InUsePerformance,
    PerformanceCounter,
    parse_calibration_ids,
    parse_cvns,
    parse_ecu_name,
    parse_performance,
    parse_supported,
    read_ecu_info,
)
from obd_diag.services.session import (
    Session,
    run_diagnosis,
    session_from_dict,
    session_to_dict,
)
from obd_diag.transport import TransportError
from tests.fakes import (
    CAN_CAR_FULL,
    ECU_INFO_HEADERS,
    MONITOR_TESTS_HEADERS,
    FakeTransport,
    can_lines,
)
from tests.samples import minimal_session


def _msgs(text: str) -> list[EcuMessage]:
    return parse_header_response(text)


def _text(text: str, size: int) -> str:
    return text.encode("ascii").ljust(size, b"\0").hex()


def _padded(lines: str, pad: str = "AA") -> str:
    """Füllt jede CAN-Zeile auf 8 Datenbytes auf, wie es Steuergeräte tun (ISO 15765-2)."""
    out = []
    for line in lines.split("\r"):
        header, *data = line.split()
        out.append(" ".join([header, *data, *[pad] * (8 - len(data))]))
    return "\r".join(out)


def _join(*parts: str) -> str:
    return "\r".join(parts)


def _read09(headers: dict[str, str]) -> tuple[list[EcuInfo] | None, FakeTransport]:
    transport = FakeTransport(CAN_CAR_FULL, headers_on=headers)
    return read_ecu_info(Elm327(transport)), transport


def _read06(headers: dict[str, str]) -> tuple[list[TestResult] | None, FakeTransport]:
    transport = FakeTransport(CAN_CAR_FULL, headers_on=headers)
    return read_test_results(Elm327(transport)), transport


# --- Mode 09: unterstützte InfoTypes -----------------------------------------------------


@pytest.mark.parametrize(
    ("mask", "expected"),
    [
        ("80000000", (0x01,)),  # Bit 7 von A = InfoType 01
        ("00000001", (0x20,)),  # letztes Bit = InfoType 20
        ("FFFFFFFF", tuple(range(1, 0x21))),
        ("00000000", ()),
    ],
)
def test_supported_info_types_bit_boundaries(mask: str, expected: tuple[int, ...]) -> None:
    (message,) = _msgs(can_lines("18DAF110", "4900" + mask))
    assert parse_supported(message) == expected


def test_supported_info_types_with_padding_bytes() -> None:
    # Einzel-Frame 06 49 00 …, danach Füllbytes bis 8 Bytes: werden abgeschnitten
    (message,) = _msgs("7E8 06 49 00 54 40 00 00 55")
    assert parse_supported(message) == (0x02, 0x04, 0x06, 0x0A)


def test_legacy_supported_with_wrong_length_is_none() -> None:
    # Legacy: 49 00 01 + vier Bytes; hier nur zwei Datenbytes nach der Nummer (+ Prüfsumme)
    (message,) = _msgs("48 6B 10 49 00 01 54 40 9A")
    assert parse_supported(message) is None


# --- Mode 09: Inhalte -----------------------------------------------------------------


def test_seven_calibration_ids_wrap_the_frame_counter() -> None:
    # 3 + 7·16 = 115 Bytes: erster Frame + 16 Folge-Frames, Nummer läuft 1…F, 0
    calids = [f"CAL{n:02d}-{'X' * n}" for n in range(7)]
    payload = "490407" + "".join(_text(c, 16) for c in calids)
    lines = can_lines("7E8", payload)
    assert "7E8 20 " in lines  # Folge-Frame mit Nummer 0 nach F
    assert parse_calibration_ids(_msgs(lines)) == tuple(calids)


def test_calibration_id_with_full_sixteen_characters_and_padding() -> None:
    lines = _padded(can_lines("18DAF110", "490401" + b"0123456789ABCDEF".hex()), "55")
    assert parse_calibration_ids(_msgs(lines)) == ("0123456789ABCDEF",)


def test_calibration_id_spaces_trimmed_and_inner_zero_bytes_dropped() -> None:
    raw = (b"  AB\0CD  ").ljust(16, b"\0").hex()
    assert parse_calibration_ids(_msgs(can_lines("7E8", "490401" + raw))) == ("ABCD",)


@pytest.mark.parametrize(
    ("payload", "parse"),
    [("490400", parse_calibration_ids), ("490600", parse_cvns)],
)
def test_zero_items_announced_is_empty(payload: str, parse: object) -> None:
    assert parse(_msgs(can_lines("7E8", payload))) == ()  # type: ignore[operator]


def test_several_cvns_keep_leading_zeros_over_several_frames() -> None:
    # vier CVN = 3 + 16 Bytes: mehrteilig, auf 8 Bytes aufgefüllt
    payload = "490604" + "00000000" + "0000FFFF" + "DEADBEEF" + "0A0B0C0D"
    lines = _padded(can_lines("18DAF118", payload), "00")
    assert parse_cvns(_msgs(lines)) == ("00000000", "0000FFFF", "DEADBEEF", "0A0B0C0D")


def test_cvn_single_frame_exactly_seven_bytes() -> None:
    assert parse_cvns(_msgs("7E8 07 49 06 01 12 34 AB CD")) == ("1234ABCD",)


def test_ecu_name_with_padding_bytes_after_the_message() -> None:
    lines = _padded(can_lines("7E9", "490A01" + _text("TCM\0-TransmissionCtl", 20)), "AA")
    assert parse_ecu_name(_msgs(lines)) == "TCM-TransmissionCtl"


def test_legacy_calibration_id_from_four_numbered_messages() -> None:
    # J1850/ISO 9141: je Nachricht 49 04 <Nr> + 4 Bytes; hier in falscher Reihenfolge
    calid = b"JM6Z18881A".ljust(16, b"\0")
    lines = [
        "48 6B 10 49 04 {:02X} {} 00".format(n, calid[4 * (n - 1) : 4 * n].hex(" ").upper())
        for n in (3, 1, 4, 2)
    ]
    assert parse_calibration_ids(_msgs("\r".join(lines))) == ("JM6Z18881A",)


def test_legacy_messages_must_start_at_one() -> None:
    text = "48 6B 10 49 06 02 12 34 AB CD 00\r48 6B 10 49 06 03 00 00 0F 3A 00"
    with pytest.raises(ValueError, match="unvollständig"):
        parse_cvns(_msgs(text))


def test_two_can_answers_from_one_ecu_are_rejected() -> None:
    text = can_lines("7E8", "4906011234ABCD") + "\r" + can_lines("7E8", "4906011234ABCD")
    with pytest.raises(ValueError, match="mehrere Antworten 49 06"):
        parse_cvns(_msgs(text))


# --- Mode 09: Überwachungszähler ----------------------------------------------------------


def _counter_payload(info_type: int, values: list[int]) -> str:
    return f"49{info_type:02X}{len(values):02X}" + "".join(f"{v:04X}" for v in values)


def test_spark_counters_with_18_values() -> None:
    values = list(range(100, 118))
    ipt = parse_performance(0x08, _msgs(can_lines("7E8", _counter_payload(0x08, values))))
    assert (ipt.obd_conditions, ipt.ignition_cycles) == (100, 101)
    assert [c.key for c in ipt.counters] == [k for k, _ in SPARK_COUNTERS[:8]]
    assert ipt.counters[-1] == PerformanceCounter("secondary_o2_b1", 116, 117)


def test_compression_counters_with_18_values_end_with_fuel() -> None:
    values = list(range(1, 19))
    ipt = parse_performance(0x0B, _msgs(can_lines("18DAF110", _counter_payload(0x0B, values))))
    assert ipt.compression_ignition
    assert [c.key for c in ipt.counters] == [k for k, _ in COMPRESSION_COUNTERS]
    assert ipt.counters[-1] == PerformanceCounter("fuel", 17, 18)
    assert ipt.counters[-1].name == "Kraftstoffsystem"


def test_compression_with_20_values_has_more_pairs_than_j1979() -> None:
    with pytest.raises(ValueError, match="20 Zählerwerte"):
        parse_performance(0x0B, _msgs(can_lines("7E8", _counter_payload(0x0B, [1] * 20))))


def test_only_obdcond_and_igncntr_gives_no_counters() -> None:
    ipt = parse_performance(0x08, _msgs(can_lines("7E8", _counter_payload(0x08, [3, 7]))))
    assert ipt == InUsePerformance(False, 3, 7, ())


def test_counters_are_unsigned_16_bit() -> None:
    values = [0xFFFF, 0x8000, 0xFFFF, 0x8000]
    ipt = parse_performance(0x08, _msgs(can_lines("7E8", _counter_payload(0x08, values))))
    assert (ipt.obd_conditions, ipt.ignition_cycles) == (65535, 32768)
    assert ipt.counters[0] == PerformanceCounter("catalyst_b1", 65535, 32768)
    assert ipt.counters[0].ratio == pytest.approx(65535 / 32768)


def test_counter_bytes_must_match_the_count() -> None:
    # 20 Werte angekündigt (0x14), aber nur 19 geliefert
    payload = "490814" + "0001" * 19
    with pytest.raises(ValueError, match="20 Elemente zu 2 Bytes angekündigt, 38 Bytes"):
        parse_performance(0x08, _msgs(can_lines("7E8", payload)))


@given(st.lists(st.integers(0, 0xFFFF), min_size=1, max_size=len(SPARK_COUNTERS)))
def test_spark_counter_pairs_round_trip(pairs: list[int]) -> None:
    values = [5, 9] + [v for p in pairs for v in (p, 0xFFFF - p)]
    ipt = parse_performance(0x08, _msgs(can_lines("7E8", _counter_payload(0x08, values))))
    assert [(c.completed, c.conditions) for c in ipt.counters] == [(p, 0xFFFF - p) for p in pairs]
    assert [c.key for c in ipt.counters] == [k for k, _ in SPARK_COUNTERS[: len(pairs)]]


# --- Mode 09: Lesen ----------------------------------------------------------------------


def _interleave(first: str, second: str) -> str:
    """Zeilen zweier Antworten abwechselnd, wie sie gleichzeitig auf dem Bus eintreffen."""
    a, b = first.split("\r"), second.split("\r")
    mixed = [line for pair in zip(a, b, strict=False) for line in pair]
    return "\r".join(mixed + a[len(b) :] + b[len(a) :])


# Zwei Steuergeräte mit 29-Bit-Headern (18DAF110 Motor, 18DAF118 Getriebe), Motor Diesel
_DIESEL = list(range(10, 28))
HEADERS_29 = {
    "0900": _join(
        can_lines("18DAF118", "490054400000"),  # 02, 04, 06, 0A
        can_lines("18DAF110", "490054600000"),  # 02, 04, 06, 0A, 0B
    ),
    # zwei CALIDs vom Motor (35 Bytes, mehrteilig) und eine vom Getriebe, Frames vermischt
    "0904": _interleave(
        _padded(can_lines("18DAF110", "490402" + _text("A6519006400", 16) + _text("SW 0815", 16))),
        _padded(can_lines("18DAF118", "490401" + _text("A0009012345", 16))),
    ),
    "0906": _join(
        can_lines("18DAF110", "49060211223344" + "55667788"),
        can_lines("18DAF118", "7F0612"),
    ),
    "090A": _join(
        can_lines("18DAF110", "490A01" + _text("ECM\0-EngineControl", 20)),
        can_lines("18DAF118", "490A01" + "00" * 20),  # leerer Name
    ),
    "090B": can_lines("18DAF110", _counter_payload(0x0B, _DIESEL)),
}


def test_read_29_bit_two_ecus_with_interleaved_multi_frames() -> None:
    ecus, transport = _read09(HEADERS_29)
    assert ecus is not None
    engine, gearbox = ecus
    assert engine.ecu == "18DAF110"
    assert engine.calibration_ids == ("A6519006400", "SW 0815")
    assert engine.cvns == ("11223344", "55667788")
    assert engine.name == "ECM-EngineControl"
    assert engine.performance is not None
    assert engine.performance.compression_ignition
    assert engine.performance.counters[-1] == PerformanceCounter("fuel", 26, 27)
    assert engine.supported == (0x02, 0x04, 0x06, 0x0A, 0x0B)
    assert gearbox == EcuInfo(
        "18DAF118", None, ("A0009012345",), (), None, (0x02, 0x04, 0x06, 0x0A)
    )
    # 0902 (FIN) liest read_vin; 0908 meldet niemand: nicht gefragt
    assert transport.sent == ["ATH1", "0900", "0904", "0906", "090A", "090B", "ATH0"]


def test_29_bit_headers_with_spaces_between_id_bytes() -> None:
    headers = {
        "0900": "18 DA F1 10 06 49 00 10 00 00 00 00",  # nur 04
        "0904": "18 DA F1 10 10 13 49 04 01 41 42 43\r18 DA F1 10 21 00 00 00 00 00 00 00\r"
        "18 DA F1 10 22 00 00 00 00 00 00 00",
    }
    ecus, transport = _read09(headers)
    assert ecus == [EcuInfo("18DAF110", calibration_ids=("ABC",), supported=(0x04,))]
    assert transport.sent == ["ATH1", "0900", "0904", "ATH0"]


def test_info_types_order_follows_read_info_types() -> None:
    headers = {"0900": can_lines("7E8", "490055600000")}  # 02 04 06 08 0A 0B
    _, transport = _read09(headers)
    assert transport.sent == ["ATH1", "0900", "0904", "0906", "0908", "090A", "090B", "ATH0"]


def test_answers_from_ecus_not_in_0900_are_ignored() -> None:
    headers = {
        "0900": can_lines("7E8", "490040000000"),  # nur 02
        "0904": can_lines("7E9", "490401" + _text("FREMD", 16)),
    }
    ecus, transport = _read09(headers)
    assert ecus == [EcuInfo("7E8", supported=(0x02,))]
    assert transport.sent == ["ATH1", "0900", "ATH0"]  # 0904 meldet niemand


def test_info_type_answered_without_support_bit_is_still_used() -> None:
    # 7E9 meldet 04 nicht, antwortet aber trotzdem: die Angabe wird übernommen
    headers = {
        "0900": _join(can_lines("7E8", "490010000000"), can_lines("7E9", "490000000000")),
        "0904": _join(
            can_lines("7E8", "490401" + _text("E", 16)), can_lines("7E9", "490401" + _text("G", 16))
        ),
    }
    ecus, _ = _read09(headers)
    assert ecus is not None
    assert [(e.ecu, e.calibration_ids, e.supported) for e in ecus] == [
        ("7E8", ("E",), (0x04,)),
        ("7E9", ("G",), ()),
    ]


def test_rejection_is_silent_and_no_data_leaves_everything_empty(
    caplog: pytest.LogCaptureFixture,
) -> None:
    headers = {**ECU_INFO_HEADERS, "0904": can_lines("7E8", "7F0912"), "0906": "NO DATA"}
    with caplog.at_level("WARNING", logger="obd_diag.protocol.vehicle_info"):
        ecus, _ = _read09(headers)
    assert ecus is not None
    assert [(e.calibration_ids, e.cvns) for e in ecus] == [((), ()), ((), ())]
    assert [e.name for e in ecus] == ["ECM-EngineControl", "TCM-TransmissionCtl"]
    assert caplog.records == []  # Ablehnung und NO DATA sind keine Fehler


def test_missing_consecutive_frame_drops_only_that_info_type(
    caplog: pytest.LogCaptureFixture,
) -> None:
    lines = ECU_INFO_HEADERS["0904"].split("\r")
    lines.remove("7E8 21 32 38 39 34 39 41 43")  # Folge-Frame 1 von 7E8 fehlt
    headers = {**ECU_INFO_HEADERS, "0904": "\r".join(lines)}
    with caplog.at_level("WARNING", logger="obd_diag.protocol.vehicle_info"):
        ecus, _ = _read09(headers)
    assert ecus is not None
    # die ganze Antwort 0904 ist unlesbar, also für beide Steuergeräte leer
    assert [e.calibration_ids for e in ecus] == [(), ()]
    assert [e.cvns for e in ecus] == [("1234ABCD",), ("00000F3A",)]
    assert "0904 nicht lesbar" in caplog.text


def test_duplicate_answer_from_one_ecu_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    headers = {
        **ECU_INFO_HEADERS,
        "0906": _join(
            can_lines("7E8", "4906011234ABCD"),
            can_lines("7E8", "4906011234ABCD"),
            can_lines("7E9", "4906010000 0F3A"),
        ),
    }
    with caplog.at_level("WARNING", logger="obd_diag.protocol.vehicle_info"):
        ecus, _ = _read09(headers)
    assert ecus is not None
    assert [e.cvns for e in ecus] == [(), ("00000F3A",)]
    assert "0906 von 7E8: mehrere Antworten 49 06" in caplog.text


def test_broken_counters_leave_the_rest(caplog: pytest.LogCaptureFixture) -> None:
    headers = {**ECU_INFO_HEADERS, "0908": can_lines("7E8", _counter_payload(0x08, [1, 2, 3]))}
    with caplog.at_level("WARNING", logger="obd_diag.protocol.vehicle_info"):
        ecus, _ = _read09(headers)
    assert ecus is not None
    assert ecus[0].performance is None
    assert ecus[0].name == "ECM-EngineControl"
    assert "0908 von 7E8: 49 08: 3 Zählerwerte passen nicht zu J1979" in caplog.text


class _NoAth0(FakeTransport):
    """Adapter, der ``ATH0`` nach ``ATH1`` nicht mehr annimmt (Header bleiben an)."""

    def __init__(self, headers: dict[str, str]) -> None:
        super().__init__(CAN_CAR_FULL, headers_on=headers)

    def write(self, data: bytes) -> None:
        was_on = self.headers
        super().write(data)
        if data == b"ATH0\r" and was_on:
            self.headers = True
            self._pending = b"?\r\r>"


@pytest.mark.parametrize("headers", [ECU_INFO_HEADERS, {}], ids=["mit Antwort", "NO DATA"])
def test_mode09_ath0_failure_is_a_transport_error(headers: dict[str, str]) -> None:
    transport = _NoAth0(headers)
    with pytest.raises(TransportError, match="ATH0"):
        read_ecu_info(Elm327(transport))
    assert transport.sent[-1] == "ATH0"


def test_mode09_ath0_failure_wins_over_an_unreadable_0900() -> None:
    # Ohne Header weiterzulesen wäre falsch: TransportError statt des ElmError von 0900
    with pytest.raises(TransportError):
        read_ecu_info(Elm327(_NoAth0({"0900": "ZZ"})))


def test_mode09_lost_connection_on_0900_aborts() -> None:
    class Unplugged(FakeTransport):
        def write(self, data: bytes) -> None:
            if data == b"0900\r":
                raise TransportError("Verbindung unterbrochen")
            super().write(data)

    with pytest.raises(TransportError, match="unterbrochen"):
        read_ecu_info(Elm327(Unplugged(CAN_CAR_FULL, headers_on=ECU_INFO_HEADERS)))


# --- Mode 06: unterstützte MIDs ------------------------------------------------------------


def test_supported_mids_at_range_end_stop_at_ff() -> None:
    (message,) = _msgs(can_lines("7E8", "46E0FFFFFFFF"))
    mids = parse_mids(message, 0xE0)
    assert mids is not None
    assert mids[0] == 0xE1 and mids[-1] == 0xFF and len(mids) == 31


def test_supported_mids_rejection_is_none() -> None:
    (message,) = _msgs(can_lines("7E8", "7F0612"))
    assert parse_mids(message, 0x00) is None


def test_range_mids_are_not_queried_as_tests() -> None:
    # 0600 meldet 01 und 20 (weiter), 0620 meldet 21 und 40 (weiter), 0640 nur 41
    headers = {
        "0600": can_lines("7E8", "460080000001"),
        "0620": can_lines("7E8", "462080000001"),
        "0640": can_lines("7E8", "464080000000"),
    }
    results, transport = _read06(headers)
    assert results == []  # alle MIDs ohne Antwort (Fake: OK, keine 46-Nachricht)
    assert transport.sent == ["ATH1", "0600", "0620", "0640", "0601", "0621", "0641", "ATH0"]


def test_next_range_reported_by_another_ecu_is_followed() -> None:
    headers = {
        "0600": _join(can_lines("7E8", "460080000000"), can_lines("7E9", "460000000001")),
        "0620": can_lines("7E9", "462080000000"),
    }
    _, transport = _read06(headers)
    assert transport.sent == ["ATH1", "0600", "0620", "0601", "0621", "ATH0"]


def test_only_rejections_for_0600_is_none() -> None:
    results, transport = _read06({"0600": can_lines("7E8", "7F0612")})
    assert results is None
    assert transport.sent == ["ATH1", "0600", "ATH0"]


# --- Mode 06: Ergebnisse ---------------------------------------------------------------


def test_three_records_over_iso_tp_with_padding() -> None:
    payload = (
        "46"
        + "A2 0B 24 0003 0000 000A"
        + "A2 0C 24 0000 0000 0000"
        + "A2 85 FE FFFC FFF0 0010"  # -1,0 Pa in -4,0 … 4,0
    ).replace(" ", "")
    (message,) = _msgs(_padded(can_lines("18DAF110", payload), "55"))
    first, second, third = parse_mode06_results(message, 0xA2)
    assert (first.tid, first.value, first.status) == (0x0B, 3, TestStatus.PASSED)
    assert second.status is TestStatus.NO_RESULT
    assert third.ecu == "18DAF110"
    assert (third.value, third.minimum, third.maximum) == (-1.0, -4.0, 4.0)
    assert third.unit == "Pa"
    assert third.status is TestStatus.PASSED


def test_second_block_with_other_mid_drops_the_whole_message() -> None:
    payload = "46" + "218024000100000002" + "228024000100000002"
    (message,) = _msgs(can_lines("7E8", payload))
    with pytest.raises(ValueError, match="0621: Ergebnis für MID 22"):
        parse_mode06_results(message, 0x21)


@pytest.mark.parametrize(
    ("uasid", "raw", "value", "unit"),
    [
        (0x39, 0x0000, -327.68, "%"),  # 0,01 % mit Versatz -327,68 %
        (0x39, 0x8000, 0.0, "%"),
        (0x39, 0xFFFF, 327.67, "%"),
        (0x16, 0x0000, -40.0, "°C"),
        (0x16, 0xFFFF, 6513.5, "°C"),
        (0x96, 0xFF9C, -10.0, "°C"),  # mit Vorzeichen, ohne Versatz
        (0x07, 0xFFFF, 16383.75, "1/min"),
        (0x81, 0x8000, -32768, ""),
        (0x81, 0x7FFF, 32767, ""),
        (0x01, 0x8000, 32768, ""),  # ohne Vorzeichen: dieselben Bytes positiv
        (0xFE, 0xFFFC, -1.0, "Pa"),
        (0x8C, 0xFF38, -2.0, "V"),
        (0x0A, 0x0001, 0.122, "mV"),
    ],
)
def test_scaling_per_uasid(uasid: int, raw: int, value: float, unit: str) -> None:
    test = TestResult("7E8", 0x01, 0x01, uasid, raw, 0, 0)
    assert test.value == pytest.approx(value)
    assert test.unit == unit


@pytest.mark.parametrize(
    ("uasid", "value", "low", "high", "status"),
    [
        # mit Vorzeichen: genau an der unteren und oberen Grenze bestanden
        (0x81, 0x8000, 0x8000, 0x7FFF, TestStatus.PASSED),
        (0x81, 0x7FFF, 0x8000, 0x7FFF, TestStatus.PASSED),
        (0x81, 0xFFFF, 0xFFFF, 0xFFFF, TestStatus.PASSED),  # -1 in -1 … -1
        (0x81, 0xFFFE, 0xFFFF, 0x0001, TestStatus.FAILED),  # -2 unter -1
        (0x81, 0x0002, 0xFFFF, 0x0001, TestStatus.FAILED),  # 2 über 1
        (0x81, 0x0000, 0xFFFF, 0x0001, TestStatus.PASSED),
        # ohne Vorzeichen sind 8000 > 7FFF: keine Zahl liegt dazwischen
        (0x01, 0x8000, 0x8000, 0x7FFF, TestStatus.FAILED),
        (0x01, 0xFFFF, 0x0000, 0xFFFF, TestStatus.PASSED),
        # nur alle drei 0 heißt „kein Ergebnis“
        (0x24, 0x0000, 0x0000, 0x0001, TestStatus.PASSED),
        (0x24, 0x0001, 0x0000, 0x0000, TestStatus.FAILED),
        (0x81, 0x0000, 0x0000, 0x0000, TestStatus.NO_RESULT),
        (0x55, 0x0000, 0x0000, 0x0000, TestStatus.NO_RESULT),  # auch bei unbekannter UASID
    ],
)
def test_status_exactly_at_the_limits(
    uasid: int, value: int, low: int, high: int, status: TestStatus
) -> None:
    assert TestResult("7E8", 0x21, 0x80, uasid, value, low, high).status is status


@pytest.mark.parametrize("uasid", [0x00, 0x42, 0x7F, 0x80, 0x88, 0xFF])
def test_unknown_uasid_has_no_scaling(uasid: int) -> None:
    test = TestResult("7E8", 0x21, 0x80, uasid, 5, 1, 9)
    assert (test.value, test.minimum, test.maximum, test.unit, test.decimals) == (
        None,
        None,
        None,
        "",
        0,
    )
    assert test.status is TestStatus.PASSED


_RAW = st.integers(0, 0xFFFF)


@given(st.sampled_from(sorted(UNITS)), _RAW, _RAW, _RAW)
def test_status_agrees_with_scaled_values(uasid: int, value: int, low: int, high: int) -> None:
    test = TestResult("7E8", 0x21, 0x80, uasid, value, low, high)
    signed = uasid >= 0x80
    number = int.from_bytes(value.to_bytes(2), signed=signed)
    unit = UNITS[uasid]
    assert test.value == pytest.approx(number * unit.scale + unit.offset)
    if value == low == high == 0:
        assert test.status is TestStatus.NO_RESULT
        return
    assert test.minimum is not None and test.maximum is not None and test.value is not None
    inside = test.minimum <= test.value <= test.maximum
    # bei gleichen Rohwerten können Fließkommafehler den Vergleich nicht kippen
    assert (test.status is TestStatus.PASSED) == inside


@given(st.binary(min_size=0, max_size=40))
def test_parse_results_accepts_only_whole_records(body: bytes) -> None:
    message = EcuMessage(0x7E8, "7E8", bytes([0x46]) + body)
    try:
        results = parse_mode06_results(message, body[0] if body else 0x01)
    except ValueError:
        assert (
            len(body) < 9
            or len(body) % 9
            or any(body[i] != body[0] for i in range(0, len(body), 9))
        )
        return
    assert len(results) == len(body) // 9
    for i, r in enumerate(results):
        block = body[9 * i : 9 * i + 9]
        assert (r.tid, r.uasid, r.value_raw, r.min_raw, r.max_raw) == (
            block[1],
            block[2],
            int.from_bytes(block[3:5]),
            int.from_bytes(block[5:7]),
            int.from_bytes(block[7:9]),
        )


# --- Mode 06: Lesen -----------------------------------------------------------------------

HEADERS_06_29 = {
    "0600": _join(can_lines("18DAF118", "460040000000"), can_lines("18DAF110", "460040000000")),
    "0602": _join(
        can_lines("18DAF118", "4602" + "0181" + "FFFF" + "FFFF" + "0001"),
        can_lines("18DAF110", "7F0612"),
    ),
}


def test_29_bit_results_sorted_and_rejection_skipped() -> None:
    results, transport = _read06(HEADERS_06_29)
    assert results == [TestResult("18DAF118", 0x02, 0x01, 0x81, 0xFFFF, 0xFFFF, 0x0001)]
    assert results[0].value == -1
    assert transport.sent == ["ATH1", "0600", "0602", "ATH0"]


def test_results_of_two_ecus_sorted_by_address_then_mid() -> None:
    headers = {
        "0600": _join(can_lines("7E9", "4600C0000000"), can_lines("7E8", "4600C0000000")),
        "0601": _join(
            can_lines("7E9", "4601" + "0124" + "0001" + "0000" + "0002"),
            can_lines("7E8", "4601" + "0124" + "0002" + "0000" + "0002"),
        ),
        "0602": _join(
            can_lines("7E9", "4602" + "0124" + "0003" + "0000" + "0002"),
            can_lines("7E8", "4602" + "0124" + "0004" + "0000" + "0002"),
        ),
    }
    results, _ = _read06(headers)
    assert results is not None
    assert [(r.ecu, r.mid, r.value_raw, r.status) for r in results] == [
        ("7E8", 0x01, 2, TestStatus.PASSED),
        ("7E8", 0x02, 4, TestStatus.FAILED),
        ("7E9", 0x01, 1, TestStatus.PASSED),
        ("7E9", 0x02, 3, TestStatus.FAILED),
    ]


def test_unreadable_mid_is_skipped_and_the_next_is_read(caplog: pytest.LogCaptureFixture) -> None:
    headers = {**MONITOR_TESTS_HEADERS, "0601": "CAN ERROR"}
    with caplog.at_level("WARNING", logger="obd_diag.protocol.monitor_tests"):
        results, transport = _read06(headers)
    assert results is not None
    assert [(r.mid, r.tid) for r in results] == [(0x21, 0x80)]
    assert "0601 nicht lesbar" in caplog.text
    assert transport.sent == ["ATH1", "0600", "0620", "0601", "0602", "0621", "ATH0"]


def test_garbled_mid_answer_is_skipped(caplog: pytest.LogCaptureFixture) -> None:
    # Folge-Frame ohne ersten Frame: Antwort unlesbar, nur diese MID fehlt
    headers = {**MONITOR_TESTS_HEADERS, "0601": "7E8 21 00 00 00 00 00 00 00"}
    with caplog.at_level("WARNING", logger="obd_diag.protocol.monitor_tests"):
        results, _ = _read06(headers)
    assert results is not None
    assert [r.mid for r in results] == [0x21]
    assert "0601 nicht lesbar: 0601: Folge-Frame ohne ersten Frame" in caplog.text


@pytest.mark.parametrize("headers", [MONITOR_TESTS_HEADERS, {}], ids=["mit Antwort", "NO DATA"])
def test_mode06_ath0_failure_is_a_transport_error(headers: dict[str, str]) -> None:
    transport = _NoAth0(headers)
    with pytest.raises(TransportError, match="ATH0"):
        read_test_results(Elm327(transport))
    assert transport.sent[-1] == "ATH0"


def test_mode06_lost_connection_during_a_mid_aborts() -> None:
    class Unplugged(FakeTransport):
        def write(self, data: bytes) -> None:
            if data == b"0621\r":
                raise TransportError("Verbindung unterbrochen")
            super().write(data)

    with pytest.raises(TransportError, match="unterbrochen"):
        read_test_results(Elm327(Unplugged(CAN_CAR_FULL, headers_on=MONITOR_TESTS_HEADERS)))


def test_mode06_legacy_answer_in_a_later_range_is_none() -> None:
    headers = {"0600": can_lines("7E8", "460080000001"), "0620": "48 6B 10 46 20 80 00 00 00 11"}
    results, transport = _read06(headers)
    assert results is None
    assert transport.sent == ["ATH1", "0600", "0620", "ATH0"]


# --- Diagnose: Teile fallen einzeln aus -------------------------------------------------


def test_diagnosis_keeps_mode06_when_mode09_is_unreadable() -> None:
    transport = FakeTransport(CAN_CAR_FULL, headers_on={**MONITOR_TESTS_HEADERS, "0900": "ZZ ZZ"})
    session = run_diagnosis(Elm327(transport), None)
    assert session.ecus is None
    assert session.tests is not None and len(session.tests) == 3
    assert not transport.headers  # beide Blöcke haben ATH0 gesendet
    blocks = [i for i, cmd in enumerate(transport.sent) if cmd == "ATH1"]
    assert [transport.sent[i + 1] for i in blocks] == ["0900", "0600"]
    assert transport.sent[-1] == "ATH0" and transport.sent[blocks[1] - 1] == "ATH0"


def test_diagnosis_aborts_when_ath0_fails() -> None:
    transport = _NoAth0(ECU_INFO_HEADERS)
    with pytest.raises(TransportError):
        run_diagnosis(Elm327(transport), None)
    assert "0600" not in transport.sent  # nach dem Fehler wird nichts mehr gefragt


# --- Kommandozeile --------------------------------------------------------------------------


@pytest.fixture
def port(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Antworten mit Headern für den Fake-Port; Tests füllen sie vor ``main``."""
    headers: dict[str, str] = {}
    monkeypatch.setattr(
        cli, "SerialTransport", lambda p, b: FakeTransport(CAN_CAR_FULL, headers_on=headers)
    )
    monkeypatch.setattr(DtcCatalog, "default", classmethod(lambda cls: None))
    return headers


def test_cli_ecus_without_details_and_counter_ratios(
    port: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    port.update(
        {
            "0900": _join(can_lines("7E8", "490001000000"), can_lines("7E9", "490040000000")),
            "0908": can_lines("7E8", _counter_payload(0x08, [20, 50, 15, 20, 0, 0])),
        }
    )
    assert main(["ecus"]) == 0
    assert capsys.readouterr().out == (
        "Steuergeräte (Mode 09):\n"
        "  7E8\n"
        "    Überwachungszähler:   20 Fahrzyklen, 50 Zündungen\n"
        "      Katalysator Bank 1: 15 von 20 (0.75)\n"
        "      Katalysator Bank 2: 0 von 0\n"
        "  7E9\n"
        "    Hinweis: keine weiteren Angaben\n"
    )


def test_cli_ecus_english(port: dict[str, str], capsys: pytest.CaptureFixture[str]) -> None:
    port.update(ECU_INFO_HEADERS)
    assert main(["ecus", "--lang", "en"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Control units (mode 09):\n  7E8  ECM-EngineControl\n")
    assert "Calibration ID" in out and "56028949AC" in out
    assert "20 driving cycles, 50 ignitions" in out
    assert "Catalyst bank 1" in out and "15 of 20 (0.75)" in out


def test_cli_ecus_not_available(port: dict[str, str], capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["ecus"]) == 1
    assert capsys.readouterr().out == "Steuergeräte (Mode 09): nicht verfügbar.\n"
    assert main(["ecus", "--json"]) == 1
    assert json.loads(capsys.readouterr().out) is None


def test_cli_ecus_json(port: dict[str, str], capsys: pytest.CaptureFixture[str]) -> None:
    port.update(HEADERS_29)
    assert main(["ecus", "--json"]) == 0
    engine, gearbox = json.loads(capsys.readouterr().out)
    assert engine["ecu"] == "18DAF110"
    assert engine["calibration_ids"] == ["A6519006400", "SW 0815"]
    assert engine["supported"] == [2, 4, 6, 10, 11]
    assert engine["performance"]["compression_ignition"] is True
    assert engine["performance"]["counters"][0] == {
        "key": "nmhc_catalyst",
        "completed": 12,
        "conditions": 13,
    }
    assert gearbox["name"] is None and gearbox["performance"] is None


_UNKNOWN_UASID_HEADERS = {
    "0600": can_lines("7E8", "460000008000"),  # nur MID 11 (Bit 17)
    "0611": can_lines("7E8", "4611" + "8055" + "0007" + "0001" + "0005"),
}


def test_cli_tests_raw_value_fallback(
    port: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    port.update(_UNKNOWN_UASID_HEADERS)
    assert main(["tests"]) == 0
    assert capsys.readouterr().out == (
        "Testergebnisse (Mode 06, 1 Test, davon 1 nicht bestanden):\n"
        "  Steuergerät 7E8\n"
        "    Monitor 11 · Herstellertest 80\n"
        "      7 (Rohwert)  (Grenzen 1 (Rohwert) bis 5 (Rohwert))  nicht bestanden\n"
    )


def test_cli_tests_english_signed(port: dict[str, str], capsys: pytest.CaptureFixture[str]) -> None:
    port.update(
        {
            "0600": can_lines("7E8", "460000004000"),  # nur MID 12 (Bit 18)
            "0612": can_lines("7E8", "4612" + "0599" + "FFCE" + "FF38" + "00C8"),
        }
    )
    assert main(["tests", "--lang", "en"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Test results (mode 06, 1 test, 0 of them failed):\n")
    assert "-5.0 kPa  (limits -20.0 kPa to 20.0 kPa)  passed" in out


def test_cli_tests_json_signed_values(
    port: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    port.update(HEADERS_06_29)
    assert main(["tests", "--json"]) == 0
    (test,) = json.loads(capsys.readouterr().out)
    assert test == {
        "ecu": "18DAF118",
        "mid": 2,
        "tid": 1,
        "uasid": 0x81,
        "value_raw": 0xFFFF,
        "min_raw": 0xFFFF,
        "max_raw": 1,
        "value": -1,
        "min": -1,
        "max": 1,
        "unit": "",
        "status": "passed",
    }


def test_cli_tests_unknown_uasid_json_has_null_values(
    port: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    port.update(_UNKNOWN_UASID_HEADERS)
    assert main(["tests", "--json"]) == 0
    (test,) = json.loads(capsys.readouterr().out)
    assert (test["value"], test["min"], test["max"], test["status"]) == (None, None, None, "failed")


def test_cli_tests_not_available(port: dict[str, str], capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["tests"]) == 1
    assert capsys.readouterr().out == "Testergebnisse (Mode 06): nicht verfügbar.\n"


def test_cli_tests_reported_but_empty(
    port: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    port.update({"0600": can_lines("7E8", "460080000000"), "0601": can_lines("7E8", "7F0612")})
    assert main(["tests"]) == 0
    assert (
        capsys.readouterr().out == "Testergebnisse (Mode 06, 0 Tests, davon 0 nicht bestanden):\n"
    )


# --- Sitzung -------------------------------------------------------------------------------

_EDGE_ECUS = (
    EcuInfo(
        "18DAF110",
        "ECM-EngineControl",
        ("A6519006400", "SW 0815"),
        ("00000000",),
        InUsePerformance(True, 0xFFFF, 0, (PerformanceCounter("fuel", 3, 0),)),
        (0x04, 0x06, 0x0A, 0x0B),
    ),
    EcuInfo("18DAF118"),
)
_EDGE_TESTS = (
    TestResult("18DAF110", 0x21, 0x80, 0x55, 7, 1, 5),  # unbekannte UASID
    TestResult("18DAF110", 0x85, 0x01, 0x81, 0x8000, 0x8000, 0x7FFF),
    TestResult("18DAF118", 0xA2, 0x0C, 0x24, 0, 0, 0),
)


def _with(ecus: tuple[EcuInfo, ...] | None, tests: tuple[TestResult, ...] | None) -> Session:
    return dataclasses.replace(minimal_session(), ecus=ecus, tests=tests)


def test_session_round_trip_with_edge_values() -> None:
    session = _with(_EDGE_ECUS, _EDGE_TESTS)
    data = json.loads(json.dumps(session_to_dict(session)))
    assert data["ecus"][0]["performance"]["counters"][0]["ratio"] is None
    assert data["tests"][0]["value"] is None
    assert data["tests"][1]["value"] == -32768
    loaded = session_from_dict(data)
    assert loaded.ecus == _EDGE_ECUS
    assert loaded.tests == _EDGE_TESTS


def test_session_status_is_recomputed_from_raw_values() -> None:
    data = json.loads(json.dumps(session_to_dict(_with(None, _EDGE_TESTS[:1]))))
    data["tests"][0]["status"] = "passed"  # zur Information; maßgeblich sind die Rohwerte
    (test,) = session_from_dict(data).tests or ()
    assert test.status is TestStatus.FAILED


def test_session_without_mode09_and_mode06_keys() -> None:
    data = session_to_dict(minimal_session())
    del data["ecus"], data["tests"]  # Sitzung einer älteren Version
    loaded = session_from_dict(data)
    assert (loaded.ecus, loaded.tests) == (None, None)


def test_session_empty_tests_stay_empty_not_none() -> None:
    loaded = session_from_dict(json.loads(json.dumps(session_to_dict(_with((), ())))))
    assert (loaded.ecus, loaded.tests) == ((), ())


def test_session_with_broken_test_entry_is_rejected() -> None:
    data = session_to_dict(_with(None, _EDGE_TESTS[:1]))
    del data["tests"][0]["min_raw"]
    with pytest.raises(ValueError, match="beschädigt"):
        session_from_dict(data)


# --- PDF --------------------------------------------------------------------------------


def _pdf_text(path: Path) -> str:
    text = "\n".join(page.extract_text() for page in PdfReader(path).pages)
    return re.sub(r"\s+", " ", text)


@pytest.fixture
def helvetica(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(report, "find_font_files", lambda: None)


@pytest.mark.usefixtures("helvetica")
def test_pdf_shows_raw_values_signed_limits_and_unnamed_ecus(tmp_path: Path) -> None:
    path = tmp_path / "bericht.pdf"
    report.export_pdf(_with(_EDGE_ECUS, _EDGE_TESTS), path)
    text = _pdf_text(path)
    assert "ECM-EngineControl (18DAF110)" in text
    assert "A6519006400" in text and "SW 0815" in text and "00000000" in text
    assert "65535 Fahrzyklen, 0 Zündungen" in text
    assert "Kraftstoffsystem 3 / 0" in text  # ohne Bedingungen keine Quote
    assert "7 (Rohwert)" in text and "1 (Rohwert) bis 5 (Rohwert)" in text
    assert "-32.768 -32.768 bis 32.767 bestanden" in text  # mit Vorzeichen, deutsch
    assert "Verbrennungsaussetzer Zylinder 1" in text
    assert "kein Ergebnis" in text


@pytest.mark.usefixtures("helvetica")
def test_pdf_mode09_and_mode06_not_available_in_english(tmp_path: Path) -> None:
    set_language("en")
    path = tmp_path / "report.pdf"
    report.export_pdf(_with(None, None), path)
    text = _pdf_text(path)
    assert "Control units (mode 09)" in text
    assert "Test results (mode 06) Test results not available." in text
