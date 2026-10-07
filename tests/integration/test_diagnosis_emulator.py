"""Vollständige Diagnose (``run_diagnosis``, ``obd-diag diagnose``) gegen den Emulator.

Antworten von ELM327-emulator 4.0.0 (Szenario ``car``):

- ``0101``: ``41 01 <A> 07 A1 00``, A = MIL + Zahl aus ``DTC_STORED``: standardgemäß
  (Otto-Motor; Aussetzer, Kraftstoffsystem, Komponenten, Katalysator, Lambdasonde und
  AGR unterstützt und abgeschlossen). Unverändert übernommen.
- ``0902``: standardgemäß mehrteilig (``014`` / ``0: 49 02 01 ...``), aber reihum
  eine von drei FIN (WP0ZZZ99ZTS390000, MAT403096BNL00000, SB1ZS3JE60E282102). Für
  feste Erwartungen wird die Antwort über ``emulator.answer["VIN"]`` festgelegt.
- Mode 02: nur ``020200`` im Format mit Frame-Nummer (``42 02 00 00 00``, kein Code);
  die übrigen PIDs erwartet der Emulator ohne Frame-Nummer (``0205`` statt
  ``020500``) und lehnt sie dann mit ``7F 02 12`` ab. Nach SAE J1979 gehört die
  Frame-Nummer zur Anfrage; ``standard_freeze_frame`` stellt die Einträge darauf um
  und liefert einen Freeze Frame zu P0133.
"""

import json
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from obd_diag.cli import main
from obd_diag.data.dtc_catalog import DtcCatalog
from obd_diag.protocol.elm327 import Elm327
from obd_diag.services.readiness import MonitorState
from obd_diag.services.session import load_session, run_diagnosis
from obd_diag.services.vehicle import read_vin
from obd_diag.transport.serial import SerialTransport
from tests.fakes import FakeCatalog

elm = pytest.importorskip("elm")
obd_message = pytest.importorskip("elm.obd_message")

pytestmark = pytest.mark.integration

EMULATOR_VINS = {"WP0ZZZ99ZTS390000", "MAT403096BNL00000", "SB1ZS3JE60E282102"}
VIN = "WVWZZZ1KZ6W123456"


def _answer(data: str) -> str:
    size = len(data.split())
    answer: str = (
        obd_message.HD(obd_message.ECU_R_ADDR_E)
        + obd_message.SZ(f"{size:02X}")
        + obd_message.DT(data)
    )
    return answer


@pytest.fixture
def standard_freeze_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mode 02 mit Frame-Nummer 00 wie in SAE J1979; Freeze Frame zu P0133."""
    car = obd_message.ObdMessage["car"]
    footer = obd_message.ELM_FOOTER
    for name, pid, data in (
        ("INJ_MF_2", "02", "42 02 00 01 33"),
        ("DTC_COOLANT_TEMP", "05", "42 05 00 73"),
        ("DTC_RPM", "0C", "42 0C 00 1A F8"),
        ("DTC_SPEED", "0D", "42 0D 00 00"),
    ):
        monkeypatch.setitem(car[name], "Request", f"^02{pid}00{footer}")
        monkeypatch.setitem(car[name], "Response", _answer(data))


@pytest.fixture
def start_emulator(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[], Any]]:
    monkeypatch.setattr(obd_message, "DTC_STORED", ["0133", "0300"])
    monkeypatch.setattr(obd_message, "DTC_PENDING", ["0133"])
    monkeypatch.setattr(obd_message, "DTC_PERMANENT", [])
    started: list[Any] = []

    def start() -> Any:
        emu = elm.Elm()
        emu.set_sorted_obd_msg("car")
        emu.port_name = emu.get_pty()
        threading.Thread(target=emu.run, daemon=True).start()
        deadline = time.monotonic() + 5
        while getattr(emu, "threadState", None) != emu.THREAD.ACTIVE:
            if time.monotonic() > deadline:
                pytest.fail("Emulator startet nicht")
            time.sleep(0.01)
        started.append(emu)
        return emu

    yield start
    for emu in started:
        emu.terminate()


@pytest.fixture
def emulator(standard_freeze_frame: None, start_emulator: Callable[[], Any]) -> Any:
    emu = start_emulator()
    emu.answer["VIN"] = obd_message.PA("01 " + VIN.encode("ascii").hex(" ").upper())
    return emu


def test_run_diagnosis_against_emulator(emulator: Any) -> None:
    catalog = FakeCatalog({"P0300": "Zündaussetzer erkannt"})
    with SerialTransport(emulator.port_name) as transport:
        session = run_diagnosis(Elm327(transport), catalog)
    assert [c.code for c in session.scan.codes] == ["P0133", "P0300", "P0133"]
    readiness = session.readiness
    assert readiness is not None
    assert (readiness.mil_on, readiness.dtc_count, readiness.compression_ignition) == (
        True,
        2,
        False,
    )
    supported = {m.key for m in readiness.monitors if m.state is not MonitorState.NOT_SUPPORTED}
    assert supported == {
        "misfire",
        "fuel_system",
        "components",
        "catalyst",
        "oxygen_sensor",
        "egr",
    }
    assert readiness.ready
    assert session.freeze_frame is not None
    assert session.freeze_frame.dtc == "P0133"
    assert session.freeze_frame.values == {"coolant_temp_c": 75, "rpm": 1726.0, "speed_kmh": 0}
    assert session.vehicle is not None
    assert (session.vehicle.vin, session.vehicle.manufacturer) == (VIN, "Volkswagen")
    assert session.vehicle.online == {}
    assert "CLEAR_DIAG_TC" not in emulator.counters  # nur lesend


def test_emulator_native_answers(start_emulator: Callable[[], Any]) -> None:
    """Ohne Anpassung: FIN reihum, Freeze Frame leer (kein Code, PIDs abgelehnt)."""
    emu = start_emulator()
    with SerialTransport(emu.port_name) as transport:
        session = run_diagnosis(Elm327(transport), None)
        vins = {read_vin(Elm327(transport)) for _ in range(3)}
    assert session.freeze_frame is None
    assert session.readiness is not None
    assert session.vehicle is not None and session.vehicle.vin in EMULATOR_VINS
    assert vins == EMULATOR_VINS


@pytest.fixture
def cli_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> Path:
    monkeypatch.setattr(DtcCatalog, "default", classmethod(lambda cls: None))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    capsys.readouterr()  # Startmeldung des Emulators verwerfen
    return tmp_path


def test_cli_diagnose_json(
    emulator: Any, cli_env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    assert main(["diagnose", "--port", emulator.port_name, "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["format"] == "obd-diag-session"
    assert data["scan"]["protocol"] == "ISO 15765-4 (CAN 11/500)"
    assert data["readiness"]["ready"] is True
    assert data["freeze_frame"]["dtc"] == "P0133"
    assert data["vehicle"]["vin"] == VIN
    assert not (cli_env / "data").exists()


def test_cli_diagnose_save_and_pdf(
    emulator: Any, cli_env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    pdf = cli_env / "bericht.pdf"
    assert main(["diagnose", "--port", emulator.port_name, "--save", "--pdf", str(pdf)]) == 0
    out, err = capsys.readouterr()
    assert "  AU-bereit: ja\n" in out
    assert "Freeze Frame (ausgelöst durch P0133):" in out
    (saved,) = (cli_env / "data" / "obd-diag" / "sessions").iterdir()
    assert f"Sitzung gespeichert: {saved}\n" in err
    assert f"PDF-Bericht: {pdf}\n" in err
    session = load_session(saved)
    assert session.vehicle is not None and session.vehicle.vin == VIN
    assert pdf.read_bytes().startswith(b"%PDF")


def test_cli_vin_against_emulator(
    emulator: Any, cli_env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    assert main(["vin", "--port", emulator.port_name]) == 0
    assert f"  FIN:        {VIN}\n" in capsys.readouterr().out
