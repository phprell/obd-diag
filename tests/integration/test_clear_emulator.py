"""Fehlercodes löschen gegen den ELM327-Emulator (Szenario ``car``) über ein pty.

Eigenheiten des Emulators 4.0.0:

- ``010C`` liefert der Reihe nach wechselnde Drehzahlen, beginnend mit 1303,75 1/min;
  der Motor "läuft" also. Für den Lösch-Pfad wird die Antwort über ``emulator.answer``
  auf 0 1/min festgelegt.
- ``04`` antwortet ``44`` und setzt den Zähler ``cmd_dtc_cleared``, danach liefern
  03/07/0A keine Codes mehr. ``ATZ`` setzt den Zähler aber zurück, und der
  Kontroll-Scan beginnt mit ``ATZ``. Ein echtes Steuergerät vergisst gelöschte Codes
  nicht beim Adapter-Reset; die Tests merken sich das Löschen deshalb zusätzlich in
  ``emulator.presets``, die jeder Reset wieder einspielt.
- Mode 02 kennt nur ``020200`` (ohne Code) im ELM327-Format; die übrigen PIDs
  erwartet er ohne Frame-Nummer und antwortet auf ``02xx00`` mit ``NO DATA``.
"""

import json
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from obd_diag.cli import main
from obd_diag.data.dtc_catalog import DtcCatalog
from obd_diag.protocol.elm327 import Elm327
from obd_diag.services.clear import ClearRefused, clear_codes
from obd_diag.services.diagnostics import DtcKind, scan
from obd_diag.transport.serial import SerialTransport

elm = pytest.importorskip("elm")
obd_message = pytest.importorskip("elm.obd_message")

pytestmark = pytest.mark.integration

RPM_ZERO = (
    obd_message.HD(obd_message.ECU_R_ADDR_E) + obd_message.SZ("04") + obd_message.DT("41 0C 00 00")
)
CODES = [
    ("P0133", DtcKind.STORED),
    ("P0300", DtcKind.STORED),
    ("P0133", DtcKind.PENDING),
    ("P0420", DtcKind.PERMANENT),
]


@pytest.fixture
def emulator(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    monkeypatch.setattr(obd_message, "DTC_STORED", ["0133", "0300"])
    monkeypatch.setattr(obd_message, "DTC_PENDING", ["0133"])
    monkeypatch.setattr(obd_message, "DTC_PERMANENT", ["0420"])
    # Gelöschte Codes bleiben gelöscht, auch über ATZ hinweg (siehe Moduldoku).
    monkeypatch.setitem(
        obd_message.ObdMessage["car"]["CLEAR_DIAG_TC"],
        "Exec",
        'self.counters["cmd_dtc_cleared"] = self.presets["cmd_dtc_cleared"] = True',
    )
    emu = elm.Elm()
    emu.set_sorted_obd_msg("car")
    emu.port_name = emu.get_pty()
    thread = threading.Thread(target=emu.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while getattr(emu, "threadState", None) != emu.THREAD.ACTIVE:
        if time.monotonic() > deadline:
            pytest.fail("Emulator startet nicht")
        time.sleep(0.01)
    yield emu
    emu.terminate()


def _codes(port: str) -> list[tuple[str, DtcKind]]:
    with SerialTransport(port) as transport:
        return [(c.code, c.kind) for c in scan(Elm327(transport), None).codes]


def test_refused_while_engine_runs(emulator: Any, tmp_path: Path) -> None:
    with (
        SerialTransport(emulator.port_name) as transport,
        pytest.raises(ClearRefused, match=r"Motor läuft \(1304 1/min\)"),
    ):
        clear_codes(Elm327(transport), None, backup_dir=tmp_path)
    assert "CLEAR_DIAG_TC" not in emulator.counters  # 04 nie gesendet
    assert list(tmp_path.iterdir()) == []
    assert _codes(emulator.port_name) == CODES


def test_clear_with_engine_off(emulator: Any, tmp_path: Path) -> None:
    emulator.answer["RPM"] = RPM_ZERO
    with SerialTransport(emulator.port_name) as transport:
        result = clear_codes(Elm327(transport), None, backup_dir=tmp_path)
    assert emulator.counters["CLEAR_DIAG_TC"] == 1
    assert [(c.code, c.kind) for c in result.before.codes] == CODES
    assert result.after.codes == []  # der Emulator löscht auch die permanenten
    data = json.loads(result.backup_path.read_text(encoding="utf-8"))
    assert [c["code"] for c in data["scan"]["codes"]] == ["P0133", "P0300", "P0133", "P0420"]
    assert data["freeze_frame"] == {"dtc": None, "raw": {"020200": "4202000000"}, "values": {}}
    assert _codes(emulator.port_name) == []


def test_negative_response_from_emulator(emulator: Any, tmp_path: Path) -> None:
    emulator.answer["RPM"] = RPM_ZERO
    emulator.answer["CLEAR_DIAG_TC"] = obd_message.NA("22")
    with (
        SerialTransport(emulator.port_name) as transport,
        pytest.raises(ClearRefused, match="Bedingungen nicht erfüllt"),
    ):
        clear_codes(Elm327(transport), None, backup_dir=tmp_path)
    assert len(list(tmp_path.iterdir())) == 1  # Sicherung bleibt
    assert _codes(emulator.port_name) == CODES


def test_cli_clear_against_emulator(
    emulator: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(DtcCatalog, "default", classmethod(lambda cls: None))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    emulator.answer["RPM"] = RPM_ZERO
    capsys.readouterr()  # Startmeldung des Emulators verwerfen
    assert main(["clear", "--port", emulator.port_name, "--yes"]) == 0
    out = capsys.readouterr().out
    assert "Folgende Fehlercodes werden im Steuergerät gelöscht:\n  P0133" in out
    (backup,) = (tmp_path / "obd-diag" / "backups").iterdir()
    assert f"Gelöscht. Sicherung: {backup}" in out
    assert out.endswith("Keine Fehlercodes gespeichert.\n")
