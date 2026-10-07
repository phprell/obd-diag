"""Vollständiger Scan gegen den ELM327-Emulator (Szenario ``car``) über ein pty.

Die Fehlercode-Antworten des Emulators baut ``tests/conftest.py`` standardgemäß
(mit Zählbyte); drei gespeicherte Codes ergeben damit eine mehrteilige Antwort.
"""

import json
import threading
import time
from collections.abc import Iterator

import pytest

from obd_diag.cli import main
from obd_diag.data.dtc_catalog import DtcCatalog
from obd_diag.protocol.elm327 import Elm327
from obd_diag.services.diagnostics import DiagnosticCode, DtcKind, scan
from obd_diag.transport.serial import SerialTransport
from tests.fakes import FakeCatalog

elm = pytest.importorskip("elm")
obd_message = pytest.importorskip("elm.obd_message")

pytestmark = pytest.mark.integration


@pytest.fixture
def car_port(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    # Die Antworten auf 03/07/0A lesen diese Modul-Listen bei jeder Anfrage.
    monkeypatch.setattr(obd_message, "DTC_STORED", ["0133", "0300", "C100"])
    monkeypatch.setattr(obd_message, "DTC_PENDING", ["0133"])
    monkeypatch.setattr(obd_message, "DTC_PERMANENT", ["0420"])
    emulator = elm.Elm()
    emulator.set_sorted_obd_msg("car")
    port: str = emulator.get_pty()
    thread = threading.Thread(target=emulator.run, daemon=True)
    thread.start()
    # Erst wenn der Emulator läuft, ist seine Startmeldung auf stdout ausgegeben.
    deadline = time.monotonic() + 5
    while getattr(emulator, "threadState", None) != emulator.THREAD.ACTIVE:
        if time.monotonic() > deadline:
            pytest.fail("Emulator startet nicht")
        time.sleep(0.01)
    yield port
    emulator.terminate()


def test_scan_against_emulator(car_port: str) -> None:
    catalog = FakeCatalog({"P0300": "Zündaussetzer erkannt"})
    with SerialTransport(car_port) as transport:
        result = scan(Elm327(transport), catalog)
    assert "ELM327" in result.adapter
    assert result.protocol == "ISO 15765-4 (CAN 11/500)"
    assert result.voltage is not None and 9.0 < result.voltage < 16.0
    assert [(c.code, c.kind) for c in result.codes] == [
        ("P0133", DtcKind.STORED),
        ("P0300", DtcKind.STORED),
        ("U0100", DtcKind.STORED),
        ("P0133", DtcKind.PENDING),
        ("P0420", DtcKind.PERMANENT),
    ]
    assert result.codes[1] == DiagnosticCode("P0300", DtcKind.STORED, catalog.lookup("P0300", "de"))


def test_cli_scan_json_against_emulator(
    car_port: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Unabhängig davon, ob der echte Katalog schon gebaut ist
    monkeypatch.setattr(DtcCatalog, "default", classmethod(lambda cls: None))
    capsys.readouterr()  # Startmeldung des Emulators verwerfen
    assert main(["scan", "--port", car_port, "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["protocol"] == "ISO 15765-4 (CAN 11/500)"
    assert [(c["code"], c["kind"]) for c in data["codes"]] == [
        ("P0133", "stored"),
        ("P0300", "stored"),
        ("U0100", "stored"),
        ("P0133", "pending"),
        ("P0420", "permanent"),
    ]


def test_scan_many_codes_multi_frame(car_port: str, monkeypatch: pytest.MonkeyPatch) -> None:
    codes = ["0133", "0300", "C100", "0420", "0171", "0101", "0442"]
    monkeypatch.setattr(obd_message, "DTC_STORED", codes)
    with SerialTransport(car_port) as transport:
        result = scan(Elm327(transport), None)
    stored = [c.code for c in result.codes if c.kind is DtcKind.STORED]
    assert stored == ["P0133", "P0300", "U0100", "P0420", "P0171", "P0101", "P0442"]
