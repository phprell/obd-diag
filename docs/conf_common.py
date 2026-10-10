"""Gemeinsame Sphinx-Konfiguration für die deutsche (``docs/``) und die englische
Dokumentation (``docs/en/``).

Beide Bäume haben dieselben Dateinamen; die deutsche liegt auf GitHub Pages an der
Wurzel, die englische unter ``en/``. Der Link oben in der Seitenleiste
(``_templates/language.html``) führt zur selben Seite in der anderen Sprache.
"""

import sys
from pathlib import Path
from typing import Any

DOCS = Path(__file__).resolve().parent
ROOT = DOCS.parent
sys.path.insert(0, str(ROOT))

from obd_diag import __version__, i18n  # noqa: E402
from tools.docs_tables import write_tables  # noqa: E402

# Sprache → (Quellordner, Pfad zur Wurzel der anderen Sprache, Linktext)
_LANGUAGES = {
    "de": (DOCS, "en/", "English"),
    "en": (DOCS / "en", "../", "Deutsch"),
}


def configure(language: str) -> dict[str, Any]:
    """Die Einstellungen für ``conf.py`` der Sprache ``language``."""
    source, other_root, other_label = _LANGUAGES[language]
    # Auch die Hilfetexte der Kommandozeile (sphinx-argparse) in dieser Sprache
    i18n.set_language(language)
    write_tables(source / "_gen", language)
    other = "en" if language == "de" else "de"
    return {
        "project": "obd-diag",
        "author": "Philipp Prell",
        "copyright": "2026, Philipp Prell",
        "release": __version__,
        "version": __version__,
        "language": language,
        "extensions": [
            "myst_parser",
            "sphinx.ext.autodoc",
            "sphinx.ext.viewcode",
            "sphinxarg.ext",
            "sphinxcontrib.mermaid",
        ],
        "source_suffix": {".md": "markdown", ".rst": "restructuredtext"},
        # Die deutsche Dokumentation enthält die englische nicht
        "exclude_patterns": ["_build", "_gen", "en", "Thumbs.db", ".DS_Store"],
        "myst_enable_extensions": ["colon_fence", "deflist"],
        "myst_heading_anchors": 3,
        # Docstrings verweisen mit ``Name`` auf Code; das sind keine Sphinx-Verweise.
        "default_role": "literal",
        "autodoc_member_order": "bysource",
        "autodoc_typehints": "description",
        "autodoc_default_options": {"members": True, "show-inheritance": True},
        "templates_path": [str(DOCS / "_templates")],
        "html_theme": "furo",
        "html_title": f"obd-diag {__version__}",
        # Die Bilder bindet jede Sprache selbst ein; aus _static wird nur das Skript gebraucht
        "html_static_path": [str(DOCS / "_static" / "mermaid-size.js")],
        "html_last_updated_fmt": "%d.%m.%Y" if language == "de" else "%Y-%m-%d",
        "html_theme_options": {
            "source_repository": "https://github.com/phprell/obd-diag/",
            "source_branch": "main",
            "source_directory": "docs/" if language == "de" else "docs/en/",
        },
        "html_sidebars": {
            "**": [
                "sidebar/brand.html",
                "language.html",
                "sidebar/search.html",
                "sidebar/scroll-start.html",
                "sidebar/navigation.html",
                "sidebar/scroll-end.html",
                "sidebar/variant-selector.html",
            ]
        },
        "html_context": {
            "other_language": other,
            "other_language_root": other_root,
            "other_language_label": other_label,
        },
        "mermaid_version": "11.4.1",
        # Diagramme in natürlicher Größe statt fest 500 px hoch
        "mermaid_height": "auto",
        "mermaid_width": "100%",
        "html_js_files": ["mermaid-size.js"],
    }
