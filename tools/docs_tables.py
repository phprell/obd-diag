"""Tabellen für die Dokumentation aus Freigabeliste und PID-Tabelle erzeugen.

    uv run python tools/docs_tables.py [ZIELORDNER] [--lang en]   # Standard: docs/_gen

Schreibt Markdown-Dateien, die ``docs/`` per ``{include}`` einbindet; ``docs/conf.py``
ruft ``write_tables`` bei jedem Bauen auf. So zeigt die Dokumentation immer genau das,
was ``tests/fixtures/command_spec.yaml`` (Befehle mit Datenblatt-Seite und Zitat) und
``obd_diag.protocol.pids.PIDS`` (Live-Werte) enthalten, und kann nicht veralten.

Die Formeln der Live-Werte stehen im Code teils als Funktion mit Docstring
(Docstring ``100*A/255: …``), teils als Lambda. Für Lambdas wird der Ausdruck aus dem
Quelltext gelesen und in die J1979-Schreibweise mit Datenbytes ``A``, ``B``, ``C`` …
übersetzt; bleibt dabei Python-Syntax übrig, schlägt ``formula`` fehl, statt eine
halbe Formel zu zeigen.

Mit ``lang="en"`` entstehen dieselben Tabellen englisch für ``docs/en`` (Namen der
Live-Werte aus ``obd_diag.i18n``, Bedeutungen aus ``meaning_en``/``reason_en`` der
Spezifikation).
"""

import argparse
import ast
import inspect
import re
import sys
import textwrap
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from obd_diag import i18n  # noqa: E402
from obd_diag.protocol.pids import PIDS, PidSpec  # noqa: E402
from tests.command_spec import CommandSpec, load_spec  # noqa: E402

_LETTERS = "ABCDEFGH"

# Ausdrücke im Quelltext der Lambdas -> J1979-Schreibweise; Reihenfolge zählt.
_REPLACEMENTS: list[tuple[str, str]] = [
    (r'int\.from_bytes\(d\[:2\], "big", signed=True\)', "(256A + B, vorzeichenbehaftet)"),
    (r'int\.from_bytes\(d\[:4\], "big"\)', "(2^24 A + 2^16 B + 2^8 C + D)"),
    (r"_word\(d\[1:3\]\)", "(256B + C)"),
    (r"_word\(d\[3:5\]\)", "(256D + E)"),
    (r"_word\(d\)", "(256A + B)"),
    (r"d\[(\d)\]", "{letter}"),
]


def _cell(text: str) -> str:
    """Text für eine Markdown-Tabellenzelle (``|`` maskiert, keine Zeilenumbrüche)."""
    return " ".join(text.split()).replace("|", "\\|")


def _lambda_source(func: Callable[..., object]) -> str:
    """Der Ausdruck einer Lambda-Funktion, so wie er im Quelltext steht."""
    lines, start = inspect.getsourcelines(func)
    source = textwrap.dedent("".join(lines))
    tree = ast.parse(source)
    target = func.__code__.co_firstlineno - start + 1
    for node in ast.walk(tree):
        if isinstance(node, ast.Lambda) and node.lineno == target:
            segment = ast.get_source_segment(source, node.body)
            if segment is not None:
                return segment
    raise ValueError(f"Lambda nicht gefunden: {func!r}")


def _translate(expression: str) -> str:
    for pattern, replacement in _REPLACEMENTS:
        if "{letter}" in replacement:
            expression = re.sub(pattern, lambda m: _LETTERS[int(m.group(1))], expression)
        else:
            expression = re.sub(pattern, replacement, expression)
    if "d[" in expression or "_" in expression or "(d" in expression:
        raise ValueError(f"Formel nicht übersetzbar: {expression}")
    return expression


def _shift(formula: str, index: int) -> str:
    """Eine Ein-Byte-Formel (mit ``A``) auf das Datenbyte ``index`` umschreiben."""
    return re.sub(r"\bA\b", _LETTERS[index], formula)


def formula(decode: Callable[[bytes], object]) -> str:
    """Die Umrechnung von ``decode`` in J1979-Schreibweise, z. B. ``(256A + B) / 4``."""
    closure = dict(
        zip(
            decode.__code__.co_freevars,
            (cell.cell_contents for cell in decode.__closure__ or ()),
            strict=True,
        )
    )
    if "index" in closure and "decode" in closure:  # _at(index, decode)
        return _shift(formula(closure["decode"]), closure["index"])
    if "bit" in closure and "decode" in closure:  # _if_supported(bit, decode)
        return f"{formula(closure['decode'])}; nur wenn Bit {closure['bit']} von A gesetzt"
    if decode.__doc__:
        first = decode.__doc__.strip().splitlines()[0]
        head = first.split(":")[0].strip()
        # "Formel; Sonderfall: Bedeutung" ganz zeigen, sonst nur die Formel (der Bereich
        # nach dem Doppelpunkt steht in einer eigenen Spalte)
        return first if ";" in head else head
    if decode.__name__ == "<lambda>":
        return _translate(_lambda_source(decode))
    raise ValueError(f"keine Formel für {decode!r}")


# Feste Texte der Tabellen je Sprache
_TEXT = {
    "de": {
        "pid_header": "| PID | Schlüssel | Wert | Einheit | Formel | Bereich |",
        "to": "bis",
        "pid_head": (
            "{values} Werte aus {pids} PIDs, nach PID sortiert. `A`, `B`, `C` … sind "
            "die Datenbytes nach `41 <PID>`; der Bereich ist der volle Wertebereich der "
            "Kodierung, nicht der physikalisch plausible.\n\n"
        ),
        "datasheet": "Datenblatt S. {page}",
        "page": "{source} S. {page}",
        "allowed": "## Erlaubte Befehle\n",
        "allowed_header": "| Befehl | Bedeutung | ans Fahrzeug | schreibt | Quelle | Zitat |",
        "only_in": " (nur in `{context}`)",
        "yes": "ja",
        "no": "nein",
        "forbidden": "## Verbotene Beispiele\n",
        "forbidden_intro": (
            "Diese Befehle lehnt die Freigabeliste ab; die Tests prüfen, dass keiner je "
            "gesendet wird.\n"
        ),
        "forbidden_header": "| Befehl | Warum verboten | Quelle | Zitat |",
        "cr_only": "(nur CR)",
        "quote": "„{quote}“",
    },
    "en": {
        "pid_header": "| PID | Key | Value | Unit | Formula | Range |",
        "to": "to",
        "pid_head": (
            "{values} values from {pids} PIDs, sorted by PID. `A`, `B`, `C` … are the "
            "data bytes after `41 <PID>`; the range is the full value range of the "
            "encoding, not the physically plausible one.\n\n"
        ),
        "datasheet": "datasheet p. {page}",
        "page": "{source} p. {page}",
        "allowed": "## Allowed commands\n",
        "allowed_header": "| Command | Meaning | to the vehicle | writes | Source | Quote |",
        "only_in": " (only in `{context}`)",
        "yes": "yes",
        "no": "no",
        "forbidden": "## Forbidden examples\n",
        "forbidden_intro": (
            "The allowlist rejects these commands; the tests check that none of them is "
            "ever sent.\n"
        ),
        "forbidden_header": "| Command | Why forbidden | Source | Quote |",
        "cr_only": "(CR only)",
        "quote": "“{quote}”",
    },
}

# Deutsche Teile der Formeln (aus _REPLACEMENTS, formula und Docstrings) auf Englisch
_FORMULA_EN: list[tuple[str, str]] = [
    (r"vorzeichenbehaftet", "signed"),
    (r"nur wenn Bit (\d+) von A gesetzt", r"only if bit \1 of A is set"),
    (r"Sonde geht nicht in den Kraftstofftrimm ein", "sensor not used for fuel trim"),
]


@contextmanager
def _language(lang: str) -> Iterator[None]:
    """Namen der Live-Werte (``PidSpec.name``) vorübergehend in ``lang``."""
    before = i18n.language()
    i18n.set_language(lang)
    try:
        yield
    finally:
        i18n.set_language(before)


def _formula_text(decode: Callable[[bytes], object], lang: str) -> str:
    text = formula(decode)
    if lang == "en":
        for pattern, replacement in _FORMULA_EN:
            text = re.sub(pattern, replacement, text)
    return text


def _number(value: float, lang: str = "de") -> str:
    text = f"{value:.6g}"
    return text.replace(".", ",") if lang == "de" else text


def pid_table(pids: dict[str, PidSpec] = PIDS, lang: str = "de") -> str:
    t = _TEXT[lang]
    rows = [t["pid_header"], "| --- | --- | --- | --- | --- | --- |"]
    with _language(lang):
        for spec in pids.values():
            rows.append(
                f"| `{spec.pid:02X}` | `{spec.key}` | {_cell(spec.name)} "
                f"| {_cell(i18n.tr(spec.unit))} "
                f"| `{_cell(_formula_text(spec.decode, lang))}` "
                f"| {_number(spec.minimum, lang)} {t['to']} {_number(spec.maximum, lang)} |"
            )
    distinct = len({spec.pid for spec in pids.values()})
    head = t["pid_head"].format(values=len(pids), pids=distinct)
    return head + "\n".join(rows) + "\n"


def _source_link(source: str, page: int, lang: str = "de") -> str:
    t = _TEXT[lang]
    if source == "ELM327DSJ":
        return t["datasheet"].format(page=page)
    return t["page"].format(source=source, page=page)


def command_tables(spec: CommandSpec | None = None, lang: str = "de") -> str:
    spec = spec or load_spec()
    t = _TEXT[lang]
    out = [t["allowed"], t["allowed_header"], "| --- | --- | --- | --- | --- | --- |"]
    for entry in spec.allowed:
        cmd = entry.command if entry.command is not None else entry.pattern
        meaning = entry.meaning if lang == "de" else entry.meaning_en
        if entry.requires:
            meaning += t["only_in"].format(context=entry.requires)
        writes = f"**{t['yes']}**" if entry.writes else t["no"]
        out.append(
            f"| `{_cell(cmd or '')}` | {_cell(meaning)} "
            f"| {t['yes'] if entry.reaches_vehicle else t['no']} "
            f"| {writes} "
            f"| {_source_link(entry.source, entry.page, lang)} "
            f"| {t['quote'].format(quote=_cell(entry.quote))} |"
        )
    out += [
        "",
        t["forbidden"],
        t["forbidden_intro"],
        t["forbidden_header"],
        "| --- | --- | --- | --- |",
    ]
    for forbidden in spec.forbidden:
        cmd = f"`{_cell(forbidden.command)}`" if forbidden.command else t["cr_only"]
        reason = forbidden.reason if lang == "de" else forbidden.reason_en
        out.append(
            f"| {cmd} | {_cell(reason)} "
            f"| {_source_link(forbidden.source, forbidden.page, lang)} "
            f"| {t['quote'].format(quote=_cell(forbidden.quote))} |"
        )
    return "\n".join(out) + "\n"


def write_tables(out_dir: Path, lang: str = "de") -> list[Path]:
    """Schreibt alle erzeugten Tabellen nach ``out_dir`` und liefert die Dateien."""
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {"pids.md": pid_table(lang=lang), "befehle.md": command_tables(lang=lang)}
    written = []
    for name, text in files.items():
        path = out_dir / name
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8")  # nur bei Änderung: Sphinx baut sonst neu
        written.append(path)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out_dir", nargs="?", type=Path)
    parser.add_argument("--lang", choices=tuple(_TEXT), default="de")
    args = parser.parse_args()
    default = ROOT / "docs" / "_gen" if args.lang == "de" else ROOT / "docs" / "en" / "_gen"
    for path in write_tables(args.out_dir or default, args.lang):
        print(path)


if __name__ == "__main__":
    main()
