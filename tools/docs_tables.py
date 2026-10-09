"""Tabellen für die Dokumentation aus Freigabeliste und PID-Tabelle erzeugen.

    uv run python tools/docs_tables.py [ZIELORDNER]      # Standard: docs/_gen

Schreibt Markdown-Dateien, die ``docs/`` per ``{include}`` einbindet; ``docs/conf.py``
ruft ``write_tables`` bei jedem Bauen auf. So zeigt die Dokumentation immer genau das,
was ``tests/fixtures/command_spec.yaml`` (Befehle mit Datenblatt-Seite und Zitat) und
``obd_diag.protocol.pids.PIDS`` (Live-Werte) enthalten, und kann nicht veralten.

Die Formeln der Live-Werte stehen im Code teils als Funktion mit Docstring
(Docstring ``100*A/255: …``), teils als Lambda. Für Lambdas wird der Ausdruck aus dem
Quelltext gelesen und in die J1979-Schreibweise mit Datenbytes ``A``, ``B``, ``C`` …
übersetzt; bleibt dabei Python-Syntax übrig, schlägt ``formula`` fehl, statt eine
halbe Formel zu zeigen.
"""

import ast
import inspect
import re
import sys
import textwrap
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

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


def _number(value: float) -> str:
    text = f"{value:.6g}"
    return text.replace(".", ",")


def pid_table(pids: dict[str, PidSpec] = PIDS) -> str:
    rows = [
        "| PID | Schlüssel | Wert | Einheit | Formel | Bereich |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for spec in pids.values():
        rows.append(
            f"| `{spec.pid:02X}` | `{spec.key}` | {_cell(spec.name)} | {_cell(spec.unit)} "
            f"| `{_cell(formula(spec.decode))}` "
            f"| {_number(spec.minimum)} bis {_number(spec.maximum)} |"
        )
    distinct = len({spec.pid for spec in pids.values()})
    head = (
        f"{len(pids)} Werte aus {distinct} PIDs, nach PID sortiert. `A`, `B`, `C` … sind "
        "die Datenbytes nach `41 <PID>`; der Bereich ist der volle Wertebereich der "
        "Kodierung, nicht der physikalisch plausible.\n\n"
    )
    return head + "\n".join(rows) + "\n"


def _source_link(source: str, page: int) -> str:
    if source == "ELM327DSJ":
        return f"Datenblatt S. {page}"
    return f"{source} S. {page}"


def command_tables(spec: CommandSpec | None = None) -> str:
    spec = spec or load_spec()
    out = [
        "## Erlaubte Befehle\n",
        "| Befehl | Bedeutung | ans Fahrzeug | schreibt | Quelle | Zitat |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for entry in spec.allowed:
        cmd = entry.command if entry.command is not None else entry.pattern
        meaning = entry.meaning
        if entry.requires:
            meaning += f" (nur in `{entry.requires}`)"
        out.append(
            f"| `{_cell(cmd or '')}` | {_cell(meaning)} "
            f"| {'ja' if entry.reaches_vehicle else 'nein'} "
            f"| {'**ja**' if entry.writes else 'nein'} "
            f"| {_source_link(entry.source, entry.page)} | „{_cell(entry.quote)}“ |"
        )
    out += [
        "",
        "## Verbotene Beispiele\n",
        "Diese Befehle lehnt die Freigabeliste ab; die Tests prüfen, dass keiner je "
        "gesendet wird.\n",
        "| Befehl | Warum verboten | Quelle | Zitat |",
        "| --- | --- | --- | --- |",
    ]
    for forbidden in spec.forbidden:
        cmd = f"`{_cell(forbidden.command)}`" if forbidden.command else "(nur CR)"
        out.append(
            f"| {cmd} | {_cell(forbidden.reason)} "
            f"| {_source_link(forbidden.source, forbidden.page)} | „{_cell(forbidden.quote)}“ |"
        )
    return "\n".join(out) + "\n"


def write_tables(out_dir: Path) -> list[Path]:
    """Schreibt alle erzeugten Tabellen nach ``out_dir`` und liefert die Dateien."""
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {"pids.md": pid_table(), "befehle.md": command_tables()}
    written = []
    for name, text in files.items():
        path = out_dir / name
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8")  # nur bei Änderung: Sphinx baut sonst neu
        written.append(path)
    return written


def main() -> None:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "_gen"
    for path in write_tables(out_dir):
        print(path)


if __name__ == "__main__":
    main()
