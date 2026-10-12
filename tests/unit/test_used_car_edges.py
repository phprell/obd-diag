"""Gebrauchtwagen-Check: Randfälle beim Lesen (J1979-Bytes, mehrere Steuergeräte,
Ablehnungen, Adapter- und Verbindungsfehler), Grenzen der Bewertung, Texte auf Deutsch
und Englisch, Kommandozeile, PDF und Sitzungsdateien."""

import dataclasses
import json
import re
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pypdf import PdfReader

from obd_diag import cli
from obd_diag.export import report
from obd_diag.export.report import export_pdf
from obd_diag.i18n import set_language
from obd_diag.protocol.elm327 import Elm327, ElmError
from obd_diag.services.diagnostics import DiagnosticCode, DtcKind, ScanResult
from obd_diag.services.readiness import ReadinessStatus, decode_readiness
from obd_diag.services.session import (
    Session,
    load_session,
    run_diagnosis,
    save_session,
    session_from_dict,
    session_to_dict,
)
from obd_diag.services.used_car import (
    RECENT_DISTANCE_KM,
    RECENT_TIME_MIN,
    RECENT_WARMUPS,
    ClearCounters,
    Finding,
    Level,
    Verdict,
    assess,
    counter_rows,
    read_clear_counters,
    read_used_car,
)
from obd_diag.transport import TransportError
from tests.fakes import CAN_CAR, CAN_CAR_FULL, FakeTransport
from tests.samples import CLEAR_COUNTERS, full_session, minimal_session

COUNTER_COMMANDS = ["0130", "0131", "014E", "0121", "014D"]

# Readiness (PID 01, Bytes A-D nach J1979): Otto ohne MIL, alles abgeschlossen; Otto mit
# Katalysator, Tankentlüftung und Lambdasonde offen; Otto mit offenem Aussetzer-Monitor
# (Byte B Bit 4); Diesel (Byte B Bit 3) mit NMHC-Katalysator und Partikelfilter offen;
# MIL an ohne offene Tests
READY = decode_readiness(bytes.fromhex("00076500"))
THREE_OPEN = decode_readiness(bytes.fromhex("00076525"))
MISFIRE_OPEN = decode_readiness(bytes.fromhex("00176500"))
DIESEL_OPEN = decode_readiness(bytes.fromhex("000F6141"))
MIL_ON = decode_readiness(bytes.fromhex("81076500"))

NO_CODES = ScanResult(adapter="ELM327 v1.5", protocol="ISO 15765-4 (CAN 11/500)", voltage=12.4)
OLD = ClearCounters(warmups=40, distance_km=3000, time_min=600, mil_distance_km=0, mil_time_min=0)


def _scan(*codes: tuple[str, DtcKind]) -> ScanResult:
    return dataclasses.replace(NO_CODES, codes=[DiagnosticCode(c, k) for c, k in codes])


def _read(responses: dict[str, str]) -> tuple[ClearCounters, list[str]]:
    transport = FakeTransport(responses)
    return read_clear_counters(Elm327(transport)), transport.sent


def _findings(
    counters: ClearCounters | None,
    scan: ScanResult = NO_CODES,
    readiness: ReadinessStatus | None = READY,
) -> list[tuple[Level, str]]:
    return [(f.level, f.text) for f in assess(counters, scan, readiness).findings]


class _BrokenTransport(FakeTransport):
    """Wie ``FakeTransport``, aber bei ``broken`` reißt die Verbindung ab."""

    def __init__(self, responses: dict[str, str], broken: str) -> None:
        super().__init__(responses)
        self.broken = broken

    def write(self, data: bytes) -> None:
        super().write(data)
        if data.decode("ascii").strip() == self.broken:
            raise TransportError("Verbindung zum Adapter unterbrochen")


# --- Dekodieren nach J1979 ---------------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("413000", 0),
        ("4130FF", 255),  # größter Wert von PID 30 (ein Byte A)
        ("41 30 0A", 10),  # mit Leerzeichen (ATS1)
        ("4130FF00", 255),  # weitere Bytes danach zählen nicht
    ],
)
def test_warmups_is_byte_a(answer: str, expected: int) -> None:
    counters, _ = _read({"0130": answer})
    assert counters.warmups == expected


@pytest.mark.parametrize(
    ("cmd", "field"),
    [
        ("0131", "distance_km"),
        ("014E", "time_min"),
        ("0121", "mil_distance_km"),
        ("014D", "mil_time_min"),
    ],
)
@pytest.mark.parametrize(
    ("data", "expected"),
    [("0000", 0), ("0001", 1), ("0100", 256), ("FFFF", 65535), ("1234", 0x1234)],
)
def test_word_counters_are_256a_plus_b(cmd: str, field: str, data: str, expected: int) -> None:
    counters, _ = _read({cmd: f"41{cmd[2:]}{data}"})
    assert dataclasses.asdict(counters)[field] == expected


@pytest.mark.parametrize(
    "answer",
    [
        "413100",  # nur ein Datenbyte statt zwei
        "4231002D",  # Mode 02 statt 01
        "4130002D",  # Antwort auf eine andere PID
        "7F013112",  # abgelehnt: Dienst nicht unterstützt
        "41",
    ],
)
def test_unusable_distance_answers_give_none(answer: str) -> None:
    counters, _ = _read({"0131": answer})
    assert counters.distance_km is None


@given(st.lists(st.integers(0, 0xFFFF), min_size=1, max_size=4))
def test_since_clear_takes_smallest_with_mil_largest_of_all_ecus(values: list[int]) -> None:
    """Je Steuergerät eine Zeile ohne Header, beliebige Reihenfolge."""
    lines = {
        cmd: "\r".join(f"41{cmd[2:]}{v:04X}" for v in values)
        for cmd in ("0131", "014E", "0121", "014D")
    }
    counters, _ = _read(lines)
    assert counters.distance_km == counters.time_min == min(values)
    assert counters.mil_distance_km == counters.mil_time_min == max(values)


@given(st.lists(st.integers(0, 0xFF), min_size=1, max_size=4))
def test_warmups_smallest_of_all_ecus(values: list[int]) -> None:
    counters, _ = _read({"0130": "\r".join(f"4130{v:02X}" for v in values)})
    assert counters.warmups == min(values)


def test_one_ecu_rejects_the_other_answers() -> None:
    """Motor antwortet, Getriebe lehnt ab (7F 01 12): der Wert des Motors zählt."""
    counters, _ = _read({"0131": "7F013112\r41310BB8", "0121": "41210005\r7F012112"})
    assert counters.distance_km == 3000
    assert counters.mil_distance_km == 5


def test_no_data_line_among_answers_is_skipped() -> None:
    counters, _ = _read({"0130": "413005\rNO DATA"})
    assert counters.warmups == 5


def test_zero_mil_counter_of_one_ecu_does_not_hide_the_other() -> None:
    counters, _ = _read({"0121": "41210000\r41210096"})
    assert counters.mil_distance_km == 150


# --- Fehler und Befehlsfolge -------------------------------------------------------


@pytest.mark.parametrize(
    "answer",
    ["?", "NO DATA", "CAN ERROR", "BUFFER FULL", "413003\rSTOPPED", "UNABLE TO CONNECT"],
)
def test_adapter_errors_leave_only_that_counter_empty(answer: str) -> None:
    """Auch Teildaten vor ``STOPPED`` zählen nicht: die Antwort ist unvollständig."""
    responses = {**CAN_CAR_FULL, "0130": answer}
    counters, sent = _read(responses)
    assert counters == dataclasses.replace(CLEAR_COUNTERS, warmups=None)
    assert sent == COUNTER_COMMANDS


def test_errors_everywhere_still_exactly_five_requests() -> None:
    """Kein Wiederholen, keine Header, kein anderer Befehl."""
    counters, sent = _read(dict.fromkeys(COUNTER_COMMANDS, "?"))
    assert counters == ClearCounters()
    assert sent == COUNTER_COMMANDS


@pytest.mark.parametrize("broken", COUNTER_COMMANDS)
def test_transport_error_aborts_reading(broken: str) -> None:
    transport = _BrokenTransport(CAN_CAR_FULL, broken)
    with pytest.raises(TransportError):
        read_clear_counters(Elm327(transport))
    assert transport.sent == COUNTER_COMMANDS[: COUNTER_COMMANDS.index(broken) + 1]


def test_transport_error_aborts_used_car_reading() -> None:
    with pytest.raises(TransportError):
        read_used_car(Elm327(_BrokenTransport(CAN_CAR_FULL, "014E")))


def test_transport_error_aborts_full_diagnosis() -> None:
    """In der Diagnose ist ein Abbruch kein „Zähler nicht gemeldet“."""
    with pytest.raises(TransportError):
        run_diagnosis(Elm327(_BrokenTransport(CAN_CAR_FULL, "0131")), None)


def test_rejected_readiness_does_not_stop_the_counters() -> None:
    transport = FakeTransport({**CAN_CAR_FULL, "0101": "NO DATA"})
    reading = read_used_car(Elm327(transport))
    assert reading.readiness is None
    assert reading.counters == CLEAR_COUNTERS
    assert transport.sent[-6:] == ["0101", *COUNTER_COMMANDS]


def test_error_type_is_elm_error() -> None:
    """Grundlage für „nur dieser Zähler leer“: ``?`` ist ein ``ElmError``."""
    with pytest.raises(ElmError):
        Elm327(FakeTransport({"0130": "?"})).command("0130")


# --- Bewerten: Grenzen ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("counters", "recent"),
    [
        (ClearCounters(warmups=RECENT_WARMUPS - 1), True),
        (ClearCounters(warmups=RECENT_WARMUPS), False),
        (ClearCounters(distance_km=RECENT_DISTANCE_KM - 1), True),
        (ClearCounters(distance_km=RECENT_DISTANCE_KM), False),
        (ClearCounters(time_min=RECENT_TIME_MIN - 1), True),
        (ClearCounters(time_min=RECENT_TIME_MIN), False),
        # ein einziger kurzer Zähler genügt, auch wenn die anderen lang sind
        (ClearCounters(warmups=255, distance_km=65535, time_min=RECENT_TIME_MIN - 1), True),
        (ClearCounters(warmups=0, distance_km=65535, time_min=65535), True),
    ],
)
def test_threshold_boundaries(counters: ClearCounters, recent: bool) -> None:
    check = assess(counters, NO_CODES, READY)
    assert check.recently_cleared is recent
    assert check.verdict is (Verdict.SUSPICIOUS if recent else Verdict.INCONSPICUOUS)


@given(
    st.one_of(st.none(), st.integers(0, 255)),
    st.one_of(st.none(), st.integers(0, 65535)),
    st.one_of(st.none(), st.integers(0, 65535)),
)
def test_recent_exactly_when_one_counter_below_its_limit(
    warmups: int | None, distance: int | None, time: int | None
) -> None:
    counters = ClearCounters(warmups=warmups, distance_km=distance, time_min=time)
    check = assess(counters, NO_CODES, READY)
    expected = (
        (warmups is not None and warmups < RECENT_WARMUPS)
        or (distance is not None and distance < RECENT_DISTANCE_KM)
        or (time is not None and time < RECENT_TIME_MIN)
    )
    assert check.recently_cleared is expected
    if expected:
        assert check.verdict is Verdict.SUSPICIOUS
    elif counters.available:
        assert check.verdict is Verdict.INCONSPICUOUS
    else:
        assert check.verdict is Verdict.UNKNOWN
    assert check.findings[0].level is (
        Level.WARNING if expected else Level.OK if counters.available else Level.INFO
    )


def test_only_mil_counters_is_not_assessable() -> None:
    """Strecke/Zeit mit Warnleuchte sagen nichts über das Löschen."""
    check = assess(ClearCounters(mil_distance_km=0, mil_time_min=0), NO_CODES, None)
    assert check.verdict is Verdict.UNKNOWN
    assert [f.level for f in check.findings] == [Level.INFO, Level.OK, Level.OK]
    assert check.findings[1].text == "Motorkontrollleuchte aus."


def test_without_readiness_and_mil_counters_no_mil_finding() -> None:
    findings = _findings(ClearCounters(warmups=40), readiness=None)
    assert [text for _, text in findings] == [
        "Nicht vor kurzem gelöscht: 40 Warmläufe seit dem letzten Löschen.",
        "Keine permanenten Fehlercodes gemeldet.",
    ]


def test_inconspicuous_lists_only_reported_counters() -> None:
    findings = _findings(ClearCounters(distance_km=12345, time_min=65535))
    assert findings[0] == (
        Level.OK,
        "Nicht vor kurzem gelöscht: 12.345 km, 1.092 Stunden seit dem letzten Löschen.",
    )


def test_recent_lists_only_the_short_counters() -> None:
    findings = _findings(ClearCounters(warmups=40, distance_km=150, time_min=600))
    assert findings[0] == (
        Level.WARNING,
        "Fehlercodes vor kurzem gelöscht: erst 150 km seit dem Löschen.",
    )


@pytest.mark.parametrize(
    ("minutes", "text"),
    [
        (0, "0 Minuten"),
        (1, "1 Minute"),
        (59, "59 Minuten"),
        (119, "119 Minuten"),
        (120, "2 Stunden"),
        (179, "2 Stunden"),  # abgerundet
        (299, "4 Stunden"),
    ],
)
def test_recent_duration_text(minutes: int, text: str) -> None:
    findings = _findings(ClearCounters(time_min=minutes))
    assert findings[0][1] == f"Fehlercodes vor kurzem gelöscht: erst {text} seit dem Löschen."


# --- Bewerten: Warnleuchte, Readiness, permanente Codes ----------------------------


def test_mil_on_without_mil_counters_no_driven_text() -> None:
    findings = _findings(ClearCounters(warmups=40, mil_distance_km=0), readiness=MIL_ON)
    assert (Level.WARNING, "Die Motorkontrollleuchte ist an.") in findings


def test_mil_on_with_only_time() -> None:
    findings = _findings(ClearCounters(warmups=40, mil_time_min=180), readiness=MIL_ON)
    assert (
        Level.WARNING,
        "Die Motorkontrollleuchte ist an. Gefahren mit Warnleuchte: 3 Stunden.",
    ) in findings


def test_mil_was_on_only_time_from_counters_without_readiness() -> None:
    check = assess(ClearCounters(warmups=40, mil_time_min=1), NO_CODES, None)
    assert check.verdict is Verdict.SUSPICIOUS
    assert (
        Finding(
            Level.WARNING,
            "Die Motorkontrollleuchte war seit dem letzten Löschen an: 1 Minute mit "
            "Warnleuchte, jetzt ist sie aus.",
        )
        in check.findings
    )


def test_mil_was_on_large_counters_with_separator() -> None:
    findings = _findings(ClearCounters(warmups=40, mil_distance_km=65535, mil_time_min=65535))
    assert (
        Level.WARNING,
        "Die Motorkontrollleuchte war seit dem letzten Löschen an: 65.535 km, 1.092 Stunden "
        "mit Warnleuchte, jetzt ist sie aus.",
    ) in findings


@pytest.mark.parametrize(
    ("readiness", "names"),
    [
        (THREE_OPEN, "3 Readiness-Tests nicht abgeschlossen: Katalysator, Tankentlüftung, "),
        (MISFIRE_OPEN, "1 Readiness-Test nicht abgeschlossen: Verbrennungsaussetzer. "),
        (DIESEL_OPEN, "2 Readiness-Tests nicht abgeschlossen: NMHC-Katalysator, Partikel"),
    ],
)
def test_open_monitors_named_in_order(readiness: ReadinessStatus, names: str) -> None:
    findings = _findings(OLD, readiness=readiness)
    info = [text for level, text in findings if level is Level.INFO]
    assert len(info) == 1
    assert info[0].startswith(names)


def test_three_open_monitors_with_recent_clear_full_text() -> None:
    findings = _findings(ClearCounters(warmups=1), readiness=THREE_OPEN)
    assert findings[1] == (
        Level.INFO,
        "Passend dazu sind 3 Readiness-Tests nicht abgeschlossen: Katalysator, "
        "Tankentlüftung, Lambdasonde.",
    )


def test_unsupported_monitors_are_not_open() -> None:
    """Nicht unterstützte Monitore (Byte C ohne Bit) zählen nie als offen."""
    readiness = decode_readiness(bytes.fromhex("000700FF"))
    assert [level for level, _ in _findings(OLD, readiness=readiness)] == [
        Level.OK,
        Level.OK,
        Level.OK,
    ]


def test_single_permanent_code_singular_and_dedup() -> None:
    scan = _scan(
        ("P0420", DtcKind.PERMANENT),
        ("P0420", DtcKind.PERMANENT),  # von zwei Steuergeräten
        ("P0300", DtcKind.STORED),
        ("P0171", DtcKind.PENDING),
    )
    findings = _findings(OLD, scan=scan)
    assert findings[-1] == (
        Level.WARNING,
        "Permanenter Fehlercode: P0420. Er lässt sich nicht löschen; das Steuergerät "
        "entfernt ihn erst, wenn es den Fehler nicht mehr feststellt.",
    )


def test_stored_and_pending_codes_alone_are_no_finding() -> None:
    """Der Check bewertet nur permanente Codes; gespeicherte zeigt der Scan."""
    scan = _scan(("P0300", DtcKind.STORED), ("P0171", DtcKind.PENDING))
    check = assess(OLD, scan, READY)
    assert check.verdict is Verdict.INCONSPICUOUS
    assert check.findings[-1] == Finding(Level.OK, "Keine permanenten Fehlercodes gemeldet.")


def test_assess_on_full_session_order_of_findings() -> None:
    session = full_session()
    check = assess(session.clear_counters, session.scan, session.readiness)
    assert check.verdict is Verdict.SUSPICIOUS
    assert check.recently_cleared
    # gelöscht, Tests offen, Warnleuchte an, P0420 permanent
    assert [f.level for f in check.findings] == [
        Level.WARNING,
        Level.INFO,
        Level.WARNING,
        Level.WARNING,
    ]


# --- Zeilen der Zähler ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("counters", "row", "value"),
    [
        (ClearCounters(warmups=254), 0, "254"),
        (ClearCounters(warmups=0), 0, "0"),
        (ClearCounters(distance_km=0), 1, "0 km"),
        (ClearCounters(distance_km=1000), 1, "1.000 km"),
        (ClearCounters(time_min=119), 2, "119 Minuten"),
        (ClearCounters(time_min=120), 2, "2 Stunden"),
        (ClearCounters(time_min=65535), 2, "1.092 Stunden"),
        (ClearCounters(mil_distance_km=65535), 3, "65.535 km"),
        (ClearCounters(mil_time_min=1), 4, "1 Minute"),
        (ClearCounters(), 4, "nicht gemeldet"),
    ],
)
def test_counter_rows_values(counters: ClearCounters, row: int, value: str) -> None:
    assert counter_rows(counters)[row][1] == value


def test_counter_rows_labels_in_fixed_order() -> None:
    assert [label for label, _ in counter_rows(ClearCounters())] == [
        "Warmläufe seit dem Löschen",
        "Strecke seit dem Löschen",
        "Zeit seit dem Löschen",
        "Strecke mit Warnleuchte an",
        "Zeit mit Warnleuchte an",
    ]


def test_counter_rows_english() -> None:
    set_language("en")
    rows = counter_rows(
        ClearCounters(
            warmups=255, distance_km=65535, time_min=65535, mil_distance_km=0, mil_time_min=1
        )
    )
    assert rows == [
        ("Warm-ups since codes cleared", "255 or more"),
        ("Distance since codes cleared", "65,535 km"),
        ("Time since codes cleared", "1,092 hours"),
        ("Distance with MIL on", "0 km"),
        (rows[4][0], "1 minute"),
    ]


def test_english_findings() -> None:
    set_language("en")
    check = assess(
        ClearCounters(warmups=1, distance_km=5000, mil_distance_km=2),
        _scan(("P0420", DtcKind.PERMANENT)),
        THREE_OPEN,
    )
    assert check.label == "Suspicious"
    assert [f.text for f in check.findings] == [
        "Trouble codes cleared recently: only 1 warm-up since clearing.",
        "Consistent with this, 3 readiness tests are incomplete: Catalyst, "
        "Evaporative system, Oxygen sensor.",
        "The check engine light (MIL) was on since the codes were last cleared: 2 km with "
        "the MIL on, now it is off.",
        "Permanent trouble code: P0420. It cannot be cleared; the control unit removes it "
        "only once it no longer detects the fault.",
    ]


@pytest.mark.parametrize(
    ("verdict_counters", "label"),
    [(OLD, "Inconspicuous"), (None, "Cannot be assessed")],
)
def test_english_labels(verdict_counters: ClearCounters | None, label: str) -> None:
    set_language("en")
    assert assess(verdict_counters, NO_CODES, READY).label == label


def test_language_switch_needs_new_assess() -> None:
    """Befundtexte entstehen beim Bewerten, das Urteil-Label bei der Anzeige."""
    check = assess(OLD, NO_CODES, READY)
    set_language("en")
    assert check.findings[-1].text == "Keine permanenten Fehlercodes gemeldet."
    assert check.label == "Inconspicuous"
    assert assess(OLD, NO_CODES, READY).findings[-1].text == "No permanent trouble codes reported."


# --- Sitzung -------------------------------------------------------------------------


def test_session_with_partial_counters_round_trip(tmp_path: Path) -> None:
    counters = ClearCounters(warmups=40, distance_km=3000, mil_distance_km=0)
    session = dataclasses.replace(minimal_session(), clear_counters=counters)
    data = session_to_dict(session)
    assert data["clear_counters"] == {
        "warmups": 40,
        "distance_km": 3000,
        "time_min": None,
        "mil_distance_km": 0,
        "mil_time_min": None,
    }
    assert load_session(save_session(session, tmp_path)).clear_counters == counters


def test_session_counters_missing_keys_are_none() -> None:
    data = session_to_dict(full_session())
    data["clear_counters"] = {"distance_km": 45}
    assert session_from_dict(data).clear_counters == ClearCounters(distance_km=45)


def test_session_counters_numbers_as_text() -> None:
    data = session_to_dict(full_session())
    data["clear_counters"] = {"warmups": "3", "distance_km": 45.0}
    assert session_from_dict(data).clear_counters == ClearCounters(warmups=3, distance_km=45)


@pytest.mark.parametrize("broken", [{"warmups": "drei"}, ["warmups", 3], "45"])
def test_session_broken_counters_are_value_error(broken: object) -> None:
    data = session_to_dict(full_session())
    data["clear_counters"] = broken
    with pytest.raises(ValueError, match="unvollständig oder beschädigt"):
        session_from_dict(data)


def test_empty_counters_survive_and_stay_distinct_from_none() -> None:
    """Gelesen, aber nichts gemeldet (``ClearCounters()``) ist nicht „nicht gelesen“."""
    session = dataclasses.replace(minimal_session(), clear_counters=ClearCounters())
    restored = session_from_dict(json.loads(json.dumps(session_to_dict(session))))
    assert restored.clear_counters == ClearCounters()


# --- Kommandozeile -------------------------------------------------------------------


def _cli(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    responses: dict[str, str],
    *args: str,
) -> tuple[int, str, str]:
    monkeypatch.setattr(cli, "SerialTransport", lambda port, baud: FakeTransport(responses))
    code = cli.main(list(args))
    out, err = capsys.readouterr()
    return code, out, err


def test_cli_nothing_reported(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = _cli(monkeypatch, capsys, {**CAN_CAR, "0101": "NO DATA"}, "used-car")
    assert code == 0
    assert out.startswith(
        "Gebrauchtwagen-Check: Nicht beurteilbar\n"
        "  Warmläufe seit dem Löschen: nicht gemeldet\n"
        "  Strecke seit dem Löschen:   nicht gemeldet\n"
        "  Zeit seit dem Löschen:      nicht gemeldet\n"
        "  Strecke mit Warnleuchte an: nicht gemeldet\n"
        "  Zeit mit Warnleuchte an:    nicht gemeldet\n"
        "  [i] Das Fahrzeug meldet nicht, wie lange das Löschen der Fehlercodes her ist\n"
        "      (PID 30, 31, 4E); ob kürzlich gelöscht wurde, lässt sich nicht sagen.\n"
        "  [ok] Keine permanenten Fehlercodes gemeldet.\n"
        "  Hinweis: Faustregel, kein Beweis:"
    )


def test_cli_old_clear_english(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    responses = {
        **CAN_CAR,
        "0101": "4101 0007 6500",
        "0130": "413028",
        "0131": "41310BB8",
        "014E": "414E0258",
        "0121": "41210000",
        "014D": "414D0000",
    }
    code, out, _ = _cli(monkeypatch, capsys, responses, "--lang", "en", "used-car")
    assert code == 0
    lines = out.splitlines()
    assert lines[0] == "Used-car check: Inconspicuous"
    assert "  Distance since codes cleared: 3,000 km" in lines
    assert (
        "  [ok] Not cleared recently: 40 warm-ups, 3,000 km, 10 hours since the codes were" in lines
    )
    assert "  [ok] Check engine light (MIL) off." in lines
    assert any(line.startswith("  Note: Rule of thumb, not proof") for line in lines)


def test_cli_json_permanent_codes_and_partial_counters(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    responses = {
        **CAN_CAR,
        "0A": "4A0204200300",  # Mode 0A ohne Header: P0420, P0300 (CAN-Zählbyte 02)
        "0101": "NO DATA",
        "0130": "4130FF",
        "0131": "4131FFFF",
    }
    code, out, _ = _cli(monkeypatch, capsys, responses, "used-car", "--json")
    assert code == 0
    data = json.loads(out)
    assert data["verdict"] == "suspicious"
    assert data["recently_cleared"] is False
    assert data["clear_counters"] == {
        "warmups": 255,
        "distance_km": 65535,
        "time_min": None,
        "mil_distance_km": None,
        "mil_time_min": None,
    }
    assert data["permanent_codes"] == ["P0420", "P0300"]
    assert [f["level"] for f in data["findings"]] == ["ok", "warning"]
    assert data["findings"][1]["text"].startswith("Permanente Fehlercodes: P0300, P0420.")


def test_cli_transport_error_exit_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        cli, "SerialTransport", lambda port, baud: _BrokenTransport(CAN_CAR_FULL, "0131")
    )
    assert cli.main(["used-car", "--json"]) == 1
    out, err = capsys.readouterr()
    assert out == ""  # kein halbes JSON
    assert err.startswith("Fehler: Verbindung zum Adapter unterbrochen")


def test_cli_unable_to_connect_hint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    responses = {"ATZ": "ELM327 v1.5", "0100": "SEARCHING...\rUNABLE TO CONNECT"}
    code, out, err = _cli(monkeypatch, capsys, responses, "used-car")
    assert code == 1
    assert out == ""
    assert "Hinweis: Kein Steuergerät antwortet." in err


def test_cli_demo_json(capsys: pytest.CaptureFixture[str]) -> None:
    """Demo-Fahrzeug: 21, 30, 31 gemeldet (Werte erfunden), 4D/4E nicht."""
    assert cli.main(["used-car", "--demo", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["clear_counters"] == {
        "warmups": 40,
        "distance_km": 3000,
        "time_min": None,
        "mil_distance_km": 0,
        "mil_time_min": None,
    }
    assert data["recently_cleared"] is False


# --- PDF -----------------------------------------------------------------------------


def _pdf_text(session: Session, path: Path) -> str:
    export_pdf(session, path)
    text = "\n".join(page.extract_text() for page in PdfReader(path).pages)
    return re.sub(r"\s+", " ", text)


@pytest.fixture
def helvetica(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(report, "find_font_files", lambda: None)


@pytest.mark.usefixtures("helvetica")
def test_pdf_old_session_without_counters_but_permanent(tmp_path: Path) -> None:
    session = dataclasses.replace(full_session(), clear_counters=None)
    text = _pdf_text(session, tmp_path / "alt.pdf")
    assert "Gebrauchtwagen-Check Ergebnis Auffällig" in text
    assert "Warmläufe seit dem Löschen" not in text
    assert "Das Fahrzeug meldet nicht, wie lange das Löschen der Fehlercodes her ist" in text
    assert "Permanenter Fehlercode: P0420." in text


@pytest.mark.usefixtures("helvetica")
def test_pdf_inconspicuous_counters(tmp_path: Path) -> None:
    session = dataclasses.replace(minimal_session(), readiness=READY, clear_counters=OLD)
    text = _pdf_text(session, tmp_path / "ok.pdf")
    for snippet in (
        "Gebrauchtwagen-Check Ergebnis Unauffällig",
        "Warmläufe seit dem Löschen 40",
        "Strecke seit dem Löschen 3.000 km",
        "Zeit seit dem Löschen 10 Stunden",
        "Strecke mit Warnleuchte an 0 km",
        "Zeit mit Warnleuchte an 0 Minuten",
        "Motorkontrollleuchte aus.",
        "Faustregel, kein Beweis",
    ):
        assert snippet in text, snippet


@pytest.mark.usefixtures("helvetica")
def test_pdf_english(tmp_path: Path) -> None:
    set_language("en")
    text = _pdf_text(full_session(), tmp_path / "en.pdf")
    for snippet in (
        "Used-car check Result Suspicious",
        "Warm-ups since codes cleared 3",
        "Trouble codes cleared recently: only 3 warm-ups, 45 km, 80 minutes since clearing.",
        "Rule of thumb, not proof",
    ):
        assert snippet in text, snippet
