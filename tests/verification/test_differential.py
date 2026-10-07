"""Differenzialtest ``decode_dtc`` gegen python-OBD für alle 65536 Bytepaare.

python-OBD steht unter GPL-2.0 und darf nur in Tests vorkommen, nie in ``src/``. Das
Ergebnis eines Vergleichs in einer eigenen venv (Prüfsumme der ganzen Tabelle und
Stichproben) liegt in tests/fixtures/traces/python_obd_dtc_table.json. Ist python-OBD
installiert (derzeit indirekt über ELM327-emulator in der dev-Gruppe), läuft der
Vergleich zusätzlich live, sonst wird er übersprungen.
"""

import hashlib
import importlib
import json
from pathlib import Path
from typing import Any

import pytest

from obd_diag.protocol.dtc_decode import decode_dtc
from tests.verification.helpers import dtc_text

FIXTURE = Path(__file__).parent.parent / "fixtures" / "traces" / "python_obd_dtc_table.json"


def _table() -> list[str]:
    return [decode_dtc(high, low) for high in range(256) for low in range(256)]


def test_all_codes_match_python_obd_table() -> None:
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    table = _table()
    for raw, code in expected["samples"].items():
        assert table[int(raw, 16)] == code
    assert hashlib.sha256("\n".join(table).encode()).hexdigest() == expected["sha256"]


def test_all_codes_match_datasheet_table() -> None:
    # Unabhängig von python-OBD: Tabelle aus dem Datenblatt (S. 32) im Test-Encoder
    assert _table() == [dtc_text((high, low)) for high in range(256) for low in range(256)]


def test_live_against_python_obd() -> None:
    try:
        decoders: Any = importlib.import_module("obd.decoders")
    except ImportError:
        pytest.skip("python-OBD nicht installiert (uv run --with obd ...)")
    for high in range(256):
        for low in range(256):
            theirs = decoders.parse_dtc((high, low))
            if (high, low) == (0, 0):
                assert theirs is None  # Füllung; parse_dtc_response überspringt sie ebenso
                continue
            # python-OBD schreibt A-F klein (z. B. P209a)
            assert theirs[0].upper() == decode_dtc(high, low), (high, low)
