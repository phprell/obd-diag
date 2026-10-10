"""Die Dokumentation gibt es vollständig auf Deutsch (``docs/``) und Englisch
(``docs/en/``): dieselben Seiten mit gleichem Aufbau.

Neue oder geänderte Seiten kommen in beiden Sprachen in denselben PR. Geprüft wird der
Aufbau (Überschriften, Codeblöcke, Bilder, Tabellenzeilen), nicht der Wortlaut.
"""

import re
from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parents[2] / "docs"
EN = DOCS / "en"
# Bindet CHANGELOG.md ein (bleibt deutsch); die englische Seite hat Titel und Hinweis
_STRUCTURE_EXEMPT = {"entwickeln/aenderungen.md"}


def _pages(root: Path) -> set[str]:
    skip = {"_build", "_gen", "en"} if root == DOCS else {"_gen"}
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*.md")
        if not skip & set(path.relative_to(root).parts)
    }


def test_same_pages_in_both_languages() -> None:
    german, english = _pages(DOCS), _pages(EN)
    assert sorted(german - english) == [], "ohne englische Fassung (docs/en/)"
    assert sorted(english - german) == [], "ohne deutsche Fassung (docs/)"


def _structure(text: str) -> dict[str, object]:
    fences = re.findall(r"^(`{3,}|:{3,})(\{?[\w-]*\}?)", text, flags=re.MULTILINE)
    outside = re.sub(r"^```.*?^```", "", text, flags=re.MULTILINE | re.DOTALL)
    return {
        "Überschriften": [len(h) for h in re.findall(r"^(#+) ", outside, flags=re.MULTILINE)],
        "Blöcke": [kind for _, kind in fences],
        "Bilder": re.findall(r"\{image\} (\S+)", text),
        "Tabellenzeilen": len(re.findall(r"^\|", outside, flags=re.MULTILINE)),
        "Verweise": sorted(re.findall(r"\{doc\}`([^`]+)`", text)),
    }


@pytest.mark.parametrize("page", sorted(_pages(DOCS) - _STRUCTURE_EXEMPT))
def test_same_structure(page: str) -> None:
    german = _structure((DOCS / page).read_text(encoding="utf-8"))
    english = _structure((EN / page).read_text(encoding="utf-8"))
    assert english == german


def test_screenshots_in_both_languages() -> None:
    german = {p.name for p in (DOCS / "_static" / "screenshots").glob("*.png")}
    english = {p.name for p in (EN / "_static" / "screenshots").glob("*.png")}
    assert german and english == german, "uv run python tools/docs_screenshots.py --lang en"
