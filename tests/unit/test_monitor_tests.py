"""Mode 06 (``protocol/monitor_tests.py``): unterstützte Monitor-IDs, Testergebnisse,
Umrechnung nach UASID und Urteil."""

import pytest

from obd_diag.i18n import set_language
from obd_diag.protocol.elm327 import Elm327, ElmError
from obd_diag.protocol.headers import parse_header_response
from obd_diag.protocol.monitor_tests import (
    UNITS,
    TestResult,
    TestStatus,
    mid_name,
    parse_results,
    parse_supported,
    read_test_results,
    tid_name,
)
from tests.fakes import CAN_CAR_FULL, MONITOR_TESTS_HEADERS, FakeTransport, can_lines


def _read(headers: dict[str, str]) -> tuple[list[TestResult] | None, FakeTransport]:
    transport = FakeTransport(CAN_CAR_FULL, headers_on=headers)
    return read_test_results(Elm327(transport)), transport


def test_supported_mids_from_the_bitmask() -> None:
    (message,) = parse_header_response(can_lines("7E8", "4620800080 01"))
    assert parse_supported(message, 0x20) == (0x21, 0x31, 0x40)
    assert parse_supported(message, 0x00) is None  # andere Basis


def test_results_are_split_into_blocks_of_nine() -> None:
    (message,) = parse_header_response(MONITOR_TESTS_HEADERS["0601"])
    first, second = parse_results(message, 0x01)
    assert first == TestResult("7E8", 0x01, 0x01, 0x0A, 0x0B00, 0x0900, 0x0D00)
    assert first.value == pytest.approx(343.552)  # 2816 · 0,122 mV
    assert (first.minimum, first.maximum) == (pytest.approx(281.088), pytest.approx(406.016))
    assert first.unit == "mV"
    assert first.status is TestStatus.PASSED
    assert second.tid == 0x05
    assert (second.value, second.unit) == (80, "ms")


@pytest.mark.parametrize(
    "payload",
    [
        "4601010A0B000900",  # zu kurz
        "4601010A0B0009000D0000",  # kein Vielfaches von 9
        "4602010A0B0009000D00",  # andere MID als angefragt
        "4101010A0B0009000D00",  # andere Antwort
    ],
)
def test_malformed_results_are_rejected(payload: str) -> None:
    (message,) = parse_header_response(can_lines("7E8", payload))
    with pytest.raises(ValueError, match="0601"):
        parse_results(message, 0x01)


# --- Umrechnung und Urteil -------------------------------------------------------------


def test_signed_values_and_limits() -> None:
    # UASID 99: 0,1 kPa mit Vorzeichen; -20,0 ≤ 5,0 ≤ 20,0
    test = TestResult("18DAF110", 0x85, 0x80, 0x99, 0x0032, 0xFF38, 0x00C8)
    assert (test.value, test.minimum, test.maximum) == (
        pytest.approx(5.0),
        pytest.approx(-20.0),
        pytest.approx(20.0),
    )
    assert test.status is TestStatus.PASSED
    # ohne Vorzeichen wäre FF38 die größte Zahl und der Test gälte als nicht bestanden
    below = TestResult("18DAF110", 0x85, 0x80, 0x99, 0xFF00, 0xFF38, 0x00C8)
    assert below.status is TestStatus.FAILED


def test_offset_units() -> None:
    temperature = TestResult("7E8", 0x01, 0x80, 0x16, 1000, 0, 2000)
    assert temperature.value == pytest.approx(60.0)  # 1000 · 0,1 °C - 40
    assert temperature.unit == "°C"


@pytest.mark.parametrize(
    ("value", "low", "high", "status"),
    [
        (100, 0, 200, TestStatus.PASSED),
        (200, 0, 200, TestStatus.PASSED),  # Grenzen gehören dazu
        (0, 0, 200, TestStatus.PASSED),
        (201, 0, 200, TestStatus.FAILED),
        (5, 10, 200, TestStatus.FAILED),
        (0, 0, 0, TestStatus.NO_RESULT),
    ],
)
def test_status(value: int, low: int, high: int, status: TestStatus) -> None:
    assert TestResult("7E8", 0x21, 0x80, 0x24, value, low, high).status is status


def test_unknown_unit_keeps_the_raw_value_and_still_judges() -> None:
    test = TestResult("7E8", 0x21, 0x80, 0x55, 7, 1, 5)
    assert test.value is None and test.unit == ""
    assert test.status is TestStatus.FAILED
    signed = TestResult("7E8", 0x21, 0x80, 0xC5, 0xFFFF, 0xFFF0, 0x0010)  # -1 in -16…16
    assert signed.status is TestStatus.PASSED


@pytest.mark.parametrize(
    ("uasid", "decimals"),
    [(0x01, 0), (0x07, 2), (0x0A, 3), (0x16, 1), (0x05, 5), (0x11, 0), (0x55, 0)],
)
def test_decimals_follow_the_resolution(uasid: int, decimals: int) -> None:
    assert TestResult("7E8", 1, 1, uasid, 0, 0, 0).decimals == decimals


def test_units_signedness_matches_the_id() -> None:
    # J1979 Anhang E: 01-7F ohne, 81-FE mit Vorzeichen; kein Eintrag für 00 und FF
    assert 0x00 not in UNITS and 0xFF not in UNITS


# --- Namen ------------------------------------------------------------------------------


@pytest.fixture
def german() -> None:
    set_language("de")


@pytest.mark.usefixtures("german")
@pytest.mark.parametrize(
    ("mid", "name"),
    [
        (0x01, "Lambdasonde Bank 1 Sensor 1"),
        (0x06, "Lambdasonde Bank 2 Sensor 2"),
        (0x10, "Lambdasonde Bank 4 Sensor 4"),
        (0x41, "Lambdasondenheizung Bank 1 Sensor 1"),
        (0x22, "Katalysator Bank 2"),
        (0x31, "Abgasrückführung (AGR) Bank 1"),
        (0x3C, "Tankentlüftung: Leck 0,020 Zoll"),
        (0x85, "Ladedruckregelung Bank 1"),
        (0x98, "NOx-/SCR-Katalysator Bank 1"),
        (0xA1, "Verbrennungsaussetzer allgemein"),
        (0xA5, "Verbrennungsaussetzer Zylinder 4"),
        (0xB1, "Partikelfilter Bank 2"),
        (0xC3, "Monitor C3"),
    ],
)
def test_mid_names(mid: int, name: str) -> None:
    assert mid_name(mid) == name


@pytest.mark.usefixtures("german")
def test_tid_names() -> None:
    assert tid_name(0x05) == "Umschaltzeit fett nach mager"
    assert tid_name(0x0C) == "Aussetzer im letzten/aktuellen Fahrzyklus"
    assert tid_name(0x80) == "Herstellertest 80"
    assert tid_name(0x30) == "Test 30"


def test_names_in_english() -> None:
    set_language("en")
    try:
        assert mid_name(0x21) == "Catalyst bank 1"
        assert mid_name(0x02) == "Oxygen sensor bank 1 sensor 2"
        assert tid_name(0x81) == "Manufacturer test 81"
    finally:
        set_language("de")


# --- Lesen ------------------------------------------------------------------------------


def test_read_test_results() -> None:
    results, transport = _read(MONITOR_TESTS_HEADERS)
    assert results is not None
    assert [(r.mid, r.tid, r.status) for r in results] == [
        (0x01, 0x01, TestStatus.PASSED),
        (0x01, 0x05, TestStatus.PASSED),
        (0x21, 0x80, TestStatus.FAILED),  # 0602 abgelehnt (7F 06 12): fehlt
    ]
    assert {r.ecu for r in results} == {"7E8"}
    assert transport.sent[-1] == "ATH0" and not transport.headers


def test_no_mode_06_is_none() -> None:
    results, transport = _read({})
    assert results is None
    assert transport.sent == ["ATH1", "0600", "ATH0"]


def test_supported_but_nothing_readable_is_empty() -> None:
    results, _ = _read({"0600": can_lines("7E8", "460040000000"), "0602": "NO DATA"})
    assert results == []


def test_several_control_units_sorted_by_address() -> None:
    headers = {
        "0600": can_lines("7E9", "460080000000") + "\r" + can_lines("7E8", "460080000000"),
        "0601": can_lines("7E9", "4601" + "80240001" + "00000002")
        + "\r"
        + can_lines("7E8", "4601" + "80240003" + "00000004"),
    }
    results, _ = _read(headers)
    assert results is not None
    assert [(r.ecu, r.value_raw) for r in results] == [("7E8", 3), ("7E9", 1)]


def test_mid_chain_stops_without_the_next_range() -> None:
    _, transport = _read({"0600": can_lines("7E8", "460080000000")})
    assert transport.sent == ["ATH1", "0600", "0601", "ATH0"]


def test_mid_chain_follows_every_range() -> None:
    headers = {
        f"06{base:02X}": can_lines("7E8", f"46{base:02X}00000001") for base in range(0, 0xE0, 0x20)
    }
    headers["06E0"] = can_lines("7E8", "46E000000001")  # E0 meldet „weiter“: es gibt kein 0700
    _, transport = _read(headers)
    assert transport.sent == ["ATH1", *(f"06{b:02X}" for b in range(0, 0x100, 0x20)), "ATH0"]


def test_legacy_protocol_is_none() -> None:
    # J1850 u. a.: anderes Format ohne UASID, wird nicht gelesen
    results, transport = _read({"0600": "48 6B 10 46 00 C0 00 00 00 11"})
    assert results is None
    assert transport.sent == ["ATH1", "0600", "ATH0"]


def test_a_broken_answer_skips_only_that_mid(caplog: pytest.LogCaptureFixture) -> None:
    headers = {**MONITOR_TESTS_HEADERS, "0601": can_lines("7E8", "460101")}
    with caplog.at_level("WARNING", logger="obd_diag.protocol.monitor_tests"):
        results, _ = _read(headers)
    assert results is not None
    assert [r.mid for r in results] == [0x21]
    assert "0601 von 7E8" in caplog.text


def test_unreadable_supported_mids_are_an_elm_error() -> None:
    transport = FakeTransport(CAN_CAR_FULL, headers_on={"0600": "ZZ"})
    with pytest.raises(ElmError, match="0600"):
        read_test_results(Elm327(transport))
    assert transport.sent[-1] == "ATH0"
