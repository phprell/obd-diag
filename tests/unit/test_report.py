import csv
import dataclasses
import re
from pathlib import Path

import pytest
from pypdf import PdfReader

from obd_diag import __version__
from obd_diag.export import report
from obd_diag.export.report import CSV_COLUMNS, export_csv, export_pdf
from obd_diag.services.readiness import MonitorState
from tests.samples import full_session, minimal_session

# --- CSV ---


def _read_csv(path: Path) -> list[list[str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.reader(f, delimiter=";"))


def test_csv_bom_delimiter_and_rows(tmp_path: Path) -> None:
    path = tmp_path / "codes.csv"
    export_csv(full_session(), path)
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # BOM, damit Excel UTF-8 erkennt
    assert raw.splitlines()[0].decode("utf-8-sig").startswith("Code;Art;Titel;")
    assert b"\r\n" in raw

    header, *rows = _read_csv(path)
    assert header == list(CSV_COLUMNS)
    records = [dict(zip(header, row, strict=True)) for row in rows]
    assert [(r["Code"], r["Art"]) for r in records] == [
        ("P0300", "Gespeichert"),
        ("P0420", "Gespeichert"),
        ("P0133", "Ausstehend"),
        ("P0420", "Permanent"),
        ("P1234", "Ausstehend"),
    ]
    p0300 = records[0]
    assert p0300["Titel"] == "Zufällige/mehrfache Zylinder-Verbrennungsaussetzer erkannt"
    assert p0300["Ursachen"] == (
        "Verschlissene Zündkerzen (hoch) | Falschluft, die alle Zylinder betrifft (hoch)"
        " | Kraftstoffdruck zu niedrig (mittel)"
    )
    assert p0300["Symptome"].startswith("Motorkontrollleuchte an oder blinkt | ")
    assert p0300["MIL"] == "ja"
    assert p0300["Abgasrelevant"] == "ja"
    assert p0300["Reparaturaufwand"] == "mittel"
    assert p0300["Kosten"] == "50–1.500 €"  # noqa: RUF001
    assert (p0300["Kosten von (EUR)"], p0300["Kosten bis (EUR)"]) == ("50", "1500")
    assert p0300["Datum"] == "2026-10-07T14:32:05+02:00"
    assert p0300["FIN"] == "WVWZZZ1KZ6W123456"
    unknown = records[4]
    assert unknown["Titel"] == unknown["Ursachen"] == unknown["Kosten"] == ""
    assert unknown["FIN"] == "WVWZZZ1KZ6W123456"


def test_csv_without_codes_has_only_header(tmp_path: Path) -> None:
    path = tmp_path / "leer.csv"
    export_csv(minimal_session(), path)
    assert _read_csv(path) == [list(CSV_COLUMNS)]


# --- PDF ---


def _pdf_text(path: Path) -> str:
    text = "\n".join(page.extract_text() for page in PdfReader(path).pages)
    return re.sub(r"[ \t]+", " ", text)


@pytest.fixture(params=["ttf", "helvetica"])
def font(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    """Beide Schriftwege: eingebettete System-Schrift (falls vorhanden) und Helvetica."""
    if request.param == "helvetica":
        monkeypatch.setattr(report, "find_font_files", lambda: None)
    elif report.find_font_files() is None:
        pytest.skip("keine DejaVu/Liberation/Noto-Schrift installiert")
    return str(request.param)


def test_pdf_full_session(tmp_path: Path, font: str) -> None:
    path = tmp_path / "bericht.pdf"
    export_pdf(full_session(), path)
    assert path.read_bytes().startswith(b"%PDF")
    text = _pdf_text(path)
    for expected in (
        "OBD-Diagnosebericht",
        "Ausgelesen am 07.10.2026, 14:32 Uhr",
        "WVWZZZ1KZ6W123456",
        "Volkswagen",
        "Deutschland",
        "2006",
        "ISO 15765-4 (CAN 11/500)",
        "12,4 V",
        "Gespeicherte Fehlercodes (2)",
        "Ausstehende Fehlercodes (2)",
        "Permanente Fehlercodes (1)",
        "Katalysatorwirkungsgrad unter Schwellwert (Bank 1)",
        "Verschlissene Zündkerzen",
        "hoch",
        "mittel",
        "niedrig",
        "Rauer Leerlauf und unrunder Motorlauf",
        "600–2.500 €",  # noqa: RUF001
        "Keine Beschreibung im Katalog",
        "Erklärung siehe oben",
        "Kühlmitteltemperatur",
        "87 °C",
        "34,5 %",
        "2.140 1/min",
        "63 km/h",
        "Tankentlüftung",
        "nicht abgeschlossen",
        "nicht unterstützt",
        "AU-bereit",
        "nein, nicht alle unterstützten Tests sind abgeschlossen",
        f"Erstellt mit obd-diag {__version__}; Fehlercode-Texte: OBDex (CC0)",
        "Seite 1 von",
    ):
        assert expected in text, expected
    assert "Warnung" not in text
    assert "nicht verfügbar" not in text


def test_pdf_low_voltage_and_ready(tmp_path: Path, font: str) -> None:
    session = full_session(voltage=11.2)
    assert session.readiness is not None
    monitors = tuple(
        dataclasses.replace(m, state=MonitorState.COMPLETE)
        if m.state is MonitorState.INCOMPLETE
        else m
        for m in session.readiness.monitors
    )
    session = dataclasses.replace(
        session, readiness=dataclasses.replace(session.readiness, monitors=monitors)
    )
    path = tmp_path / "bericht.pdf"
    export_pdf(session, path)
    text = _pdf_text(path)
    assert "11,2 V (niedrig)" in text
    assert "Warnung: Bordspannung unter 11,8 V" in text
    assert "AU-bereit\nja" in text or "AU-bereit ja" in text


def test_pdf_minimal_session(tmp_path: Path, font: str) -> None:
    path = tmp_path / "leer.pdf"
    export_pdf(minimal_session(), path)
    reader = PdfReader(path)
    assert len(reader.pages) == 1
    text = _pdf_text(path)
    assert "Fahrzeug-Identifikation (FIN) nicht verfügbar." in text
    assert "Readiness-Status nicht verfügbar." in text
    assert "Freeze Frame nicht verfügbar." in text
    assert "Keine Fehlercodes gespeichert." in text
    assert "unbekannt" in text  # Bordspannung
    assert "AU-bereit" not in text
    assert "Seite 1 von 1" in text


def test_pdf_metadata(tmp_path: Path) -> None:
    path = tmp_path / "bericht.pdf"
    export_pdf(full_session(), path)
    meta = PdfReader(path).metadata
    assert meta is not None
    assert meta.title == "OBD-Diagnosebericht"
    assert meta.subject == "WVWZZZ1KZ6W123456"


def test_pdf_embeds_system_font(tmp_path: Path) -> None:
    files = report.find_font_files()
    if files is None:
        pytest.skip("keine DejaVu/Liberation/Noto-Schrift installiert")
    path = tmp_path / "bericht.pdf"
    export_pdf(full_session(), path)
    data = path.read_bytes()
    assert b"/FontFile2" in data  # eingebettete TrueType-Schrift


def test_ttf_fallback_replaces_missing_glyphs() -> None:
    files = report.find_font_files()
    if files is None:
        pytest.skip("keine DejaVu/Liberation/Noto-Schrift installiert")
    fonts = report._register_ttf(*files)
    assert fonts is not None
    assert fonts.clean("Kühlmittel 87 °C – 50 €") == "Kühlmittel 87 °C – 50 €"  # noqa: RUF001
    assert fonts.clean("\U0001f697") == "?"  # Emoji hat keine dieser Schriften


@pytest.mark.parametrize(
    ("value", "decimals", "expected"),
    [(2140.0, 0, "2.140"), (34.5, 1, "34,5"), (12.44, 1, "12,4"), (-5, 0, "-5")],
)
def test_german_number(value: float, decimals: int, expected: str) -> None:
    assert report._number(value, decimals) == expected
