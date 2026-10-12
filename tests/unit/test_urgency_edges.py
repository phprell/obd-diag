"""Dringlichkeit (``services.urgency``): jede Regel mit Begründung in beiden Sprachen,
Vorrang der Regeln, Grenzen der J2012-Codegruppen und die Ausgabe in CLI und CSV.

Die Dringlichkeit wird nur berechnet (nicht gespeichert) und sendet nichts.
"""

import csv
import dataclasses
import json
from pathlib import Path

import pytest

from obd_diag import cli
from obd_diag.cli import main
from obd_diag.data.dtc_catalog import DtcCatalog
from obd_diag.export.report import export_csv
from obd_diag.i18n import set_language, tr
from obd_diag.services import vehicle
from obd_diag.services.diagnostics import DiagnosticCode, DtcKind
from obd_diag.services.session import load_session, save_session
from obd_diag.services.urgency import (
    URGENCY_LABELS,
    URGENCY_NOTE,
    Urgency,
    UrgencyRating,
    highest,
    rate_code,
    rate_codes,
)
from tests.fakes import CAN_CAR, CAN_CAR_FULL, FakeCatalog, FakeTransport
from tests.samples import full_session

STORED, PENDING, PERMANENT = DtcKind.STORED, DtcKind.PENDING, DtcKind.PERMANENT

# Begründungen: deutscher Anfang und englischer Anfang (aus en.po)
OVERHEAT = ("Der Motor meldet Übertemperatur.", "The engine reports overheating.")
GEARBOX = ("Das Getriebe meldet Übertemperatur.", "The transmission reports overheating.")
OIL = ("Der Motoröldruck ist zu niedrig.", "The engine oil pressure is too low.")
MISFIRE = ("Verbrennungsaussetzer. Blinkt", "Misfire. If the warning light flashes")
RESTRAINT = ("Rückhaltesystem (Airbag, Gurtstraffer)", "Restraint system (airbag, belt")
PERMANENT_TEXT = ("Permanenter Code:", "Permanent code:")
MIL_ON = ("Gespeichert, und die Kontrollleuchte ist an.", "Stored, and the warning light is on.")
MIL_OFF = ("Gespeichert, die Kontrollleuchte ist aus.", "Stored, the warning light is off.")
MIL_UNKNOWN = ("Gespeichert; ob die Kontrollleuchte an ist", "Stored; whether the warning light")
PENDING_TEXT = ("Ausstehend: einmal erkannt", "Pending: detected once")

# Jede Regel (Modul-Docstring 1-7) mit dem ersten zutreffenden Grund
RULES = [
    # 1: Übertemperatur/Öldruck in jeder Art, unabhängig von der MIL
    ("P0217", {PENDING}, False, Urgency.STOP, OVERHEAT),
    ("P0217", {STORED, PERMANENT}, True, Urgency.STOP, OVERHEAT),
    ("P0218", {STORED}, None, Urgency.STOP, GEARBOX),
    ("P0524", {PENDING}, None, Urgency.STOP, OIL),
    # 2: Aussetzer bestätigt; der Aussetzer-Grund geht vor „permanent“ und „MIL an“
    ("P0300", {PERMANENT}, True, Urgency.SOON, MISFIRE),
    ("P0301", {STORED, PENDING}, None, Urgency.SOON, MISFIRE),
    ("P0310", {STORED}, False, Urgency.SOON, MISFIRE),
    # 3: Airbag bestätigt; Grund geht vor „permanent“
    ("B0001", {PERMANENT}, False, Urgency.SOON, RESTRAINT),
    ("B0050", {STORED}, None, Urgency.SOON, RESTRAINT),
    ("B0099", {STORED}, True, Urgency.SOON, RESTRAINT),
    # 4: permanent, MIL egal
    ("P0420", {PERMANENT}, None, Urgency.SOON, PERMANENT_TEXT),
    ("P0420", {PERMANENT, PENDING}, False, Urgency.SOON, PERMANENT_TEXT),
    ("U0100", {PERMANENT, STORED}, False, Urgency.SOON, PERMANENT_TEXT),
    # 5/6: gespeichert, nach MIL
    ("P0171", {STORED}, True, Urgency.SOON, MIL_ON),
    ("P0171", {STORED, PENDING}, False, Urgency.CHECK, MIL_OFF),
    ("C0035", {STORED}, None, Urgency.CHECK, MIL_UNKNOWN),
    # 7: nur ausstehend, auch bei MIL an und bei Aussetzern/Airbag
    ("P0171", {PENDING}, True, Urgency.WATCH, PENDING_TEXT),
    ("P0300", {PENDING}, True, Urgency.WATCH, PENDING_TEXT),
    ("B0001", {PENDING}, None, Urgency.WATCH, PENDING_TEXT),
]


@pytest.mark.parametrize(("code", "kinds", "mil_on", "level", "reason"), RULES)
@pytest.mark.parametrize("lang", ["de", "en"])
def test_rule_level_and_reason(
    code: str,
    kinds: set[DtcKind],
    mil_on: bool | None,
    level: Urgency,
    reason: tuple[str, str],
    lang: str,
) -> None:
    rating = rate_code(code, kinds, mil_on)
    assert rating.level is level
    assert rating.label == URGENCY_LABELS[level]
    set_language(lang)
    assert tr(rating.reason).startswith(reason[0 if lang == "de" else 1])


@pytest.mark.parametrize(
    ("code", "level"),
    [
        # Nachbarn der kritischen Codes sind gewöhnliche Codes
        ("P0216", Urgency.CHECK),
        ("P0219", Urgency.CHECK),
        ("P0523", Urgency.CHECK),
        ("P0525", Urgency.CHECK),
        ("P1217", Urgency.CHECK),  # herstellerspezifisch, gleiche Endziffern
        # Aussetzer P0300-P0312 laut J2012, Nachbarn nicht
        ("P0299", Urgency.CHECK),
        ("P0311", Urgency.SOON),
        ("P0312", Urgency.SOON),
        ("P0313", Urgency.CHECK),
        ("P0320", Urgency.CHECK),
        ("P1300", Urgency.CHECK),
        ("P3300", Urgency.CHECK),
        # Airbag nur B00xx, nicht B01xx, nicht andere Gruppen mit 00
        ("B0100", Urgency.CHECK),
        ("B1001", Urgency.CHECK),
        ("C0001", Urgency.CHECK),
        ("U0001", Urgency.CHECK),
        ("P0001", Urgency.CHECK),
    ],
)
def test_code_group_boundaries(code: str, level: Urgency) -> None:
    assert rate_code(code, {STORED}, None).level is level


def test_kinds_may_be_any_iterable() -> None:
    assert rate_code("P0420", iter([PENDING, PERMANENT]), None).level is Urgency.SOON
    assert rate_code("P0420", [STORED, STORED], True).reason == MIL_ON[0]
    with pytest.raises(ValueError):
        rate_code("P0217", iter([]), True)  # auch kritische Codes brauchen eine Art


def test_critical_reasons_are_distinct_and_translated() -> None:
    reasons = {rate_code(c, {STORED}, None).reason for c in ("P0217", "P0218", "P0524")}
    assert len(reasons) == 3
    set_language("en")
    assert all(tr(r) != r for r in reasons)


def test_rate_codes_without_codes_is_empty() -> None:
    assert rate_codes([], True) == {}
    assert highest(rate_codes([], None).values()) is None


def test_rate_codes_passes_mil_to_every_code() -> None:
    codes = [DiagnosticCode("P0171", STORED), DiagnosticCode("P0420", STORED)]
    for mil_on, level in ((True, Urgency.SOON), (False, Urgency.CHECK), (None, Urgency.CHECK)):
        assert {r.level for r in rate_codes(codes, mil_on).values()} == {level}


def test_rate_codes_merges_kinds_for_rule_two() -> None:
    # Ein Aussetzer, ausstehend gemeldet und später permanent: über beide Arten bewertet
    codes = (c for c in [DiagnosticCode("P0300", PENDING), DiagnosticCode("P0300", PERMANENT)])
    (rating,) = rate_codes(codes, False).values()
    assert rating.level is Urgency.SOON
    assert rating.reason.startswith(MISFIRE[0])


def test_highest_with_generator_and_stop_wins() -> None:
    codes = [
        DiagnosticCode("P0133", PENDING),
        DiagnosticCode("P0300", STORED),
        DiagnosticCode("P0524", PENDING),  # ausstehend, aber Öldruck
        DiagnosticCode("P0420", PERMANENT),
    ]
    top = highest(r for r in rate_codes(codes, None).values())
    assert top is not None
    assert top.level is Urgency.STOP
    assert top.reason.startswith(OIL[0])


def test_levels_are_ordered() -> None:
    assert Urgency.WATCH < Urgency.CHECK < Urgency.SOON < Urgency.STOP
    assert max(Urgency.SOON, Urgency.WATCH) is Urgency.SOON
    assert [int(u) for u in Urgency] == [1, 2, 3, 4]


def test_labels_in_both_languages() -> None:
    german = [URGENCY_LABELS[u] for u in Urgency]
    assert german == [
        "Beobachten",
        "Bei Gelegenheit prüfen",
        "Bald in die Werkstatt",
        "Möglichst nicht weiterfahren",
    ]
    set_language("en")
    assert [tr(label) for label in german] == [
        "Keep an eye on it",
        "Check when convenient",
        "Visit a workshop soon",
        "Avoid driving",
    ]
    assert tr(URGENCY_NOTE).startswith("The urgency is a rough rating")


def test_rating_is_frozen() -> None:
    rating = UrgencyRating(Urgency.CHECK, "x")
    with pytest.raises(dataclasses.FrozenInstanceError):
        rating.level = Urgency.STOP  # type: ignore[misc]


# --- obd-diag scan ---


@pytest.fixture
def car(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Fahrzeug am Fake-Port, Katalog ohne Einträge."""
    responses = dict(CAN_CAR)
    monkeypatch.setattr(cli, "SerialTransport", lambda port, baud: FakeTransport(responses))
    monkeypatch.setattr(DtcCatalog, "default", classmethod(lambda cls: FakeCatalog({})))
    return responses


def _urgency_lines(out: str) -> list[str]:
    """Zeilen mit „Dringlichkeit“, umbrochene Begründungen wieder zu einer Zeile."""
    lines: list[str] = []
    for line in out.splitlines():
        if "Dringlichkeit" in line and not line.startswith("Hinweis"):
            lines.append(line.strip())
        elif lines and line.startswith(" " * 9) and lines[-1].startswith("Dringlichkeit:"):
            lines[-1] += " " + line.strip()
    return lines


def test_cli_scan_stop_code(car: dict[str, str], capsys: pytest.CaptureFixture[str]) -> None:
    car.update({"03": "4301 0217", "07": "NO DATA", "0A": "NO DATA"})
    assert main(["scan"]) == 0
    out = capsys.readouterr().out
    assert "Gespeichert:\n  P0217  (keine Beschreibung im Katalog)\n" in out
    assert _urgency_lines(out)[0].startswith(
        "Dringlichkeit: Möglichst nicht weiterfahren. Der Motor meldet Übertemperatur."
    )
    assert "\nDringlichkeit insgesamt: Möglichst nicht weiterfahren\n" in out


def test_cli_scan_only_pending_and_permanent(
    car: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    car.update({"03": "4300", "07": "4701 0171", "0A": "4A01 0420"})
    assert main(["scan"]) == 0
    out = capsys.readouterr().out
    assert "Gespeichert:" not in out
    assert "Ausstehend:\n  P0171  (keine Beschreibung im Katalog)\n" in out
    lines = _urgency_lines(out)
    assert lines[0].startswith("Dringlichkeit: Beobachten. Ausstehend: einmal erkannt")
    assert any(
        line.startswith("Dringlichkeit: Bald in die Werkstatt. Permanenter") for line in lines
    )
    assert lines[-1] == "Dringlichkeit insgesamt: Bald in die Werkstatt"


def test_cli_scan_without_codes_has_no_urgency(
    car: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    car.update({"03": "4300", "07": "4700", "0A": "4A00"})
    assert main(["scan"]) == 0
    out = capsys.readouterr().out
    assert "Dringlichkeit" not in out
    assert out.endswith("Keine Fehlercodes gespeichert.\n")


def test_cli_scan_english(car: dict[str, str], capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["scan", "--lang", "en"]) == 0
    out = capsys.readouterr().out
    # P0133 gespeichert und ausstehend: in beiden Abschnitten dieselbe Einstufung
    assert (
        out.count("         Urgency: Check when convenient. Stored; whether the warning light") == 3
    )  # P0133 (gespeichert), P0171, P0133 (ausstehend)
    assert "         Urgency: Visit a workshop soon. Misfire. If the warning light" in out
    assert "\nOverall urgency: Visit a workshop soon\nNote: The urgency is a rough" in out
    assert "Dringlichkeit" not in out


def test_cli_scan_urgency_is_wrapped_and_indented(
    car: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["scan"]) == 0
    out = capsys.readouterr().out
    block = out.split("  P0300  (keine Beschreibung im Katalog)\n", 1)[1].split("\n  P", 1)[0]
    lines = block.splitlines()
    assert len(lines) > 1  # Aussetzer-Begründung ist länger als eine Zeile
    assert all(line.startswith(" " * 9) and len(line) <= 88 for line in lines)
    assert " ".join(line.strip() for line in lines).startswith(
        "Dringlichkeit: Bald in die Werkstatt. Verbrennungsaussetzer. Blinkt die"
    )


def test_cli_scan_json_has_no_urgency(
    car: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    # Nur berechnet, nicht gespeichert: JSON bleibt ohne Dringlichkeit
    assert main(["scan", "--json"]) == 0
    text = capsys.readouterr().out
    data = json.loads(text)
    assert [c["code"] for c in data["codes"]] == ["P0133", "P0300", "P0171", "P0133"]
    assert "urgency" not in text.lower()
    assert "Dringlichkeit" not in text


def test_cli_scan_sends_no_extra_command(
    car: dict[str, str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Die MIL liest scan nicht nach (kein 0101); die Dringlichkeit bleibt „nicht gelesen“
    transports: list[FakeTransport] = []

    def open_port(port: str, baud: int) -> FakeTransport:
        transports.append(FakeTransport(car))
        return transports[-1]

    monkeypatch.setattr(cli, "SerialTransport", open_port)
    assert main(["scan"]) == 0
    assert "0101" not in transports[0].sent
    assert "nicht gelesen" in capsys.readouterr().out


# --- obd-diag diagnose: MIL aus der Readiness ---


@pytest.fixture
def full_car(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, str]:
    responses = dict(CAN_CAR_FULL)
    monkeypatch.setattr(cli, "SerialTransport", lambda port, baud: FakeTransport(responses))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(DtcCatalog, "default", classmethod(lambda cls: FakeCatalog({})))

    def no_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("Netzwerkzugriff")

    monkeypatch.setattr(vehicle, "urlopen", no_network)
    return responses


def test_cli_diagnose_mil_on(full_car: dict[str, str], capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["diagnose"]) == 0  # 0101: A = 83, MIL an
    lines = _urgency_lines(capsys.readouterr().out)
    assert lines[0] == (
        "Dringlichkeit: Bald in die Werkstatt. Gespeichert, und die Kontrollleuchte ist an."
    )
    assert "nicht gelesen" not in " ".join(lines)


def test_cli_diagnose_mil_off(full_car: dict[str, str], capsys: pytest.CaptureFixture[str]) -> None:
    full_car["0101"] = "4101 0307 6521"  # Bit A7 aus
    assert main(["diagnose"]) == 0
    lines = _urgency_lines(capsys.readouterr().out)
    assert (
        lines[0]
        == "Dringlichkeit: Bei Gelegenheit prüfen. Gespeichert, die Kontrollleuchte ist aus."
    )
    assert lines[-1] == "Dringlichkeit insgesamt: Bald in die Werkstatt"  # P0300


def test_cli_diagnose_without_readiness(
    full_car: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    full_car["0101"] = "NO DATA"
    assert main(["diagnose"]) == 0
    lines = _urgency_lines(capsys.readouterr().out)
    assert lines[0].startswith("Dringlichkeit: Bei Gelegenheit prüfen. Gespeichert; ob die")


# --- CSV und Sitzungen ---


def _csv_urgency(path: Path, delimiter: str = ";") -> dict[str, str]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        header, *rows = list(csv.reader(f, delimiter=delimiter))
    return {f"{row[0]}/{row[1]}": row[header.index(header[-1])] for row in rows}


def test_csv_urgency_follows_mil_of_session(tmp_path: Path) -> None:
    session = full_session()
    no_mil = dataclasses.replace(session, readiness=None)
    path = tmp_path / "ohne.csv"
    export_csv(no_mil, path)
    assert _csv_urgency(path) == {
        "P0300/Gespeichert": "Bald in die Werkstatt",  # Aussetzer, MIL egal
        "P0420/Gespeichert": "Bald in die Werkstatt",  # auch permanent
        "P0133/Ausstehend": "Beobachten",
        "P0420/Permanent": "Bald in die Werkstatt",
        "P1234/Ausstehend": "Beobachten",
    }


def test_csv_urgency_english(tmp_path: Path) -> None:
    set_language("en")
    path = tmp_path / "codes.csv"
    export_csv(full_session(), path)
    with path.open(encoding="utf-8-sig", newline="") as f:
        header = next(csv.reader(f, delimiter=","))
    assert header[-1] == "Urgency"
    assert _csv_urgency(path, ",") == {
        "P0300/Stored": "Visit a workshop soon",
        "P0420/Stored": "Visit a workshop soon",
        "P0133/Pending": "Keep an eye on it",
        "P0420/Permanent": "Visit a workshop soon",
        "P1234/Pending": "Keep an eye on it",
    }


def test_session_round_trip_keeps_rating_inputs(tmp_path: Path) -> None:
    # Gespeichert werden Codes und MIL, nicht die Einstufung; nach dem Laden gleich
    codes = [DiagnosticCode("P0171", STORED), DiagnosticCode("P0217", PENDING)]
    session = full_session()
    session = dataclasses.replace(session, scan=dataclasses.replace(session.scan, codes=codes))
    path = save_session(session, tmp_path)
    assert "urgency" not in path.read_text(encoding="utf-8").lower()
    loaded = load_session(path)
    assert loaded.readiness is not None
    before = rate_codes(session.scan.codes, True)
    after = rate_codes(loaded.scan.codes, loaded.readiness.mil_on)
    assert after == before
    assert [r.level for r in after.values()] == [Urgency.SOON, Urgency.STOP]


def test_cli_export_old_session_without_readiness(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    session = dataclasses.replace(
        full_session(),
        readiness=None,
        scan=dataclasses.replace(full_session().scan, codes=[DiagnosticCode("P0171", STORED)]),
    )
    path = save_session(session, tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data.pop("readiness", None)  # ältere Sitzung ohne den Abschnitt
    path.write_text(json.dumps(data), encoding="utf-8")
    target = tmp_path / "alt.csv"
    assert main(["export", str(path), "--csv", str(target)]) == 0
    assert _csv_urgency(target) == {"P0171/Gespeichert": "Bei Gelegenheit prüfen"}


# --- B0000 ist kein Airbag-Code -----------------------------------------------------


def test_b0000_is_not_a_restraint_code() -> None:
    # Der Modul-Docstring nennt B0001-B0099; früher traf ``B00\d\d`` auch B0000.
    assert rate_code("B0000", {STORED}, None).level is Urgency.CHECK
