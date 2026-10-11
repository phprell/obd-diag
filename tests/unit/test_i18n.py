"""Übersetzung (``obd_diag.i18n``, ``locale/en.po``): Katalog vollständig und aktuell,
keine deutschen Texte an ``tr`` vorbei, englische Ausgabe von CLI, PDF, CSV und PIDs.

Schlägt ein Test hier fehl, weil Texte neu oder geändert sind:

    uv run python tools/translations.py --todo   # fehlende Übersetzungen
    uv run python tools/translations.py --fill F  # eintragen (JSON {"Nummer": "Text"})
"""

import ast
import csv
import re
from pathlib import Path

import pytest
from pypdf import PdfReader

from obd_diag import i18n
from obd_diag.cli import language_from_argv, main
from obd_diag.export.report import export_csv, export_pdf
from obd_diag.i18n import N_, csv_delimiter, decimal, parse_po, set_language, tr, trn
from obd_diag.protocol.pids import PIDS
from obd_diag.services.live import LiveRecorder, LiveSample
from obd_diag.services.session import save_session
from tests.samples import full_session
from tools import translations

PACKAGE = Path(i18n.__file__).parent
CATALOG = PACKAGE / "locale" / "en.po"

# --- Katalog ---


def test_catalog_is_current() -> None:
    existing = parse_po(CATALOG.read_text(encoding="utf-8"))
    expected = translations.render(translations.updated(existing))
    assert CATALOG.read_text(encoding="utf-8") == expected, (
        "locale/en.po ist nicht aktuell: uv run python tools/translations.py"
    )


def test_every_text_is_translated() -> None:
    catalog = parse_po(CATALOG.read_text(encoding="utf-8"))
    missing = [m for m in translations.extract() if m not in catalog]
    assert missing == [], "ohne Übersetzung, siehe tools/translations.py --todo"


def test_plural_texts_have_two_forms() -> None:
    catalog = parse_po(CATALOG.read_text(encoding="utf-8"))
    for msgid, message in translations.extract().items():
        assert (message.plural is not None) == (len(catalog[msgid]) == 2), msgid


_PLACEHOLDER = re.compile(r"\{[^{}]*\}|%\d|%[a-zA-Z]")


def test_placeholders_survive_translation() -> None:
    """Gleiche Platzhalter in Original und Übersetzung, sonst scheitert ``.format``
    bzw. ``.arg`` (QML) oder ein Wert fehlt. Datumsformate (``%d.%m.%Y``) dürfen
    die Reihenfolge ändern, aber keine Angabe verlieren."""
    messages = translations.extract()
    for msgid, forms in parse_po(CATALOG.read_text(encoding="utf-8")).items():
        message = messages.get(msgid)
        originals = (
            [msgid] if message is None or message.plural is None else [msgid, message.plural]
        )
        for original, translated in zip(originals * 2, forms, strict=False):
            ours = sorted(_PLACEHOLDER.findall(original))
            theirs = sorted(_PLACEHOLDER.findall(translated))
            if "{n}" in ours and "{n}" not in theirs and original != msgid:
                continue  # Mehrzahl ohne Zahl ist erlaubt ("1 round" / "{n} rounds")
            assert theirs == ours, f"{msgid!r} → {translated!r}"


def test_translations_keep_ampersand_shortcuts() -> None:
    """Menütitel mit ``&`` (Tastenkürzel) und ``&&`` (ein echtes &) bleiben so."""
    for msgid, forms in parse_po(CATALOG.read_text(encoding="utf-8")).items():
        if re.match(r"&\w", msgid):
            assert re.match(r"&\w", forms[0]), msgid
        assert ("&&" in msgid) == ("&&" in forms[0]), msgid


# --- Keine deutschen Texte ohne tr ---

# Typische deutsche Wörter und Zeichen; englische Texte enthalten sie nicht.
_GERMAN = re.compile(
    r"[äöüÄÖÜß]|\b(der|die|das|den|dem|nicht|kein|keine|keinen|und|oder|mit|ist|sind|bei"
    r"|für|wird|werden|nach|von|vom|zum|zur|auf|aus|ein|eine|einen|noch|nur|Fehler"
    r"|Sitzung|Bitte|Datei|Antwort|unbekannt\w*|ungültig\w*|Steuergerät\w*|Deutsch)\b"
)
_TR_CALLS = {"tr", "N_", "trn"}
_LOGGERS = {"log", "logger", "logging"}
# Dateien, deren Texte nicht beim Nutzer ankommen: Katalogbau (Entwicklerwerkzeug,
# tools/build_dtc_db.py) und Programmierfehler in i18n selbst.
_PY_EXEMPT = {"data/catalog_build.py", "i18n.py"}
# QML: der Menüname ist absichtlich zweisprachig, die Sprachen heißen in ihrer Sprache
_QML_EXEMPT = {"Sprache / Language", "Deutsch", "English"}


def _untranslated(tree: ast.Module) -> list[tuple[int, str]]:
    skip: set[int] = set()
    for node in ast.walk(tree):
        # Docstrings und frei stehende Texte (Kommentare in Anführungszeichen)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            skip.add(id(node.value))
        if isinstance(node, ast.Call):
            func = node.func
            translated = isinstance(func, ast.Name) and func.id in _TR_CALLS
            # Protokollmeldungen sind für Entwickler und bleiben deutsch wie Kommentare
            logged = (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id in _LOGGERS
            )
            if translated or logged:
                skip.update(id(sub) for arg in node.args for sub in ast.walk(arg))
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in skip
        and _GERMAN.search(node.value)
    ]


def test_no_german_text_bypasses_tr() -> None:
    found = []
    for path in sorted(PACKAGE.rglob("*.py")):
        rel = path.relative_to(PACKAGE).as_posix()
        if rel in _PY_EXEMPT:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found += [f"{rel}:{line}: {text!r}" for line, text in _untranslated(tree)]
    assert found == [], "Texte mit tr()/N_() ausgeben (siehe obd_diag.i18n)"


def test_guard_finds_german_text() -> None:
    tree = ast.parse(
        '"""Docstring bleibt."""\n'
        'log.info("Antwort fehlt")\n'
        'print(tr("Keine Antwort"))\n'
        'raise ValueError(f"Antwort von {port} fehlt")\n'
        'print("Fertig: keine Codes")\n'
    )
    assert sorted(line for line, _ in _untranslated(tree)) == [4, 5]


_QML_STRING = re.compile(r'(qsTr\(\s*)?"((?:[^"\\\n]|\\.)*)"')


def test_no_german_text_bypasses_qstr() -> None:
    found = []
    for path in sorted(PACKAGE.rglob("*.qml")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = re.sub(r"^\s*//.*|\s//.*$", "", line)
            for match in _QML_STRING.finditer(code):
                text = match.group(2)
                if not match.group(1) and text not in _QML_EXEMPT and _GERMAN.search(text):
                    found.append(f"{path.name}:{number}: {text}")
    assert found == [], "Texte in QML mit qsTr() ausgeben"


def test_tr_with_f_string_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "mod.py").write_text('tr(f"Port {port}")\n', encoding="utf-8")
    monkeypatch.setattr(translations, "PACKAGE", tmp_path)
    monkeypatch.setattr(translations, "ROOT", tmp_path)
    with pytest.raises(ValueError, match=r"mod\.py:1: tr\(\) mit f-String"):
        translations.extract()


# --- i18n ---


def test_parse_po() -> None:
    text = (
        'msgid ""\nmsgstr ""\n"Language: en\\n"\n\n'
        '#: a.py\nmsgid "Hallo"\nmsgstr "Hello"\n\n'
        'msgid "Lang"\n"er Text"\nmsgstr "Long"\n" text \\"quoted\\""\n\n'
        'msgid "{n} Runde"\nmsgid_plural "{n} Runden"\nmsgstr[0] "{n} round"\n'
        'msgstr[1] "{n} rounds"\n\n'
        '#, fuzzy\nmsgid "Offen"\nmsgstr ""\n\n'
        'msgid "Halb"\nmsgid_plural "Halbe"\nmsgstr[0] "half"\nmsgstr[1] ""\n'
    )
    assert parse_po(text) == {
        "Hallo": ("Hello",),
        "Langer Text": ('Long text "quoted"',),
        "{n} Runde": ("{n} round", "{n} rounds"),
    }


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"LANG": "de_DE.UTF-8"}, "de"),
        ({"LANG": "de_AT.UTF-8"}, "de"),
        ({"LANG": "en_US.UTF-8"}, "en"),
        ({"LANG": "fr_FR.UTF-8"}, "en"),
        ({"LANG": "C.UTF-8"}, "en"),
        ({"LANGUAGE": "de:en", "LANG": "en_US.UTF-8"}, "de"),
        ({"LC_ALL": "en_GB.UTF-8", "LANG": "de_DE.UTF-8"}, "en"),
        ({"LC_MESSAGES": "de_CH.UTF-8", "LANG": "en_US.UTF-8"}, "de"),
        ({}, "en"),
    ],
)
def test_system_language(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str], expected: str
) -> None:
    for name in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    assert i18n.system_language() == expected


def test_tr_trn_and_numbers() -> None:
    assert tr("Abbrechen") == "Abbrechen"
    assert trn("{n} Test nicht abgeschlossen: {names}.", "x", 2) == "x"
    assert (decimal(12.345), csv_delimiter()) == ("12,3", ";")
    set_language("en")
    assert tr("Abbrechen") == "Cancel"
    assert tr("nicht im Katalog") == "nicht im Katalog"
    assert tr("") == ""
    assert N_("Abbrechen") == "Abbrechen"  # nur Markierung
    assert trn("Weiterhin {n} Code vorhanden.", "Weiterhin {n} Codes vorhanden.", 1) == (
        "{n} code still present."
    )
    assert trn("Weiterhin {n} Code vorhanden.", "Weiterhin {n} Codes vorhanden.", 3) == (
        "{n} codes still present."
    )
    assert (decimal(12.345, 2), csv_delimiter()) == ("12.35", ",")
    with pytest.raises(ValueError, match="unbekannte Sprache"):
        set_language("fr")


def test_language_from_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGUAGE", "de")
    assert language_from_argv(["scan"]) == "de"
    assert language_from_argv(["--lang", "en", "scan"]) == "en"
    assert language_from_argv(["scan", "--lang=en"]) == "en"
    assert language_from_argv(["scan", "--lang", "xx"]) == "de"  # argparse meldet den Fehler


# --- Englische Ausgabe ---


def test_pid_names_in_english() -> None:
    set_language("en")
    assert PIDS["rpm"].name == "Engine speed"
    assert tr(PIDS["rpm"].unit) == "rpm"
    assert PIDS["o2_s2_voltage"].name == "Oxygen sensor 2 voltage"
    set_language("de")
    assert PIDS["rpm"].name == "Motordrehzahl"
    assert PIDS["o2_s2_voltage"].name == "Lambdasonde 2 Spannung"


def test_cli_in_english(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["vin", "--lang", "en", "WVWZZZ1KZ6W123456"]) == 0
    assert capsys.readouterr().out == (
        "Vehicle:\n"
        "  VIN:          WVWZZZ1KZ6W123456\n"
        "  Check digit:  not mandatory (differs)\n"
        "  Manufacturer: Volkswagen\n"
        "  Country:      Germany\n"
        "  Model year:   2006 (from position 10, without guarantee)\n"
    )
    assert main(["--lang", "en", "info", "--port", "/dev/does-not-exist"]) == 1
    assert capsys.readouterr().err.startswith("Error: /dev/does-not-exist cannot be opened")


def test_cli_help_in_english(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["--lang", "en", "live", "--help"])
    out = capsys.readouterr().out
    assert "show supported values with their keys and exit" in out
    assert "unterstützte" not in out


def test_report_and_csv_in_english(tmp_path: Path) -> None:
    set_language("en")
    pdf, table = tmp_path / "report.pdf", tmp_path / "codes.csv"
    export_pdf(full_session(), pdf)
    export_csv(full_session(), table)
    text = re.sub(r"\s+", " ", " ".join(p.extract_text() for p in PdfReader(pdf).pages))
    for expected in ("OBD diagnosis report", "Stored trouble codes", "Check engine light"):
        assert expected in text
    assert "Fehlercodes" not in text
    with table.open(encoding="utf-8-sig", newline="") as f:
        header, first, *_ = list(csv.reader(f, delimiter=","))
    assert header[:4] == ["Code", "Type", "Title", "Description"]
    assert first[:2] == ["P0300", "Stored"]


def test_live_csv_in_english(tmp_path: Path) -> None:
    set_language("en")
    path = tmp_path / "live.csv"
    recorder = LiveRecorder(path, [PIDS["rpm"]])
    recorder.add(LiveSample(1.5, {"rpm": 812.25}, 12.4, False))
    recorder.close()
    assert path.read_text(encoding="utf-8-sig").splitlines() == [
        "Time (s),Engine speed (rpm),Battery voltage (V)",
        "1.5,812.25,12.4",
    ]


def test_cli_export_in_english(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    session_file = save_session(full_session(), tmp_path)
    table = tmp_path / "codes.csv"
    assert main(["export", "--lang", "en", str(session_file), "--csv", str(table)]) == 0
    assert capsys.readouterr().out == f"CSV: {table}\n"
    assert table.read_bytes().startswith(b"\xef\xbb\xbfCode,Type,")
