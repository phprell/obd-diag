"""Sphinx-Konfiguration der Dokumentation von obd-diag.

Bauen: ``uv run --group docs sphinx-build -W docs docs/_build`` (siehe
``docs/entwickeln/regeln.md``). Die Tabellen der Befehlsreferenz und der PID-Formeln
erzeugt ``tools/docs_tables.py`` bei jedem Bauen neu nach ``docs/_gen``.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from obd_diag import __version__  # noqa: E402
from tools.docs_tables import write_tables  # noqa: E402

write_tables(Path(__file__).parent / "_gen")

project = "obd-diag"
author = "Philipp Prell"
copyright = "2026, Philipp Prell"
release = __version__
version = __version__
language = "de"

extensions = [
    "myst_parser",
    "sphinx.ext.autodoc",
    "sphinx.ext.viewcode",
    "sphinxarg.ext",
    "sphinxcontrib.mermaid",
]

source_suffix = {".md": "markdown", ".rst": "restructuredtext"}
exclude_patterns = ["_build", "_gen", "Thumbs.db", ".DS_Store"]
myst_enable_extensions = ["colon_fence", "deflist"]
myst_heading_anchors = 3
# Docstrings verweisen mit ``Name`` auf Code; das sind keine Sphinx-Verweise.
default_role = "literal"

autodoc_member_order = "bysource"
autodoc_typehints = "description"
autodoc_default_options = {"members": True, "show-inheritance": True}

html_theme = "furo"
html_title = f"obd-diag {__version__}"
html_static_path = ["_static"]
html_last_updated_fmt = "%d.%m.%Y"
html_theme_options = {
    "source_repository": "https://github.com/phprell/obd-diag/",
    "source_branch": "main",
    "source_directory": "docs/",
}

mermaid_version = "11.4.1"
# Diagramme in natürlicher Größe statt fest 500 px hoch
mermaid_height = "auto"
mermaid_width = "100%"
html_js_files = ["mermaid-size.js"]
