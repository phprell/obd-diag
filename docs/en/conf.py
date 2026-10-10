"""Sphinx-Konfiguration der englischen Dokumentation (siehe ``docs/conf.py``)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conf_common import configure

globals().update(configure("en"))
