"""Randfälle des Sitzungsvergleichs (``services/compare.py``, ``obd-diag compare``).

Ergänzt ``test_compare.py``: Grenzen (Spannungstoleranz, 11,8 V), Abschnitte mit
fehlenden Angaben (ältere Sitzungen), Reihenfolge und Symmetrie (Hypothesis),
Englisch und die Ausgabe der Kommandozeile.
"""

import dataclasses
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from obd_diag.cli import main
from obd_diag.i18n import set_language
from obd_diag.protocol.obd import FreezeFrame
from obd_diag.services.compare import (
    Change,
    ChangeKind,
    Section,
    SessionComparison,
    Trend,
    change_text,
    compare_sessions,
    comparison_to_dict,
)
from obd_diag.services.diagnostics import DiagnosticCode, DtcKind, ScanResult
from obd_diag.services.readiness import Monitor, MonitorState, ReadinessStatus
from obd_diag.services.session import Session, save_session
from obd_diag.services.used_car import ClearCounters
from tests.samples import CLEAR_COUNTERS, READINESS, full_session, minimal_session


def _later(session: Session, **changes: object) -> Session:
    """``session`` drei Tage später, mit geänderten Feldern."""
    later = session.created + timedelta(days=3)
    return dataclasses.replace(session, created=later, **changes)  # type: ignore[arg-type]


def _section(comparison: SessionComparison, key: str) -> list[Change]:
    for s in comparison.sections:
        if s.key == key:
            return list(s.changes)
    raise KeyError(key)


def _by_subject(comparison: SessionComparison, key: str) -> dict[str, Change]:
    return {c.subject: c for c in _section(comparison, key)}


def _scan(session: Session, **changes: object) -> Session:
    return _later(session, scan=dataclasses.replace(session.scan, **changes))  # type: ignore[arg-type]


def _codes(*codes: tuple[str, DtcKind]) -> list[DiagnosticCode]:
    return [DiagnosticCode(code, kind) for code, kind in codes]


S, P, X = DtcKind.STORED, DtcKind.PENDING, DtcKind.PERMANENT


# --- Fehlercodes -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("before", "after", "kind", "trend", "text"),
    [
        # gespeichert -> nur noch ausstehend: Bestätigung weg, besser
        ([S, P], [P], ChangeKind.CHANGED, Trend.BETTER, "gespeichert, ausstehend → ausstehend"),
        # ausstehend -> bestätigt (zusätzlich gespeichert): schlechter
        ([P], [S, P], ChangeKind.CHANGED, Trend.WORSE, "ausstehend → gespeichert, ausstehend"),
        # gespeichert bleibt, permanent kommt dazu: neutral
        ([S], [S, X], ChangeKind.CHANGED, Trend.NEUTRAL, "gespeichert → gespeichert, permanent"),
        ([X], [P], ChangeKind.CHANGED, Trend.NEUTRAL, "permanent → ausstehend"),
        ([], [X], ChangeKind.ADDED, Trend.WORSE, "neu: permanent"),
        ([], [S, P, X], ChangeKind.ADDED, Trend.WORSE, "neu: gespeichert, ausstehend, permanent"),
        ([P], [], ChangeKind.REMOVED, Trend.BETTER, "entfällt (vorher: ausstehend)"),
        ([S, X], [], ChangeKind.REMOVED, Trend.BETTER, "entfällt (vorher: gespeichert, permanent)"),
        ([P, X], [P, X], ChangeKind.UNCHANGED, Trend.NEUTRAL, "weiterhin ausstehend, permanent"),
    ],
)
def test_code_kind_transitions(
    before: list[DtcKind], after: list[DtcKind], kind: ChangeKind, trend: Trend, text: str
) -> None:
    a = _scan(minimal_session(), codes=[DiagnosticCode("P0101", k) for k in before])
    b = _scan(a, codes=[DiagnosticCode("P0101", k) for k in after])
    (change,) = _section(compare_sessions(a, b), "codes")
    assert (change.kind, change.trend, change_text(change)) == (kind, trend, text)
    assert change.subject == "P0101"  # ohne Katalogtext nur der Code


def test_kinds_text_follows_fixed_order_not_scan_order() -> None:
    """Arten immer in der Reihenfolge gespeichert, ausstehend, permanent."""
    a = _scan(minimal_session(), codes=_codes(("P0001", X), ("P0001", P), ("P0001", S)))
    (change,) = _section(compare_sessions(a, a), "codes")
    assert change.after == "gespeichert, ausstehend, permanent"


def test_duplicate_entries_count_once() -> None:
    """Meldet ein Steuergerät denselben Code doppelt, ist das kein Unterschied."""
    a = _scan(minimal_session(), codes=_codes(("P0001", S)))
    b = _scan(a, codes=_codes(("P0001", S), ("P0001", S)))
    (change,) = _section(compare_sessions(a, b), "codes")
    assert change.kind is ChangeKind.UNCHANGED and change.after == "gespeichert"


def test_codes_sorted_by_kind_then_code() -> None:
    a = _scan(minimal_session(), codes=_codes(("U0100", S), ("P0300", S), ("B1000", P)))
    b = _scan(a, codes=_codes(("U0100", S), ("C0035", P), ("P0420", S), ("B1000", S)))
    subjects = [(c.kind, c.subject) for c in _section(compare_sessions(a, b), "codes")]
    assert subjects == [
        (ChangeKind.ADDED, "C0035"),
        (ChangeKind.ADDED, "P0420"),
        (ChangeKind.CHANGED, "B1000"),
        (ChangeKind.REMOVED, "P0300"),
        (ChangeKind.UNCHANGED, "U0100"),
    ]


def test_code_title_prefers_newer_session() -> None:
    """Titel aus der jüngeren Sitzung (Sprache des jüngeren Scans), sonst aus der älteren."""
    session = full_session()
    p0420 = next(c for c in session.scan.codes if c.code == "P0420")
    assert p0420.info is not None
    english = dataclasses.replace(
        p0420, info=dataclasses.replace(p0420.info, title="Catalyst Efficiency Below Threshold")
    )
    b = _scan(session, codes=[english])
    changes = _section(compare_sessions(session, b), "codes")
    by_code = {c.subject.split()[0]: c for c in changes}
    assert by_code["P0420"].subject == "P0420  Catalyst Efficiency Below Threshold"
    # nur vorher vorhanden: Titel aus der älteren Sitzung
    assert by_code["P0300"].subject == (
        "P0300  Zufällige/mehrfache Zylinder-Verbrennungsaussetzer erkannt"
    )


def test_codes_without_scan_codes_on_both_sides() -> None:
    assert _section(compare_sessions(minimal_session(), _later(minimal_session())), "codes") == []


# --- Readiness ---------------------------------------------------------------------


def _readiness(*monitors: tuple[str, MonitorState], **changes: object) -> ReadinessStatus:
    names = {m.key: m.name for m in READINESS.monitors}
    status = ReadinessStatus(
        mil_on=False,
        dtc_count=0,
        compression_ignition=False,
        monitors=tuple(Monitor(key, names.get(key, key), state) for key, state in monitors),
    )
    return dataclasses.replace(status, **changes)  # type: ignore[arg-type]


C, INC, N = MonitorState.COMPLETE, MonitorState.INCOMPLETE, MonitorState.NOT_SUPPORTED


@pytest.mark.parametrize(
    ("state_a", "state_b", "kind", "trend"),
    [
        (C, INC, ChangeKind.CHANGED, Trend.WORSE),
        (INC, C, ChangeKind.CHANGED, Trend.BETTER),
        (N, C, ChangeKind.CHANGED, Trend.NEUTRAL),
        (C, N, ChangeKind.CHANGED, Trend.NEUTRAL),
        (N, INC, ChangeKind.CHANGED, Trend.NEUTRAL),
        (INC, N, ChangeKind.CHANGED, Trend.NEUTRAL),
    ],
)
def test_monitor_state_trends(
    state_a: MonitorState, state_b: MonitorState, kind: ChangeKind, trend: Trend
) -> None:
    a = _later(minimal_session(), readiness=_readiness(("catalyst", state_a)))
    b = _later(a, readiness=_readiness(("catalyst", state_b)))
    changes = _by_subject(compare_sessions(a, b), "readiness")
    assert (changes["Katalysator"].kind, changes["Katalysator"].trend) == (kind, trend)


def test_monitor_only_on_one_side() -> None:
    """Monitor nur vorher bzw. nur nachher (z. B. nach Motorartwechsel): neu/entfällt."""
    a = _later(minimal_session(), readiness=_readiness(("misfire", C), ("catalyst", C)))
    b = _later(a, readiness=_readiness(("misfire", C), ("egr", N)))
    changes = _section(compare_sessions(a, b), "readiness")
    assert changes == [
        Change(ChangeKind.REMOVED, "Katalysator", "abgeschlossen", None),
        Change(ChangeKind.ADDED, "Abgasrückführung", None, "nicht unterstützt"),
    ]


def test_monitor_order_old_first_then_new() -> None:
    a = _later(minimal_session(), readiness=_readiness(("evap", C), ("misfire", INC)))
    b = _later(a, readiness=_readiness(("egr", C), ("misfire", C), ("evap", INC)))
    subjects = [c.subject for c in _section(compare_sessions(a, b), "readiness")]
    assert subjects == [
        "Tankentlüftung",
        "Verbrennungsaussetzer",
        "Abgasrückführung",
    ]


@pytest.mark.parametrize(
    ("count_a", "count_b", "trend"),
    [(0, 1, Trend.WORSE), (5, 2, Trend.BETTER), (1, 0, Trend.BETTER)],
)
def test_dtc_count_trend(count_a: int, count_b: int, trend: Trend) -> None:
    a = _later(minimal_session(), readiness=_readiness(dtc_count=count_a))
    b = _later(a, readiness=_readiness(dtc_count=count_b))
    (change,) = _section(compare_sessions(a, b), "readiness")
    assert change == Change(
        ChangeKind.CHANGED, "Gemeldete Fehlercodes", str(count_a), str(count_b), trend
    )


def test_mil_turning_on_is_worse() -> None:
    a = _later(minimal_session(), readiness=_readiness())
    b = _later(a, readiness=_readiness(mil_on=True))
    (change,) = _section(compare_sessions(a, b), "readiness")
    assert change == Change(ChangeKind.CHANGED, "Kontrollleuchte (MIL)", "aus", "an", Trend.WORSE)


def test_all_complete_lost_is_worse() -> None:
    a = _later(minimal_session(), readiness=_readiness(("catalyst", C)))
    b = _later(a, readiness=_readiness(("catalyst", INC)))
    changes = _by_subject(compare_sessions(a, b), "readiness")
    assert changes["Alle Tests abgeschlossen"] == Change(
        ChangeKind.CHANGED, "Alle Tests abgeschlossen", "ja", "nein", Trend.WORSE
    )


def test_diesel_to_petrol_is_neutral() -> None:
    a = _later(minimal_session(), readiness=_readiness(compression_ignition=True))
    b = _later(a, readiness=_readiness())
    (change,) = _section(compare_sessions(a, b), "readiness")
    assert change == Change(ChangeKind.CHANGED, "Motorart", "Diesel", "Otto", Trend.NEUTRAL)


def test_readiness_section_order() -> None:
    """MIL, Zahl, Motorart, Monitore, zuletzt das Gesamturteil."""
    a = _later(minimal_session(), readiness=_readiness(("catalyst", C)))
    b = _later(
        a,
        readiness=_readiness(
            ("catalyst", INC), mil_on=True, dtc_count=1, compression_ignition=True
        ),
    )
    subjects = [c.subject for c in _section(compare_sessions(a, b), "readiness")]
    assert subjects == [
        "Kontrollleuchte (MIL)",
        "Gemeldete Fehlercodes",
        "Motorart",
        "Katalysator",
        "Alle Tests abgeschlossen",
    ]


# --- Freeze Frame ------------------------------------------------------------------


def test_freeze_frame_appears_after_new_code() -> None:
    a = _later(minimal_session())
    b = _later(a, freeze_frame=FreezeFrame(dtc="P0171", values={"rpm": 812.25}))
    assert _section(compare_sessions(a, b), "freeze_frame") == [
        Change(ChangeKind.ADDED, "Auslösender Fehlercode", None, "P0171"),
        Change(ChangeKind.ADDED, "Drehzahl", None, "812 1/min"),
    ]


def test_freeze_frame_unknown_key_uses_key_and_two_decimals() -> None:
    """Werte ohne Bezeichnung: Schlüssel als Name, zwei Nachkommastellen, keine Einheit."""
    a = _later(minimal_session(), freeze_frame=FreezeFrame(values={"fuel_pressure": 3.14159}))
    b = _later(a, freeze_frame=FreezeFrame(values={"fuel_pressure": 2.5}))
    (change,) = _section(compare_sessions(a, b), "freeze_frame")
    assert change == Change(ChangeKind.CHANGED, "fuel_pressure", "3,14", "2,50")


def test_freeze_frame_without_dtc_and_values_on_both_sides() -> None:
    a = _later(minimal_session(), freeze_frame=FreezeFrame())
    b = _later(a, freeze_frame=None)
    assert _section(compare_sessions(a, b), "freeze_frame") == []


def test_freeze_frame_values_kept_in_order() -> None:
    a = _later(minimal_session(), freeze_frame=FreezeFrame(values={"speed_kmh": 50.0}))
    b = _later(a, freeze_frame=FreezeFrame(values={"coolant_temp_c": -40.0, "speed_kmh": 0.0}))
    assert [change_text(c) for c in _section(compare_sessions(a, b), "freeze_frame")] == [
        "50 km/h → 0 km/h",
        "neu: -40 °C",
    ]


# --- Zähler seit dem Löschen ---------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "before", "after", "trend"),
    [
        ("warmups", 3, 4, Trend.NEUTRAL),  # normal weitergezählt
        ("warmups", 3, 3, None),  # gleich: kein Eintrag
        ("warmups", 3, 0, Trend.WORSE),  # dazwischen gelöscht
        ("distance_km", 45, 44, Trend.WORSE),  # schon ein Kilometer weniger
        ("time_min", 80, 65535, Trend.NEUTRAL),  # bis zum Höchstwert von PID 4E
        ("mil_distance_km", 12, 13, Trend.WORSE),
        ("mil_distance_km", 12, 0, Trend.BETTER),
        ("mil_time_min", 0, 1, Trend.WORSE),
        ("mil_time_min", 20, 19, Trend.BETTER),
        ("warmups", None, 3, Trend.NEUTRAL),  # nur nachher gemeldet
        ("mil_time_min", 20, None, Trend.NEUTRAL),  # nur vorher gemeldet
    ],
)
def test_counter_trends(field: str, before: int | None, after: int | None, trend: Trend) -> None:
    a = _later(
        minimal_session(), clear_counters=dataclasses.replace(CLEAR_COUNTERS, **{field: before})
    )
    b = _later(a, clear_counters=dataclasses.replace(CLEAR_COUNTERS, **{field: after}))
    changes = _section(compare_sessions(a, b), "clear_counters")
    if trend is None:
        assert changes == []
        return
    (change,) = changes
    assert change.trend is trend
    assert change.kind is (
        ChangeKind.ADDED
        if before is None
        else ChangeKind.REMOVED
        if after is None
        else ChangeKind.CHANGED
    )


def test_counter_texts_with_and_without_unit() -> None:
    """PID 30 hat keine Einheit, 31/21 km, 4E/4D min."""
    a = _later(minimal_session(), clear_counters=ClearCounters())
    b = _later(a, clear_counters=CLEAR_COUNTERS)
    assert [change_text(c) for c in _section(compare_sessions(a, b), "clear_counters")] == [
        "neu: 3",
        "neu: 45 km",
        "neu: 80 min",
        "neu: 12 km",
        "neu: 20 min",
    ]


def test_counters_missing_in_older_session_on_both_sides() -> None:
    """Ältere Sitzungen ohne Zähler (``None``) gegen leere Zähler: kein Unterschied."""
    a = _later(minimal_session(), clear_counters=None)
    b = _later(a, clear_counters=ClearCounters())
    assert _section(compare_sessions(a, b), "clear_counters") == []
    assert _section(compare_sessions(b, _later(b, clear_counters=None)), "clear_counters") == []


def test_counter_zero_is_not_missing() -> None:
    """0 ist ein gemeldeter Wert (gerade gelöscht), kein fehlender."""
    a = _later(minimal_session(), clear_counters=ClearCounters(distance_km=0))
    b = _later(a, clear_counters=ClearCounters())
    (change,) = _section(compare_sessions(a, b), "clear_counters")
    assert change_text(change) == "entfällt (vorher: 0 km)"


def test_english_counter_names() -> None:
    set_language("en")
    a = _later(minimal_session(), clear_counters=ClearCounters(warmups=3, distance_km=45))
    b = _later(a, clear_counters=ClearCounters(warmups=1, distance_km=50))
    texts = [
        f"{c.subject}: {change_text(c)}" for c in _section(compare_sessions(a, b), "clear_counters")
    ]
    assert texts == [
        "Warm-ups since codes cleared: 3 → 1",
        "Distance since codes cleared: 45 km → 50 km",
    ]


def test_voltage_step_counted_where_float_allows() -> None:
    """11,7 → 11,8 V (genau 0,1 V) zählt; siehe Fehler am Dateiende für andere Werte."""
    a = _scan(minimal_session(), voltage=11.7)
    (change,) = _section(compare_sessions(a, _scan(a, voltage=11.8)), "connection")
    assert change.trend is Trend.BETTER


# --- Verbindung ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("before", "after", "trend"),
    [
        (12.4, 11.7, Trend.WORSE),  # unter LOW_VOLTAGE (11,8 V)
        (11.9, 11.7, Trend.WORSE),
        (11.7, 12.6, Trend.BETTER),
        (11.5, 11.0, Trend.NEUTRAL),  # beide zu niedrig
        (12.6, 14.2, Trend.NEUTRAL),  # beide in Ordnung (Motor läuft)
        (11.8, 11.6, Trend.WORSE),  # 11,8 V gilt noch nicht als niedrig
        (11.6, 11.8, Trend.BETTER),
        (None, 11.5, Trend.WORSE),  # unbekannt -> niedrig
        (11.5, None, Trend.BETTER),
        (None, 12.6, Trend.NEUTRAL),
        (12.6, None, Trend.NEUTRAL),
    ],
)
def test_voltage_trend(before: float | None, after: float | None, trend: Trend) -> None:
    a = _scan(minimal_session(), voltage=before)
    b = _scan(a, voltage=after)
    (change,) = _section(compare_sessions(a, b), "connection")
    assert change.subject == "Bordspannung"
    assert (change.kind, change.trend) == (ChangeKind.CHANGED, trend)


def test_voltage_unknown_text() -> None:
    a = _scan(minimal_session(), voltage=None)
    b = _scan(a, voltage=12.0)
    (change,) = _section(compare_sessions(a, b), "connection")
    assert change_text(change) == "unbekannt → 12,0 V"


@pytest.mark.parametrize(("before", "after"), [(12.4, 12.45), (12.45, 12.4), (12.0, 12.09)])
def test_voltage_noise_below_tolerance(before: float, after: float) -> None:
    a = _scan(minimal_session(), voltage=before)
    assert _section(compare_sessions(a, _scan(a, voltage=after)), "connection") == []


def test_adapter_and_protocol_change() -> None:
    a = _scan(minimal_session())
    b = _scan(a, adapter="OBDLink SX", protocol="ISO 15765-4 (CAN 29/500)")
    assert _section(compare_sessions(a, b), "connection") == [
        Change(ChangeKind.CHANGED, "Adapter", "ELM327 v2.1", "OBDLink SX"),
        Change(ChangeKind.CHANGED, "Protokoll", "ISO 9141-2", "ISO 15765-4 (CAN 29/500)"),
    ]


# --- Fahrzeug und ältere Sitzungen ---------------------------------------------------


def test_vin_only_in_newer_session() -> None:
    a = dataclasses.replace(full_session(), vehicle=None)
    result = compare_sessions(a, _later(full_session()))
    assert result.same_vehicle is None
    assert _section(result, "vehicle") == [
        Change(ChangeKind.ADDED, "FIN", None, "WVWZZZ1KZ6W123456")
    ]


def test_same_vin_other_details_is_no_difference() -> None:
    """Nur die FIN zählt; andere Angaben (z. B. Online-Daten) ändern nichts."""
    session = full_session()
    assert session.vehicle is not None
    b = _later(session, vehicle=dataclasses.replace(session.vehicle, online={}))
    result = compare_sessions(session, b)
    assert result.same_vehicle is True and _section(result, "vehicle") == []


def test_both_without_vehicle() -> None:
    a = minimal_session()
    result = compare_sessions(a, _later(a))
    assert result.same_vehicle is None and _section(result, "vehicle") == []


def test_different_vehicles_still_compared_in_full() -> None:
    """Verschiedene FIN: Warnung, aber alle Abschnitte werden trotzdem verglichen."""
    a = full_session()
    assert a.vehicle is not None
    b = dataclasses.replace(
        minimal_session(),
        created=a.created + timedelta(days=1),
        vehicle=dataclasses.replace(a.vehicle, vin="WDD1770121N000000"),
    )
    result = compare_sessions(a, b)
    assert result.same_vehicle is False
    assert {s.key for s in result.sections if s.differences} == {
        "vehicle",
        "codes",
        "readiness",
        "freeze_frame",
        "clear_counters",
        "connection",
    }


def test_older_session_without_ecus_and_tests() -> None:
    """Sitzungen älterer Versionen ohne Steuergeräte/Testergebnisse vergleichen sich normal."""
    session = full_session()
    old = dataclasses.replace(session, ecus=None, tests=None, clear_counters=None)
    result = compare_sessions(old, _later(session))
    assert [s.key for s in result.sections if s.differences] == ["clear_counters"]


# --- Reihenfolge und Gesamtergebnis --------------------------------------------------


def test_equal_timestamps_keep_given_order() -> None:
    a = minimal_session()
    b = dataclasses.replace(a, scan=dataclasses.replace(a.scan, codes=_codes(("P0001", S))))
    result = compare_sessions(b, a)
    assert not result.swapped and result.before is b
    (change,) = _section(result, "codes")
    assert change.kind is ChangeKind.REMOVED


def test_timezones_are_compared_as_instants() -> None:
    """14:00 MESZ liegt vor 13:00 UTC: maßgeblich ist der Zeitpunkt, nicht die Uhrzeit."""
    cest = full_session()  # 14:32 +02:00 = 12:32 UTC
    utc = dataclasses.replace(cest, created=datetime(2026, 10, 7, 13, 0, tzinfo=UTC))
    result = compare_sessions(utc, cest)
    assert result.swapped and result.before is cest


def test_unchanged_codes_alone_are_no_difference() -> None:
    a = _scan(minimal_session(), codes=_codes(("P0420", S)))
    result = compare_sessions(a, _later(a))
    assert not result.has_differences
    codes = next(s for s in result.sections if s.key == "codes")
    assert codes.differences == () and len(codes.changes) == 1


def test_section_differences_filter() -> None:
    changes = (
        Change(ChangeKind.UNCHANGED, "a", "x", "x"),
        Change(ChangeKind.ADDED, "b", None, "y"),
    )
    assert Section("k", "K", changes).differences == changes[1:]


# --- Texte und JSON ------------------------------------------------------------------


def test_change_text_changed_with_missing_side_uses_arrow() -> None:
    assert change_text(Change(ChangeKind.CHANGED, "x", "1", "2")) == "1 → 2"


def test_english_change_texts() -> None:
    set_language("en")
    a = _scan(
        minimal_session(),
        voltage=12.6,
        codes=_codes(("P0300", S)),
    )
    a = dataclasses.replace(a, readiness=_readiness(("misfire", C)))
    b = _later(
        a,
        scan=dataclasses.replace(a.scan, voltage=11.5, codes=_codes(("P0171", P))),
        readiness=_readiness(("misfire", INC), mil_on=True),
        freeze_frame=FreezeFrame(dtc="P0171", values={"engine_load_pct": 34.5}),
    )
    result = compare_sessions(a, b)
    texts = {s.key: [f"{c.subject}: {change_text(c)}" for c in s.changes] for s in result.sections}
    assert texts["codes"] == ["P0171: new: pending", "P0300: gone (before: stored)"]
    assert texts["readiness"] == [
        "Check engine light (MIL): off → on",
        "Misfire: complete → incomplete",
        "All tests complete: yes → no",
    ]
    assert texts["freeze_frame"] == [
        "Triggering trouble code: new: P0171",
        "Engine load: new: 34.5 %",
    ]
    assert texts["connection"] == ["Battery voltage: 12.6 V → 11.5 V"]


def test_english_unknown_voltage_and_vin() -> None:
    set_language("en")
    a = _scan(minimal_session(), voltage=None)
    b = _later(a, vehicle=full_session().vehicle, scan=dataclasses.replace(a.scan, voltage=12.0))
    result = compare_sessions(a, b)
    (vin,) = _section(result, "vehicle")
    assert vin.subject == "VIN"
    (volt,) = _section(result, "connection")
    assert change_text(volt) == "unknown → 12.0 V"


def test_json_contains_every_section_and_unchanged_entries() -> None:
    a = _scan(minimal_session(), codes=_codes(("P0420", S)))
    data = comparison_to_dict(compare_sessions(_later(a), a))  # absichtlich vertauscht
    assert data["before"] == a.created.isoformat()
    assert data["has_differences"] is False and data["same_vehicle"] is None
    assert [s["key"] for s in data["sections"]] == [
        "vehicle",
        "codes",
        "readiness",
        "freeze_frame",
        "clear_counters",
        "connection",
    ]
    codes = data["sections"][1]
    assert codes["changes"] == [
        {
            "kind": "unchanged",
            "subject": "P0420",
            "before": "gespeichert",
            "after": "gespeichert",
            "trend": "neutral",
        }
    ]


# --- Eigenschaften (Hypothesis) ------------------------------------------------------

_CODES = ("P0101", "P0300", "P0420", "U0100", "C0035")
_MONITORS = ("misfire", "catalyst", "egr", "evap")

code_lists = st.lists(
    st.builds(DiagnosticCode, st.sampled_from(_CODES), st.sampled_from(list(DtcKind))),
    max_size=8,
)
readiness_strategy = st.none() | st.builds(
    ReadinessStatus,
    mil_on=st.booleans(),
    dtc_count=st.integers(0, 127),
    compression_ignition=st.booleans(),
    monitors=st.lists(
        st.tuples(st.sampled_from(_MONITORS), st.sampled_from(list(MonitorState))),
        unique_by=lambda m: m[0],
        max_size=4,
    ).map(lambda ms: tuple(Monitor(k, k, s) for k, s in ms)),
)
counter = st.none() | st.integers(0, 65535)
counters_strategy = st.none() | st.builds(
    ClearCounters, counter, counter, counter, counter, counter
)
freeze_strategy = st.none() | st.builds(
    FreezeFrame,
    dtc=st.none() | st.sampled_from(_CODES),
    values=st.dictionaries(
        st.sampled_from(["rpm", "speed_kmh", "coolant_temp_c", "engine_load_pct"]),
        st.integers(0, 255).map(float),
        max_size=4,
    ),
)


@st.composite
def sessions(draw: st.DrawFn) -> Session:
    return Session(
        created=draw(
            st.datetimes(datetime(2020, 1, 1), datetime(2030, 1, 1), timezones=st.just(UTC))
        ),
        scan=ScanResult(
            adapter=draw(st.sampled_from(["ELM327 v1.5", "ELM327 v2.1"])),
            protocol=draw(st.sampled_from(["ISO 9141-2", "ISO 15765-4 (CAN 11/500)"])),
            voltage=draw(st.none() | st.integers(100, 150).map(lambda v: v / 10)),
            codes=draw(code_lists),
        ),
        readiness=draw(readiness_strategy),
        freeze_frame=draw(freeze_strategy),
        clear_counters=draw(counters_strategy),
    )


_INVERSE = {
    ChangeKind.ADDED: ChangeKind.REMOVED,
    ChangeKind.REMOVED: ChangeKind.ADDED,
    ChangeKind.CHANGED: ChangeKind.CHANGED,
    ChangeKind.UNCHANGED: ChangeKind.UNCHANGED,
}
_OPPOSITE = {Trend.BETTER: Trend.WORSE, Trend.WORSE: Trend.BETTER, Trend.NEUTRAL: Trend.NEUTRAL}


@settings(max_examples=150, deadline=None)
@given(sessions())
def test_session_compared_with_itself_has_no_differences(session: Session) -> None:
    result = compare_sessions(session, session)
    assert not result.has_differences and not result.swapped
    for section in result.sections:
        assert all(c.kind is ChangeKind.UNCHANGED for c in section.changes)


@settings(max_examples=150, deadline=None)
@given(sessions(), sessions())
def test_argument_order_does_not_matter(a: Session, b: Session) -> None:
    """Die ältere Sitzung ist immer „vorher“, egal in welcher Reihenfolge übergeben."""
    if a.created == b.created:
        b = dataclasses.replace(b, created=a.created + timedelta(seconds=1))
    first, second = compare_sessions(a, b), compare_sessions(b, a)
    assert first.sections == second.sections
    assert first.swapped != second.swapped
    assert first.before.created < first.after.created


@settings(max_examples=150, deadline=None)
@given(sessions(), sessions())
def test_change_kinds_match_present_values(a: Session, b: Session) -> None:
    """Neu: nur nachher vorhanden; entfällt: nur vorher; geändert: beide, verschieden."""
    for section in compare_sessions(a, b).sections:
        for c in section.changes:
            if c.kind is ChangeKind.ADDED:
                assert c.before is None and c.after is not None
            elif c.kind is ChangeKind.REMOVED:
                assert c.before is not None and c.after is None
            elif c.kind is ChangeKind.CHANGED:
                assert c.before is not None and c.after is not None
            else:
                assert c.before == c.after and c.trend is Trend.NEUTRAL


@settings(max_examples=150, deadline=None)
@given(sessions(), sessions())
def test_codes_and_readiness_are_antisymmetric(a: Session, b: Session) -> None:
    """Rückwärts gelesen (vorher und nachher vertauscht) kehren sich Art und Tendenz um.

    Gilt für Fehlercodes und Readiness; die Zähler sind bewusst nicht symmetrisch
    (steigende Zähler sind normal, fallende heißen „gelöscht“).
    """
    a = dataclasses.replace(a, created=datetime(2026, 1, 1, tzinfo=UTC))
    b = dataclasses.replace(b, created=datetime(2026, 1, 2, tzinfo=UTC))
    forward = compare_sessions(a, b)
    backward = compare_sessions(
        dataclasses.replace(b, created=a.created), dataclasses.replace(a, created=b.created)
    )
    for key in ("codes", "readiness"):
        f = {c.subject: c for c in _section(forward, key)}
        r = {c.subject: c for c in _section(backward, key)}
        assert f.keys() == r.keys()
        for subject, c in f.items():
            assert r[subject].kind is _INVERSE[c.kind]
            assert r[subject].trend is _OPPOSITE[c.trend]
            assert (r[subject].before, r[subject].after) == (c.after, c.before)


@settings(max_examples=100, deadline=None)
@given(sessions(), sessions())
def test_json_form_is_serialisable(a: Session, b: Session) -> None:
    result = compare_sessions(a, b)
    data = json.loads(json.dumps(comparison_to_dict(result)))
    assert data["has_differences"] is result.has_differences
    assert sum(len(s["changes"]) for s in data["sections"]) == sum(
        len(s.changes) for s in result.sections
    )


# --- Kommandozeile -------------------------------------------------------------------


def test_cli_prints_paths_in_time_order_and_marks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old = _scan(minimal_session(), voltage=12.6, codes=_codes(("P0420", S)))
    new = _later(old, scan=dataclasses.replace(old.scan, voltage=11.5, codes=_codes(("P0420", P))))
    old_path, new_path = save_session(old, tmp_path), save_session(new, tmp_path)
    assert main(["compare", str(new_path), str(old_path), "--lang", "de"]) == 0
    out = capsys.readouterr().out
    assert out.index(f"  {old_path}") < out.index(f"  {new_path}")
    assert "Vorher:  2026-01-05 03:04" in out  # Uhrzeit wie gespeichert (UTC)
    assert "Fehlercodes:\n  ~ P0420: gespeichert → ausstehend\n" in out
    assert "Verbindung:\n  ~ Bordspannung: 12,6 V → 11,5 V\n" in out
    assert "Warnung" not in out  # keine FIN: keine Warnung
    # Abschnitte ohne Einträge erscheinen nicht
    assert "Readiness" not in out and "Fahrzeug" not in out


def test_cli_no_differences_german(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = save_session(minimal_session(), tmp_path)
    assert main(["compare", str(path), str(path), "--lang", "de"]) == 0
    out = capsys.readouterr().out
    assert out.endswith("\nKeine Unterschiede.\n")


def test_cli_english_warning_and_vin(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    a = full_session()
    assert a.vehicle is not None
    b = _later(a, vehicle=dataclasses.replace(a.vehicle, vin="WVWZZZ1KZ6W654321"))
    pa, pb = save_session(a, tmp_path), save_session(b, tmp_path)
    assert main(["--lang", "en", "compare", str(pa), str(pb)]) == 0
    out = capsys.readouterr().out
    assert "Warning: The sessions come from different vehicles (VIN differs).\n" in out
    assert "Vehicle:\n  ~ VIN: WVWZZZ1KZ6W123456 → WVWZZZ1KZ6W654321\n" in out
    assert "Before: " in out and "After: " in out


def test_cli_json_english_from_older_session_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Datei einer älteren Version ohne ``clear_counters``/``ecus``/``tests``."""
    old_path = save_session(full_session(), tmp_path)
    data = json.loads(old_path.read_text(encoding="utf-8"))
    for key in ("clear_counters", "ecus", "tests"):
        del data[key]
    old_path.write_text(json.dumps(data), encoding="utf-8")
    new_path = save_session(_later(full_session()), tmp_path)
    assert main(["compare", str(new_path), str(old_path), "--json", "--lang", "en"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["before"] == "2026-10-07T14:32:05+02:00"
    assert result["same_vehicle"] is True
    sections = {s["key"]: s for s in result["sections"]}
    assert sections["codes"]["title"] == "Trouble codes"
    counters = sections["clear_counters"]["changes"]
    assert [(c["kind"], c["after"]) for c in counters] == [
        ("added", "3"),
        ("added", "45 km"),
        ("added", "80 min"),
        ("added", "12 km"),
        ("added", "20 min"),
    ]
    assert [s["key"] for s in result["sections"] if s["changes"] and s["key"] != "codes"] == [
        "clear_counters"
    ]


def test_cli_json_keeps_unicode(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    a = _scan(minimal_session(), voltage=12.6)
    pa, pb = save_session(a, tmp_path), save_session(_scan(a, voltage=11.0), tmp_path)
    assert main(["compare", str(pa), str(pb), "--json", "--lang", "de"]) == 0
    out = capsys.readouterr().out
    assert '"before": "12,6 V"' in out and "\\u" not in out


# --- Spannungsschritt von genau 0,1 V ---------------------------------------------
# Früher ``abs(a - b) >= VOLTAGE_TOLERANCE`` mit Gleitkommazahlen: 12,5 - 12,4 ergibt
# 0,0999…96 < 0,1, ein echter Unterschied wurde je nach Wert erkannt oder nicht.


@pytest.mark.parametrize(
    ("before", "after"), [(12.4, 12.5), (12.0, 12.1), (11.8, 11.9), (14.4, 14.5), (12.5, 12.4)]
)
def test_voltage_step_of_exactly_tolerance_counts(before: float, after: float) -> None:
    """Ein Unterschied von genau 0,1 V (Auflösung von ATRV) ist kein Rauschen."""
    a = _scan(minimal_session(), voltage=before)
    (change,) = _section(compare_sessions(a, _scan(a, voltage=after)), "connection")
    assert change.subject == "Bordspannung"
