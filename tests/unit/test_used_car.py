"""Gebrauchtwagen-Check (``services/used_car.py``): Zähler lesen und bewerten."""

import json
from pathlib import Path

import pytest

from obd_diag import cli
from obd_diag.i18n import set_language
from obd_diag.protocol.elm327 import Elm327
from obd_diag.services.diagnostics import DiagnosticCode, DtcKind, ScanResult
from obd_diag.services.readiness import decode_readiness
from obd_diag.services.session import load_session, save_session, session_from_dict
from obd_diag.services.session import session_to_dict as to_dict
from obd_diag.services.used_car import (
    RECENT_DISTANCE_KM,
    RECENT_TIME_MIN,
    RECENT_WARMUPS,
    ClearCounters,
    Level,
    Verdict,
    assess,
    counter_rows,
    read_clear_counters,
    read_used_car,
)
from tests.fakes import CAN_CAR, CAN_CAR_FULL, FakeTransport
from tests.samples import CLEAR_COUNTERS, full_session, minimal_session

# Readiness ohne MIL, alle Tests abgeschlossen bzw. Katalysator offen (Otto)
READY = decode_readiness(bytes.fromhex("00076500"))
CATALYST_OPEN = decode_readiness(bytes.fromhex("00076501"))
MIL_ON = decode_readiness(bytes.fromhex("81076500"))
NO_CODES = ScanResult(adapter="ELM327 v1.5", protocol="ISO 15765-4 (CAN 11/500)", voltage=12.4)
PERMANENT = ScanResult(
    adapter="ELM327 v1.5",
    protocol="ISO 15765-4 (CAN 11/500)",
    voltage=12.4,
    codes=[
        DiagnosticCode("P0420", DtcKind.STORED),
        DiagnosticCode("P0420", DtcKind.PERMANENT),
        DiagnosticCode("P0300", DtcKind.PERMANENT),
    ],
)
OLD = ClearCounters(warmups=40, distance_km=3000, time_min=600, mil_distance_km=0, mil_time_min=0)


def _counters(responses: dict[str, str]) -> tuple[ClearCounters, list[str]]:
    transport = FakeTransport(responses)
    return read_clear_counters(Elm327(transport)), transport.sent


# --- Lesen ---------------------------------------------------------------------------


def test_reads_all_five_counters_in_order() -> None:
    counters, sent = _counters(CAN_CAR_FULL)
    assert sent == ["0130", "0131", "014E", "0121", "014D"]
    assert counters == CLEAR_COUNTERS


def test_counters_not_reported_stay_none() -> None:
    counters, _ = _counters(
        {"0130": "NO DATA", "0131": "7F0112", "014E": "?", "0121": "OK", "014D": "414D"}
    )
    assert counters == ClearCounters()
    assert not counters.available


def test_adapter_error_on_one_counter_keeps_the_others() -> None:
    counters, sent = _counters({"0130": "?", "0131": "41310BB8", "014E": "NO DATA"})
    assert counters.distance_km == 3000
    assert counters.warmups is None
    assert len(sent) == 5


def test_several_ecus_smallest_since_clear_largest_with_mil() -> None:
    """Ohne Header in beliebiger Reihenfolge: seit dem Löschen zählt das Steuergerät mit
    dem kleinsten Wert, mit Warnleuchte das mit dem größten."""
    counters, _ = _counters(
        {
            "0130": "413028\r413005",
            "0131": "41310BB8\r41310064",
            "014E": "414E0050\r414E1000",
            "0121": "41210000\r4121000C",
            "014D": "414D0014\r414D0000",
        }
    )
    assert counters == ClearCounters(
        warmups=5, distance_km=100, time_min=80, mil_distance_km=12, mil_time_min=20
    )


def test_short_or_foreign_answers_are_skipped() -> None:
    counters, _ = _counters({"0131": "4131\r410C0000\rZZ\r41310064"})
    assert counters.distance_km == 100


def test_read_used_car_with_scan_and_readiness() -> None:
    reading = read_used_car(Elm327(FakeTransport(CAN_CAR_FULL)))
    assert reading.counters == CLEAR_COUNTERS
    assert reading.readiness is not None
    assert reading.readiness.mil_on
    assert [c.code for c in reading.scan.codes if c.kind is DtcKind.STORED] == [
        "P0133",
        "P0300",
        "P0171",
    ]


def test_read_used_car_without_readiness() -> None:
    reading = read_used_car(Elm327(FakeTransport({**CAN_CAR, "0101": "?"})))
    assert reading.readiness is None
    assert reading.counters == ClearCounters()


# --- Bewerten ------------------------------------------------------------------------


def _levels(counters: ClearCounters | None, scan: ScanResult = NO_CODES, readiness=READY):  # type: ignore[no-untyped-def]
    check = assess(counters, scan, readiness)
    return check, [(f.level, f.text) for f in check.findings]


def test_old_clear_is_inconspicuous() -> None:
    check, findings = _levels(OLD)
    assert check.verdict is Verdict.INCONSPICUOUS
    assert check.label == "Unauffällig"
    assert not check.recently_cleared
    assert findings == [
        (
            Level.OK,
            "Nicht vor kurzem gelöscht: 40 Warmläufe, 3.000 km, 10 Stunden seit dem letzten "
            "Löschen.",
        ),
        (Level.OK, "Motorkontrollleuchte aus."),
        (Level.OK, "Keine permanenten Fehlercodes gemeldet."),
    ]


@pytest.mark.parametrize(
    ("counters", "since"),
    [
        (ClearCounters(warmups=RECENT_WARMUPS - 1), "9 Warmläufe"),
        (ClearCounters(warmups=1, distance_km=5000), "1 Warmlauf"),
        (ClearCounters(distance_km=RECENT_DISTANCE_KM - 1), "199 km"),
        (ClearCounters(time_min=RECENT_TIME_MIN - 1), "4 Stunden"),
        (ClearCounters(time_min=1), "1 Minute"),
        (ClearCounters(warmups=0, distance_km=0, time_min=0), "0 Warmläufe, 0 km, 0 Minuten"),
    ],
)
def test_recently_cleared(counters: ClearCounters, since: str) -> None:
    check, findings = _levels(counters)
    assert check.verdict is Verdict.SUSPICIOUS
    assert check.recently_cleared
    assert findings[0] == (
        Level.WARNING,
        f"Fehlercodes vor kurzem gelöscht: erst {since} seit dem Löschen.",
    )


def test_thresholds_themselves_are_not_recent() -> None:
    counters = ClearCounters(
        warmups=RECENT_WARMUPS, distance_km=RECENT_DISTANCE_KM, time_min=RECENT_TIME_MIN
    )
    check, _ = _levels(counters)
    assert check.verdict is Verdict.INCONSPICUOUS


def test_maximum_warmups_shown_as_at_least() -> None:
    rows = dict(counter_rows(ClearCounters(warmups=255, distance_km=65535)))
    assert rows["Warmläufe seit dem Löschen"] == "255 oder mehr"
    assert rows["Strecke seit dem Löschen"] == "65.535 km"
    assert rows["Zeit seit dem Löschen"] == "nicht gemeldet"


def test_open_monitors_support_a_recent_clear() -> None:
    _, findings = _levels(ClearCounters(warmups=2), readiness=CATALYST_OPEN)
    assert (
        Level.INFO,
        "Passend dazu ist 1 Readiness-Test nicht abgeschlossen: Katalysator.",
    ) in findings
    _, findings = _levels(OLD, readiness=CATALYST_OPEN)
    assert (
        Level.INFO,
        "1 Readiness-Test nicht abgeschlossen: Katalysator. Das kann auch an den "
        "Fahrbedingungen liegen.",
    ) in findings


def test_open_monitors_alone_are_no_finding() -> None:
    check, _ = _levels(OLD, readiness=CATALYST_OPEN)
    assert check.verdict is Verdict.INCONSPICUOUS


def test_mil_on() -> None:
    check, findings = _levels(OLD, readiness=MIL_ON)
    assert check.verdict is Verdict.SUSPICIOUS
    assert (Level.WARNING, "Die Motorkontrollleuchte ist an.") in findings
    counters = ClearCounters(warmups=40, mil_distance_km=12, mil_time_min=20)
    _, findings = _levels(counters, readiness=MIL_ON)
    assert (
        Level.WARNING,
        "Die Motorkontrollleuchte ist an. Gefahren mit Warnleuchte: 12 km, 20 Minuten.",
    ) in findings


def test_mil_was_on_since_last_clear() -> None:
    check, findings = _levels(ClearCounters(warmups=40, mil_distance_km=150))
    assert check.verdict is Verdict.SUSPICIOUS
    assert (
        Level.WARNING,
        "Die Motorkontrollleuchte war seit dem letzten Löschen an: 150 km mit "
        "Warnleuchte, jetzt ist sie aus.",
    ) in findings


def test_permanent_codes() -> None:
    check, findings = _levels(OLD, scan=PERMANENT)
    assert check.verdict is Verdict.SUSPICIOUS
    assert findings[-1] == (
        Level.WARNING,
        "Permanente Fehlercodes: P0300, P0420. Sie lassen sich nicht löschen; das "
        "Steuergerät entfernt sie erst, wenn es den Fehler nicht mehr feststellt.",
    )


def test_without_counters_unknown_but_permanent_codes_still_count() -> None:
    check, findings = _levels(None, readiness=None)
    assert check.verdict is Verdict.UNKNOWN
    assert check.label == "Nicht beurteilbar"
    assert [level for level, _ in findings] == [Level.INFO, Level.OK]
    check, _ = _levels(ClearCounters(), scan=PERMANENT, readiness=None)
    assert check.verdict is Verdict.SUSPICIOUS


def test_english() -> None:
    set_language("en")
    check, findings = _levels(ClearCounters(warmups=3, distance_km=1200, time_min=150))
    assert check.label == "Suspicious"
    assert findings[0] == (
        Level.WARNING,
        "Trouble codes cleared recently: only 3 warm-ups, 2 hours since clearing.",
    )
    rows = counter_rows(ClearCounters(distance_km=1200))
    assert rows[1] == ("Distance since codes cleared", "1,200 km")
    assert rows[0] == ("Warm-ups since codes cleared", "not reported")


# --- Sitzung -------------------------------------------------------------------------


def test_session_round_trip(tmp_path: Path) -> None:
    session = full_session()
    assert session_from_dict(json.loads(json.dumps(to_dict(session)))) == session
    assert load_session(save_session(session, tmp_path)).clear_counters == CLEAR_COUNTERS
    assert to_dict(minimal_session())["clear_counters"] is None


def test_session_of_older_version_has_no_counters() -> None:
    data = to_dict(full_session())
    del data["clear_counters"]
    assert session_from_dict(data).clear_counters is None


# --- Kommandozeile -------------------------------------------------------------------


@pytest.fixture
def car(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "SerialTransport", lambda port, baud: FakeTransport(CAN_CAR_FULL))


@pytest.mark.usefixtures("car")
def test_cli_used_car(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["used-car"]) == 0
    out = capsys.readouterr().out
    assert out.startswith(
        "Gebrauchtwagen-Check: Auffällig\n"
        "  Warmläufe seit dem Löschen: 3\n"
        "  Strecke seit dem Löschen:   45 km\n"
    )
    assert "  [i] Passend dazu sind 2 Readiness-Tests nicht abgeschlossen:" in out
    assert "  [!] Permanent" not in out  # Mode 0A: NO DATA


@pytest.mark.usefixtures("car")
def test_cli_used_car_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--lang", "en", "used-car", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["verdict"] == "suspicious"
    assert data["recently_cleared"] is True
    assert data["clear_counters"] == {
        "warmups": 3,
        "distance_km": 45,
        "time_min": 80,
        "mil_distance_km": 12,
        "mil_time_min": 20,
    }
    assert data["permanent_codes"] == []
    assert data["findings"][0] == {
        "level": "warning",
        "text": "Trouble codes cleared recently: only 3 warm-ups, 45 km, 80 minutes since "
        "clearing.",
    }
