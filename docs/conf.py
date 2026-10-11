"""Sphinx-Konfiguration der deutschen Dokumentation von obd-diag.

Bauen: ``uv run --group docs sphinx-build -W docs docs/_build``, die englische mit
``uv run --group docs sphinx-build -W docs/en docs/_build/en`` (siehe
``docs/entwickeln/regeln.md``). Die Einstellungen stehen in ``conf_common.py``; die
Tabellen der Befehlsreferenz und der PID-Formeln erzeugt ``tools/docs_tables.py`` bei
jedem Bauen neu nach ``docs/_gen`` bzw. ``docs/en/_gen``.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from conf_common import configure

globals().update(configure("de"))
