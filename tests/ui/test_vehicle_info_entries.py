"""Steuergeräte (Mode 09) und Testergebnisse (Mode 06) als Werte für QML."""

import pytest

from obd_diag.i18n import set_language
from obd_diag.protocol.monitor_tests import TestResult
from obd_diag.ui.viewmodels.vehicle_info import ecus_entry, results_entry
from tests.samples import ECUS, TESTS


@pytest.fixture(autouse=True)
def german() -> None:
    set_language("de")


def test_ecus_entry() -> None:
    entry = ecus_entry(ECUS)
    assert entry["available"] is True
    engine, gearbox = entry["ecus"]
    assert engine["name"] == "ECM-EngineControl"
    assert engine["facts"] == [
        {"label": "Kalibrierungs-ID", "value": "03C906056DK"},
        {"label": "CVN", "value": "1234ABCD"},
    ]
    assert engine["countersTitle"] == "Überwachungszähler: 20 Fahrzyklen, 50 Zündungen"
    assert engine["counters"][0] == {
        "label": "Katalysator Bank 1",
        "value": "15 von 20",
        "ratio": 0.75,
        "ratioText": "0,75",
    }
    assert engine["counters"][2]["ratioText"] == ""  # ohne Bedingungen keine Quote
    assert gearbox["name"] == "Steuergerät 7E9"  # ohne 090A
    assert gearbox["counters"] == []


def test_no_ecus() -> None:
    assert ecus_entry(None) == {"available": False, "ecus": []}


def test_results_entry() -> None:
    entry = results_entry(TESTS, ECUS)
    assert (entry["count"], entry["failedCount"], entry["noResultCount"]) == (3, 1, 1)
    assert entry["summary"] == "1 Test außerhalb der Grenzwerte."
    (group,) = entry["groups"]
    assert group["title"] == "ECM-EngineControl (7E8)"
    o2, catalyst, misfire = group["rows"]
    assert o2["monitor"] == "Lambdasonde Bank 1 Sensor 1"
    assert o2["test"] == "Spannungsschwelle fett nach mager"
    assert o2["value"] == "343,552 mV"
    assert o2["limits"] == "Grenzen 281,088 mV bis 406,016 mV"
    assert (o2["status"], o2["statusLabel"]) == ("passed", "bestanden")
    assert (catalyst["value"], catalyst["status"]) == ("240", "failed")
    assert (misfire["value"], misfire["limits"], misfire["statusLabel"]) == (
        "",
        "",
        "kein Ergebnis",
    )


def test_results_entry_all_within_limits_and_english() -> None:
    passed = (TestResult("18DAF110", 0x85, 0x80, 0x99, 0x0032, 0xFF38, 0x00C8),)
    assert results_entry(passed)["summary"] == (
        "Alle Tests mit Ergebnis liegen innerhalb der Grenzwerte."
    )
    set_language("en")
    entry = results_entry(passed)
    assert entry["groups"][0]["title"] == "Control unit 18DAF110"
    assert entry["groups"][0]["rows"][0]["limits"] == "limits -20.0 kPa to 20.0 kPa"
    assert entry["summary"] == "All tests with a result are within their limits."


def test_unknown_unit_shows_the_raw_value() -> None:
    entry = results_entry((TestResult("7E8", 0x21, 0x80, 0x55, 7, 1, 5),))
    assert entry["groups"][0]["rows"][0]["value"] == "7 (Rohwert)"


def test_no_tests_and_empty_tests() -> None:
    assert results_entry(None)["available"] is False
    empty = results_entry(())
    assert empty["available"] is True
    assert empty["summary"] == "Das Fahrzeug meldet Mode 06, aber keine Testergebnisse."
