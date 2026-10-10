"""Übersetzung der Texte für Oberfläche, Kommandozeile und Bericht (Deutsch/Englisch).

Die Texte im Code sind deutsch und werden mit ``tr("…")`` ausgegeben; ist Englisch
eingestellt, kommt die Übersetzung aus ``locale/en.po`` (Format von GNU gettext, die
deutschen Texte sind die ``msgid``). Platzhalter stehen in geschweiften Klammern und
werden nach dem Übersetzen gefüllt: ``tr("Verbinde mit {port} …").format(port=port)``.
Texte, die schon beim Import feststehen (Tabellen, Konstanten), markiert ``N_``; sie
werden erst bei der Ausgabe mit ``tr`` übersetzt. So bleiben gespeicherte Sitzungen
sprachneutral: sie enthalten die deutschen Texte, angezeigt wird die eingestellte
Sprache. QML nimmt ``qsTr`` und denselben Katalog (``ui/window.py``).

Neue Texte trägt ``uv run python tools/translations.py`` in ``en.po`` ein;
``tests/unit/test_i18n.py`` schlägt fehl, solange eine Übersetzung fehlt oder ein
deutscher Text ohne ``tr`` ausgegeben wird.

Dieses Modul gehört zu keiner Schicht und importiert nichts aus dem Paket; jede
Schicht darf es benutzen.
"""

import ast
import os
from functools import cache
from pathlib import Path

LANGUAGES = ("de", "en")
SOURCE_LANGUAGE = "de"
LOCALE_DIR = Path(__file__).with_name("locale")

_language = SOURCE_LANGUAGE


def set_language(language: str) -> None:
    """Sprache für alle folgenden Ausgaben; ``ValueError`` bei unbekannter Sprache."""
    global _language
    if language not in LANGUAGES:
        raise ValueError(f"unbekannte Sprache: {language!r}")
    _language = language


def language() -> str:
    return _language


def system_language() -> str:
    """Sprache aus der Umgebung wie bei gettext (``LANGUAGE``, ``LC_ALL``,
    ``LC_MESSAGES``, ``LANG``): Deutsch, wenn dort Deutsch steht, sonst Englisch."""
    for name in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(name, "")
        if not value:
            continue
        # LANGUAGE kann eine Liste sein ("de:en"); die erste Angabe zählt
        first = value.split(":")[0].split("_")[0].split(".")[0].lower()
        if first in ("c", "posix", ""):
            return "en"
        return "de" if first == "de" else "en"
    return "en"


Entry = tuple[str, ...]  # eine Übersetzung oder (Singular, Plural)


def parse_po(text: str) -> dict[str, Entry]:
    """``msgid`` → Übersetzung aus einer PO-Datei; leere und unvollständige Einträge
    fehlen. Kommentare und ``#, fuzzy`` werden ignoriert (fuzzy zählt als übersetzt
    nur, wenn ``msgstr`` gefüllt ist)."""
    entries: dict[str, Entry] = {}
    fields: dict[str, str] = {}
    current: str | None = None

    def flush() -> None:
        msgid = fields.get("msgid", "")
        if msgid:
            if "msgid_plural" in fields:
                forms = (fields.get("msgstr[0]", ""), fields.get("msgstr[1]", ""))
                if all(forms):
                    entries[msgid] = forms
            elif fields.get("msgstr"):
                entries[msgid] = (fields["msgstr"],)
        fields.clear()

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            if not line and fields:
                flush()
                current = None
            continue
        if line.startswith('"'):
            if current is not None:
                fields[current] += _unquote(line)
            continue
        key, _, rest = line.partition(" ")
        if key == "msgid" and "msgid" in fields:
            flush()
        current = key
        fields[key] = _unquote(rest.strip())
    flush()
    return entries


def _unquote(token: str) -> str:
    value = ast.literal_eval(token)
    if not isinstance(value, str):
        raise ValueError(f"kein PO-String: {token}")
    return value


@cache
def catalog(language: str) -> dict[str, Entry]:
    if language == SOURCE_LANGUAGE:
        return {}
    return parse_po((LOCALE_DIR / f"{language}.po").read_text(encoding="utf-8"))


def tr(text: str) -> str:
    """``text`` (deutsch) in der eingestellten Sprache; ohne Übersetzung unverändert."""
    if _language == SOURCE_LANGUAGE or not text:
        return text
    entry = catalog(_language).get(text)
    return entry[0] if entry else text


def trn(singular: str, plural: str, n: int) -> str:
    """Einzahl oder Mehrzahl nach ``n`` (Deutsch und Englisch: Einzahl nur bei 1)."""
    form = 0 if n == 1 else 1
    if _language != SOURCE_LANGUAGE:
        entry = catalog(_language).get(singular)
        if entry is not None and len(entry) == 2:
            return entry[form]
    return (singular, plural)[form]


def N_(text: str) -> str:
    """Markiert ``text`` zum Übersetzen, ohne es schon zu übersetzen."""
    return text


def decimal(value: float, digits: int = 1) -> str:
    """Zahl mit ``digits`` Nachkommastellen und dem Dezimalzeichen der Sprache."""
    text = f"{value:.{digits}f}"
    return text.replace(".", ",") if _language == "de" else text


def csv_delimiter() -> str:
    """Trennzeichen für CSV wie in der Tabellenkalkulation der Sprache: Deutsch ``;``
    (das Komma ist Dezimalzeichen), Englisch ``,``."""
    return ";" if _language == "de" else ","
