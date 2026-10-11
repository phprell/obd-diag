"""Sitzungsvergleich (``services/compare.py``) und ``obd-diag compare``."""

import dataclasses
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from obd_diag.cli import main
from obd_diag.i18n import set_language
from obd_diag.protocol.elm327 import Elm327
from obd_diag.protocol.obd import FreezeFrame
from obd_diag.services.compare import (
    SECTIONS,
    Change,
    ChangeKind,
    SessionComparison,
    Trend,
    change_text,
    compare_sessions,
    comparison_to_dict,
)
from obd_diag.services.diagnostics import DiagnosticCode, DtcKind
from obd_diag.services.readiness import MonitorState
from obd_diag.services.session import Session, run_diagnosis, save_session
from obd_diag.transport.demo import DemoTransport
from tests.samples import full_session, minimal_session


def _later(session: Session, **changes: object) -> Session:
    """``session`` drei Tage später, mit geänderten Feldern."""
    later = session.created + timedelta(days=3)
    return dataclasses.replace(session, created=later, **changes)  # type: ignore[arg-type]


def _section(comparison: SessionComparison, key: str) -> list[Change]:
    for s in comparison.sections:
        if s.key == key:
            return list(s.changes)
    raise KeyError(key)


def _codes(session: Session, codes: list[DiagnosticCode]) -> Session:
    return _later(session, scan=dataclasses.replace(session.scan, codes=codes))


def test_identical_sessions_have_no_differences() -> None:
    session = full_session()
    result = compare_sessions(session, session)
    assert not result.has_differences
    assert result.same_vehicle is True
    # Codes, die bleiben, stehen trotzdem da (sagt für sich etwas aus)
    codes = _section(result, "codes")
    assert {c.kind for c in codes} == {ChangeKind.UNCHANGED}
    assert [c.subject.split()[0] for c in codes] == ["P0133", "P0300", "P0420", "P1234"]
    p0420 = next(c for c in codes if c.subject.startswith("P0420"))
    assert p0420.after == "gespeichert, permanent"
    assert change_text(p0420) == "weiterhin gespeichert, permanent"
    assert all(not _section(result, key) for key in ("readiness", "freeze_frame", "vehicle"))


def test_new_cleared_and_confirmed_codes() -> None:
    before = full_session()
    codes = [c for c in before.scan.codes if c.code not in ("P0300", "P0133")]
    codes += [
        DiagnosticCode("P0133", DtcKind.STORED),  # ausstehend -> bestätigt
        DiagnosticCode("P0171", DtcKind.PENDING),  # neu
    ]
    result = compare_sessions(before, _codes(before, codes))
    by_code = {c.subject.split()[0]: c for c in _section(result, "codes")}
    assert by_code["P0171"] == Change(ChangeKind.ADDED, "P0171", None, "ausstehend", Trend.WORSE)
    assert change_text(by_code["P0171"]) == "neu: ausstehend"
    p0300 = by_code["P0300"]
    assert (p0300.kind, p0300.trend) == (ChangeKind.REMOVED, Trend.BETTER)
    assert change_text(p0300) == "entfällt (vorher: gespeichert)"
    p0133 = by_code["P0133"]
    assert (p0133.kind, p0133.trend) == (ChangeKind.CHANGED, Trend.WORSE)
    assert change_text(p0133) == "ausstehend → gespeichert"
    # Titel aus der Sitzung, die ihn hat (hier nur vorher)
    assert p0133.subject == "P0133  Lambdasonde langsames Ansprechverhalten (Bank 1, Sonde 1)"
    assert by_code["P0420"].kind is ChangeKind.UNCHANGED
    # Reihenfolge: neu, geändert, entfallen, unverändert
    assert [c.kind for c in _section(result, "codes")] == [
        ChangeKind.ADDED,
        ChangeKind.CHANGED,
        ChangeKind.REMOVED,
        ChangeKind.UNCHANGED,
        ChangeKind.UNCHANGED,
    ]


def test_stored_code_becoming_permanent_only_counts_as_better() -> None:
    """Nach dem Löschen bleibt ein Code oft als permanent stehen: kein Rückschritt."""
    before = full_session()
    after = _codes(before, [DiagnosticCode("P0300", DtcKind.PERMANENT)])
    p0300 = next(
        c for c in _section(compare_sessions(before, after), "codes") if "P0300" in c.subject
    )
    assert (p0300.before, p0300.after, p0300.trend) == ("gespeichert", "permanent", Trend.BETTER)


def test_pending_to_permanent_is_neutral() -> None:
    before = _codes(full_session(), [DiagnosticCode("P0001", DtcKind.PENDING)])
    after = _later(
        before,
        scan=dataclasses.replace(before.scan, codes=[DiagnosticCode("P0001", DtcKind.PERMANENT)]),
    )
    (change,) = _section(compare_sessions(before, after), "codes")
    assert change.trend is Trend.NEUTRAL


def test_order_follows_creation_time() -> None:
    older = full_session()
    newer = _codes(older, [])
    result = compare_sessions(newer, older)
    assert result.swapped
    assert result.before is older and result.after is newer
    assert {c.kind for c in _section(result, "codes")} == {ChangeKind.REMOVED}
    assert not compare_sessions(older, newer).swapped


def test_mixed_naive_and_aware_timestamps_keep_given_order() -> None:
    aware = full_session()
    naive = dataclasses.replace(aware, created=datetime(2020, 1, 1))
    result = compare_sessions(aware, naive)
    assert not result.swapped and result.before is aware


def test_readiness_changes() -> None:
    before = full_session()
    assert before.readiness is not None
    monitors = tuple(
        dataclasses.replace(m, state=MonitorState.COMPLETE)
        if m.state is MonitorState.INCOMPLETE
        else dataclasses.replace(m, state=MonitorState.INCOMPLETE)
        if m.key == "misfire"
        else m
        for m in before.readiness.monitors
    )
    readiness = dataclasses.replace(before.readiness, mil_on=False, dtc_count=0, monitors=monitors)
    changes = {
        c.subject: c
        for c in _section(
            compare_sessions(before, _later(before, readiness=readiness)), "readiness"
        )
    }
    assert changes["Kontrollleuchte (MIL)"] == Change(
        ChangeKind.CHANGED, "Kontrollleuchte (MIL)", "an", "aus", Trend.BETTER
    )
    assert changes["Gemeldete Fehlercodes"].trend is Trend.BETTER
    assert changes["Katalysator"].trend is Trend.BETTER
    assert changes["Verbrennungsaussetzer"] == Change(
        ChangeKind.CHANGED,
        "Verbrennungsaussetzer",
        "abgeschlossen",
        "nicht abgeschlossen",
        Trend.WORSE,
    )
    assert "Abgasrückführung" not in changes  # unverändert nicht unterstützt
    assert "Alle Tests abgeschlossen" not in changes  # vorher wie nachher nein


def test_all_complete_and_engine_type() -> None:
    before = full_session()
    assert before.readiness is not None
    monitors = tuple(
        dataclasses.replace(m, state=MonitorState.COMPLETE)
        if m.state is MonitorState.INCOMPLETE
        else m
        for m in before.readiness.monitors
    )
    after = _later(
        before,
        readiness=dataclasses.replace(
            before.readiness, monitors=monitors, compression_ignition=True
        ),
    )
    changes = {c.subject: c for c in _section(compare_sessions(before, after), "readiness")}
    assert changes["Alle Tests abgeschlossen"] == Change(
        ChangeKind.CHANGED, "Alle Tests abgeschlossen", "nein", "ja", Trend.BETTER
    )
    assert (changes["Motorart"].before, changes["Motorart"].after) == ("Otto", "Diesel")


def test_readiness_missing_on_one_side() -> None:
    before = full_session()
    (change,) = _section(compare_sessions(before, _later(before, readiness=None)), "readiness")
    assert change_text(change) == "entfällt (vorher: verfügbar)"
    (change,) = _section(
        compare_sessions(dataclasses.replace(before, readiness=None), _later(before)),
        "readiness",
    )
    assert change_text(change) == "neu: verfügbar"


def test_freeze_frame_changes() -> None:
    before = full_session()
    frame = FreezeFrame(
        dtc="P0420", values={"engine_load_pct": 34.5, "rpm": 800.0, "coolant_temp_c": 87.0}
    )
    changes = {
        c.subject: c
        for c in _section(
            compare_sessions(before, _later(before, freeze_frame=frame)), "freeze_frame"
        )
    }
    assert change_text(changes["Auslösender Fehlercode"]) == "P0300 → P0420"
    assert change_text(changes["Drehzahl"]) == "2140 1/min → 800 1/min"
    assert change_text(changes["Geschwindigkeit"]) == "entfällt (vorher: 63 km/h)"
    assert "Motorlast" not in changes
    # nach dem Löschen kein Freeze Frame mehr
    gone = {
        c.subject: c
        for c in _section(
            compare_sessions(before, _later(before, freeze_frame=None)), "freeze_frame"
        )
    }
    assert change_text(gone["Auslösender Fehlercode"]) == "entfällt (vorher: P0300)"
    assert change_text(gone["Motorlast"]) == "entfällt (vorher: 34,5 %)"


def test_vehicle_and_connection() -> None:
    before = full_session()
    assert before.vehicle is not None
    other = dataclasses.replace(before.vehicle, vin="WVWZZZ1KZ6W654321")
    scan = dataclasses.replace(before.scan, voltage=11.5, protocol="ISO 9141-2")
    result = compare_sessions(before, _later(before, vehicle=other, scan=scan))
    assert result.same_vehicle is False
    (vin,) = _section(result, "vehicle")
    assert (vin.before, vin.after) == ("WVWZZZ1KZ6W123456", "WVWZZZ1KZ6W654321")
    connection = {c.subject: c for c in _section(result, "connection")}
    assert change_text(connection["Bordspannung"]) == "12,4 V → 11,5 V"
    assert connection["Bordspannung"].trend is Trend.WORSE  # unter 11,8 V
    assert change_text(connection["Protokoll"]) == "ISO 15765-4 (CAN 11/500) → ISO 9141-2"
    assert "Adapter" not in connection
    # Messrauschen unter 0,1 V zählt nicht
    small = _later(before, scan=dataclasses.replace(before.scan, voltage=12.45))
    assert not _section(compare_sessions(before, small), "connection")


def test_missing_vin_gives_unknown_vehicle() -> None:
    result = compare_sessions(full_session(), _later(full_session(), vehicle=None))
    assert result.same_vehicle is None
    (vin,) = _section(result, "vehicle")
    assert vin.kind is ChangeKind.REMOVED


def test_minimal_sessions() -> None:
    a = minimal_session()
    result = compare_sessions(a, _later(a))
    assert not result.has_differences
    assert all(not s.changes for s in result.sections)


def test_sections_are_extensible() -> None:
    """Neue Sitzungsdaten bekommen eine eigene Funktion in ``SECTIONS``."""
    assert [key for key, _title, _fn in SECTIONS] == [
        "vehicle",
        "codes",
        "readiness",
        "freeze_frame",
        "connection",
    ]
    extra = ("extra", "Extra", lambda a, b: [Change(ChangeKind.CHANGED, "x", "1", "2")])
    SECTIONS.append(extra)
    try:
        result = compare_sessions(full_session(), full_session())
        assert result.sections[-1].title == "Extra" and result.has_differences
    finally:
        SECTIONS.remove(extra)


def test_english_texts() -> None:
    before = full_session()
    after = _codes(before, [DiagnosticCode("P0300", DtcKind.PERMANENT)])
    set_language("en")
    try:
        result = compare_sessions(before, after)
        p0300 = next(c for c in _section(result, "codes") if "P0300" in c.subject)
        assert change_text(p0300) == "stored → permanent"
        assert change_text(dataclasses.replace(p0300, kind=ChangeKind.ADDED)) == "new: permanent"
    finally:
        set_language("de")
    titles = [s.title for s in result.sections]
    assert titles == ["Vehicle", "Trouble codes", "Readiness", "Freeze frame", "Connection"]


def test_json_form() -> None:
    before = full_session()
    data = comparison_to_dict(compare_sessions(before, _codes(before, [])))
    assert data["before"] == before.created.isoformat()
    assert data["same_vehicle"] is True and data["has_differences"] is True
    codes = next(s for s in data["sections"] if s["key"] == "codes")
    assert codes["changes"][0]["kind"] == "removed"
    assert codes["changes"][0]["trend"] == "better"
    json.dumps(data)


def test_demo_vehicle_twice_shows_only_the_remaining_code() -> None:
    """Zwei Diagnosen am Demo-Fahrzeug (W177-Mitschnitte): U1218 bleibt, sonst nichts."""
    sessions = []
    for _ in range(2):
        with DemoTransport() as transport:
            sessions.append(run_diagnosis(Elm327(transport), None))
    result = compare_sessions(*sessions)
    assert result.same_vehicle is True
    codes = _section(result, "codes")
    assert [(c.kind, c.subject) for c in codes] == [(ChangeKind.UNCHANGED, "U1218")]
    assert not _section(result, "readiness") and not _section(result, "freeze_frame")


# --- Kommandozeile -------------------------------------------------------------------


@pytest.fixture
def two_files(tmp_path: Path) -> tuple[Path, Path]:
    before = full_session()
    codes = [c for c in before.scan.codes if c.code != "P0300"] + [
        DiagnosticCode("P0171", DtcKind.STORED)
    ]
    return save_session(before, tmp_path), save_session(_codes(before, codes), tmp_path)


def test_cli_compare(two_files: tuple[Path, Path], capsys: pytest.CaptureFixture[str]) -> None:
    old, new = two_files
    assert main(["compare", str(new), str(old), "--lang", "de"]) == 0  # Reihenfolge egal
    out, err = capsys.readouterr()
    assert err == ""
    assert f"Vorher:  2026-10-07 14:32  {old}\nNachher: 2026-10-10 14:32  {new}\n" in out
    assert "Fehlercodes:\n  + P0171: neu: gespeichert\n" in out
    assert (
        "  - P0300  Zufällige/mehrfache Zylinder-Verbrennungsaussetzer erkannt: "
        "entfällt (vorher: gespeichert)\n" in out
    )
    assert (
        "  = P0420  Katalysatorwirkungsgrad unter Schwellwert (Bank 1): "
        "weiterhin gespeichert, permanent\n" in out
    )
    assert "Readiness" not in out
    assert "Keine Unterschiede" not in out


def test_cli_compare_same_file_in_english(
    two_files: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    old, _new = two_files
    assert main(["--lang", "en", "compare", str(old), str(old)]) == 0
    out = capsys.readouterr().out
    assert "Before: " in out and "No differences." in out
    assert "Trouble codes:\n  = P0133" in out


def test_cli_compare_json(two_files: tuple[Path, Path], capsys: pytest.CaptureFixture[str]) -> None:
    old, new = two_files
    assert main(["compare", str(old), str(new), "--json", "--lang", "de"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["has_differences"] is True
    assert [s["key"] for s in data["sections"]][1] == "codes"


def test_cli_compare_warns_about_different_vehicles(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = full_session()
    assert before.vehicle is not None
    other = _later(before, vehicle=dataclasses.replace(before.vehicle, vin="WVWZZZ1KZ6W654321"))
    a, b = save_session(before, tmp_path), save_session(other, tmp_path)
    assert main(["compare", str(a), str(b), "--lang", "de"]) == 0
    assert "Warnung: Die Sitzungen stammen von verschiedenen Fahrzeugen" in capsys.readouterr().out


def test_cli_compare_bad_file(
    tmp_path: Path, two_files: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    broken = tmp_path / "kaputt.json"
    broken.write_text("{}", encoding="utf-8")
    assert main(["compare", str(two_files[0]), str(broken), "--lang", "de"]) == 1
    assert "Keine obd-diag-Diagnosesitzung" in capsys.readouterr().err
    assert main(["compare", str(tmp_path / "fehlt.json"), str(broken), "--lang", "de"]) == 1
    assert "fehlt.json" in capsys.readouterr().err
