"""Übersetzungskatalog ``src/obd_diag/locale/en.po`` pflegen.

Sammelt alle zu übersetzenden Texte: in Python die Literale in ``tr("…")``,
``N_("…")`` und ``trn("…", "…", n)``, in QML die Literale in ``qsTr("…")``. Neue Texte
kommen mit leerer Übersetzung in den Katalog, nicht mehr benutzte fallen heraus,
vorhandene Übersetzungen bleiben.

    uv run python tools/translations.py           # Katalog aktualisieren
    uv run python tools/translations.py --todo    # fehlende Übersetzungen als JSON
    uv run python tools/translations.py --fill F  # JSON {"Nummer": "Übersetzung"} eintragen

``tests/unit/test_i18n.py`` prüft, dass der Katalog aktuell und vollständig ist.
"""

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "src" / "obd_diag"
CATALOG = PACKAGE / "locale" / "en.po"

TR_FUNCTIONS = {"tr", "N_"}
PLURAL_FUNCTIONS = {"trn"}
_QSTR = re.compile(r'qsTr\(\s*"((?:[^"\\\n]|\\.)*)"\s*[,)]')

HEADER = """\
# Englische Übersetzung von obd-diag. Quelltexte sind deutsch (msgid).
# Gepflegt mit: uv run python tools/translations.py (siehe src/obd_diag/i18n.py)
msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\\n"
"Language: en\\n"
"Plural-Forms: nplurals=2; plural=(n != 1);\\n"
"""


@dataclass
class Message:
    msgid: str
    plural: str | None = None
    refs: list[str] = field(default_factory=list)
    msgstr: tuple[str, ...] = ()

    @property
    def translated(self) -> bool:
        return bool(self.msgstr) and all(self.msgstr)


def _python_files() -> list[Path]:
    return sorted(PACKAGE.rglob("*.py"))


def _qml_files() -> list[Path]:
    return sorted(PACKAGE.rglob("*.qml"))


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _literal(node: ast.expr) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def extract() -> dict[str, Message]:
    """Alle Texte in Fundstellen-Reihenfolge; ``ValueError`` bei ``tr`` mit einem
    f-String (der ließe sich nicht nachschlagen)."""
    found: dict[str, Message] = {}

    def add(msgid: str, ref: str, plural: str | None = None) -> None:
        message = found.setdefault(msgid, Message(msgid, plural))
        if plural is not None and message.plural is None:
            message.plural = plural
        if ref not in message.refs:
            message.refs.append(ref)

    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            name = node.func.id
            ref = _rel(path)  # ohne Zeilennummer, sonst änderte jede Code-Änderung den Katalog
            tr_call = name in TR_FUNCTIONS | PLURAL_FUNCTIONS and node.args
            if tr_call and isinstance(node.args[0], ast.JoinedStr):
                raise ValueError(f"{ref}:{node.lineno}: {name}() mit f-String; .format() nehmen")
            if name in TR_FUNCTIONS and node.args:
                text = _literal(node.args[0])
                if text:
                    add(text, ref)
            elif name in PLURAL_FUNCTIONS and len(node.args) >= 2:
                singular, plural = _literal(node.args[0]), _literal(node.args[1])
                if singular and plural:
                    add(singular, ref, plural)
    for path in _qml_files():
        text = path.read_text(encoding="utf-8")
        for match in _QSTR.finditer(text):
            add(json.loads(f'"{match.group(1)}"'), _rel(path))
    return found


def _quote(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped.replace(chr(9), "\\t")}"'


def render(messages: dict[str, Message]) -> str:
    parts = [HEADER]
    for m in messages.values():
        lines = [f"#: {ref}" for ref in m.refs]
        lines.append(f"msgid {_quote(m.msgid)}")
        if m.plural is not None:
            forms = m.msgstr if len(m.msgstr) == 2 else ("", "")
            lines.append(f"msgid_plural {_quote(m.plural)}")
            lines += [f"msgstr[{i}] {_quote(form)}" for i, form in enumerate(forms)]
        else:
            lines.append(f"msgstr {_quote(m.msgstr[0] if len(m.msgstr) == 1 else '')}")
        parts.append("\n".join(lines) + "\n")
    return "\n".join(parts)


def updated(existing: dict[str, tuple[str, ...]]) -> dict[str, Message]:
    """Die aktuellen Texte mit den vorhandenen Übersetzungen."""
    messages = extract()
    for m in messages.values():
        m.msgstr = existing.get(m.msgid, ())
    return messages


def _read_catalog() -> dict[str, tuple[str, ...]]:
    sys.path.insert(0, str(PACKAGE.parent))
    from obd_diag.i18n import parse_po

    return parse_po(CATALOG.read_text(encoding="utf-8")) if CATALOG.exists() else {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--todo", action="store_true", help="fehlende Übersetzungen ausgeben")
    parser.add_argument("--fill", type=Path, help="JSON mit Übersetzungen nach Nummer")
    args = parser.parse_args(argv)

    messages = updated(_read_catalog())
    missing = [m for m in messages.values() if not m.translated]
    if args.fill is not None:
        fills = json.loads(args.fill.read_text(encoding="utf-8"))
        for number, value in fills.items():
            m = missing[int(number)]
            m.msgstr = tuple(value) if isinstance(value, list) else (value,)
    CATALOG.parent.mkdir(exist_ok=True)
    CATALOG.write_text(render(messages), encoding="utf-8")
    missing = [m for m in messages.values() if not m.translated]
    if args.todo:
        todo = {
            str(i): m.msgid if m.plural is None else [m.msgid, m.plural]
            for i, m in enumerate(missing)
        }
        print(json.dumps(todo, ensure_ascii=False, indent=1))
    print(f"{len(messages)} Texte, {len(missing)} ohne Übersetzung", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
