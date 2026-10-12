"""Randfälle der QML-Werte für Steuergeräte (Mode 09) und Testergebnisse (Mode 06)."""

import pytest

from obd_diag.i18n import set_language
from obd_diag.protocol.monitor_tests import TestResult
from obd_diag.protocol.vehicle_info import EcuInfo, InUsePerformance, PerformanceCounter
from obd_diag.ui.viewmodels.vehicle_info import ecus_entry, results_entry

DIESEL = EcuInfo(
    "18DAF110",
    performance=InUsePerformance(
        True,
        1234,
        65535,
        (PerformanceCounter("pm_filter", 30, 20), PerformanceCounter("fuel", 0, 0)),
    ),
)


@pytest.fixture(autouse=True)
def german() -> None:
    set_language("de")


def test_diesel_counters_and_ratio_above_one() -> None:
    (engine,) = ecus_entry((DIESEL,))["ecus"]
    assert engine["name"] == "Steuergerät 18DAF110"
    assert engine["facts"] == []
    assert engine["countersTitle"] == "Überwachungszähler: 1234 Fahrzyklen, 65535 Zündungen"
    pm, fuel = engine["counters"]
    assert pm == {
        "label": "Partikelfilter",
        "value": "30 von 20",
        "ratio": 1.5,
        "ratioText": "1,50",
    }
    assert (fuel["label"], fuel["ratio"], fuel["ratioText"]) == ("Kraftstoffsystem", -1.0, "")


def test_ecus_entry_in_english() -> None:
    set_language("en")
    (engine,) = ecus_entry((DIESEL,))["ecus"]
    assert engine["name"] == "Control unit 18DAF110"
    assert engine["counters"][0]["label"] == "Particulate filter"
    assert engine["counters"][0]["ratioText"] == "1.50"


def test_ecu_with_name_but_nothing_else() -> None:
    (entry,) = ecus_entry((EcuInfo("7E9", name="TCM-TransmissionCtl"),))["ecus"]
    assert entry == {
        "address": "7E9",
        "name": "TCM-TransmissionCtl",
        "facts": [],
        "countersTitle": "",
        "counters": [],
    }


def test_results_grouped_per_ecu_with_signed_limits_and_thousands() -> None:
    tests = (
        TestResult("7E8", 0x85, 0x01, 0x81, 0x8000, 0x8000, 0x7FFF),  # -32768 genau an der Grenze
        TestResult("7E9", 0x21, 0x80, 0x24, 3, 0, 2),
        TestResult("7E9", 0xA2, 0x0C, 0x24, 0, 0, 0),
    )
    entry = results_entry(tests, (EcuInfo("7E9", name="TCM"),))
    assert (entry["count"], entry["failedCount"], entry["noResultCount"]) == (3, 1, 1)
    first, second = entry["groups"]
    assert first["title"] == "Steuergerät 7E8"
    (row,) = first["rows"]
    assert row["value"] == "-32.768"
    assert row["limits"] == "Grenzen -32.768 bis 32.767"
    assert row["status"] == "passed"
    assert second["title"] == "TCM (7E9)"
    assert [r["status"] for r in second["rows"]] == ["failed", "no_result"]
    assert second["rows"][1]["minimum"] == second["rows"][1]["maximum"] == ""


def test_results_summary_plural_and_raw_limits_in_english() -> None:
    set_language("en")
    tests = (
        TestResult("7E8", 0x21, 0x80, 0x55, 7, 1, 5),
        TestResult("7E8", 0x22, 0x80, 0x24, 9, 0, 5),
    )
    entry = results_entry(tests)
    assert entry["summary"] == "2 tests outside their limits."
    row = entry["groups"][0]["rows"][0]
    assert row["value"] == "7 (raw value)"
    assert (row["minimum"], row["maximum"]) == ("1 (raw value)", "5 (raw value)")
    assert row["statusLabel"] == "failed"
