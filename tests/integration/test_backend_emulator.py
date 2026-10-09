"""Die echten Aktionen der Oberfläche (``ui/backend.py``) gegen den Emulator.

Anders als in ``tests/ui/test_gui_emulator.py`` laufen ``diagnose_port`` und
``clear_port`` hier direkt im Test-Thread, ohne View-Model und Worker. Die Antworten
macht ``tests/emulator_patches.py`` standardgemäß (Freeze Frame mit Frame-Nummer,
Löschen übersteht ``ATZ``).
"""

import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar

import pytest
import yaml

from obd_diag.data.catalog_build import build
from obd_diag.data.dtc_catalog import DtcCatalog
from obd_diag.services.diagnostics import DtcKind
from obd_diag.transport.trace import read_trace
from obd_diag.ui import backend as backend_module
from obd_diag.ui.backend import catalog_available, clear_port, diagnose_port
from tests.emulator_patches import patch_freeze_frame, patch_persistent_clear, rpm_zero

elm = pytest.importorskip("elm")
obd_message = pytest.importorskip("elm.obd_message")

pytestmark = pytest.mark.integration

FIXTURE = Path(__file__).parent.parent / "fixtures" / "dtc_sample.yaml"


@pytest.fixture
def emulator(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    monkeypatch.setattr(obd_message, "DTC_STORED", ["0420", "0300"])
    monkeypatch.setattr(obd_message, "DTC_PENDING", [])
    monkeypatch.setattr(obd_message, "DTC_PERMANENT", [])
    patch_freeze_frame(monkeypatch, obd_message, "0420")
    patch_persistent_clear(monkeypatch, obd_message)
    emu = elm.Elm()
    emu.set_sorted_obd_msg("car")
    emu.port_name = emu.get_pty()
    threading.Thread(target=emu.run, daemon=True).start()
    deadline = time.monotonic() + 5
    while getattr(emu, "threadState", None) != emu.THREAD.ACTIVE:
        if time.monotonic() > deadline:
            pytest.fail("Emulator startet nicht")
        time.sleep(0.01)
    yield emu
    emu.terminate()


class _Catalog(DtcCatalog):
    """Kleiner Katalog aus der Test-Fixture; merkt sich, ob er geschlossen wurde."""

    opened: ClassVar[list["_Catalog"]] = []

    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.closed = False
        _Catalog.opened.append(self)

    def close(self) -> None:
        self.closed = True
        super().close()


@pytest.fixture
def data_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Sicherungen und Mitschnitte in ``tmp_path``, Katalog aus der Fixture."""
    path = tmp_path / "catalog.sqlite"
    build(yaml.safe_load(FIXTURE.read_text(encoding="utf-8")), path, {})
    _Catalog.opened = []
    monkeypatch.setattr(DtcCatalog, "default", classmethod(lambda cls: _Catalog(path)))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    return tmp_path / "data" / "obd-diag"


def test_diagnose_port_with_trace(emulator: Any, data_home: Path) -> None:
    session = diagnose_port(emulator.port_name, 38400, trace=True)
    assert [(c.code, c.kind) for c in session.scan.codes] == [
        ("P0420", DtcKind.STORED),
        ("P0300", DtcKind.STORED),
    ]
    p0420 = session.scan.codes[0].info
    assert p0420 is not None and p0420.title.startswith("Katalysatorwirkungsgrad")
    assert session.freeze_frame is not None and session.freeze_frame.dtc == "P0420"
    assert [c.closed for c in _Catalog.opened] == [True]  # Katalog je Job geöffnet
    # Mitschnitt: Kopf mit Port und Baudrate, alle Befehle und Antworten
    (trace,) = (data_home / "traces").glob("trace-*.log")
    lines = trace.read_text(encoding="utf-8").splitlines()
    assert lines[0].endswith(f" {emulator.port_name} 38400 Baud")
    assert lines[-1].endswith(" -- geschlossen")
    entries = read_trace(trace)
    sent = [data for direction, data in entries if direction == ">>"]
    assert sent[0] == b"ATZ\r"
    assert {b"03\r", b"07\r", b"0A\r", b"0101\r", b"0902\r"} <= set(sent)
    assert b"04\r" not in sent  # nur lesend
    assert any(d == "<<" and b"43" in data for d, data in entries)


def test_diagnose_port_without_trace(emulator: Any, data_home: Path) -> None:
    session = diagnose_port(emulator.port_name, 38400, lang="en")
    info = session.scan.codes[0].info
    assert info is not None and info.title.startswith("Catalyst System Efficiency")
    assert not (data_home / "traces").exists()


def test_clear_port_with_trace(emulator: Any, data_home: Path) -> None:
    emulator.answer["RPM"] = rpm_zero(obd_message)
    result = clear_port(emulator.port_name, 38400, trace=True)
    assert result.backup_path.parent == data_home / "backups"
    assert [c.code for c in result.before.codes] == ["P0420", "P0300"]
    assert result.after.codes == []
    assert emulator.counters["CLEAR_DIAG_TC"] == 1
    assert [c.closed for c in _Catalog.opened] == [True]
    (trace,) = (data_home / "traces").glob("trace-*.log")
    sent = [data for direction, data in read_trace(trace) if direction == ">>"]
    i = sent.index(b"04\r")
    assert sent.count(b"04\r") == 1
    assert b"010C\r" in sent[:i] and b"020200\r" in sent[:i]  # Prüfung und Sicherung davor
    assert sent[i + 1] == b"ATZ\r"  # Kontroll-Scan


def test_clear_port_refused_closes_catalog(emulator: Any, data_home: Path) -> None:
    from obd_diag.services.clear import ClearRefused

    with pytest.raises(ClearRefused, match="Motor läuft"):
        clear_port(emulator.port_name, 38400)
    assert [c.closed for c in _Catalog.opened] == [True]
    assert "CLEAR_DIAG_TC" not in emulator.counters
    assert not (data_home / "backups").exists()


def test_without_catalog(emulator: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(DtcCatalog, "default", classmethod(lambda cls: None))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert not catalog_available()
    session = diagnose_port(emulator.port_name, 38400)
    assert all(c.info is None for c in session.scan.codes)
    emulator.answer["RPM"] = rpm_zero(obd_message)
    assert clear_port(emulator.port_name, 38400).after.codes == []


def test_serial_backend_passes_tracing(emulator: Any, data_home: Path) -> None:
    backend = backend_module.serial_backend()
    backend.diagnose(emulator.port_name, 38400, False, False)
    assert not (data_home / "traces").exists()
    backend.set_tracing(True)
    backend.diagnose(emulator.port_name, 38400, False, False)
    emulator.answer["RPM"] = rpm_zero(obd_message)
    backend.clear(emulator.port_name, 38400)
    assert len(list((data_home / "traces").glob("trace-*.log"))) == 2
    backend.set_tracing(False)
    backend.diagnose(emulator.port_name, 38400, False, False)
    assert len(list((data_home / "traces").glob("trace-*.log"))) == 2
