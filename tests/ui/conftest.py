"""Gemeinsames für die GUI-Tests: Qt ohne Bildschirm, Fake-Backend, Beispieldaten."""

import os

# Vor dem Anlegen der QApplication setzen; überschreibt bewusst eine Desktop-Sitzung
# (z. B. QT_QPA_PLATFORM=wayland), damit Tests nie Fenster öffnen.
os.environ["QT_QPA_PLATFORM"] = "offscreen"
# Software-Rendering: kein OpenGL/EGL nötig (CI-Runner ohne GPU)
os.environ.setdefault("QT_QUICK_BACKEND", "software")

from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from obd_diag.data.dtc_catalog import Cause, DtcInfo
from obd_diag.export.report import export_csv, export_pdf
from obd_diag.protocol.obd import FreezeFrame
from obd_diag.services.clear import ClearResult
from obd_diag.services.diagnostics import DiagnosticCode, DtcKind, ScanResult
from obd_diag.services.readiness import MonitorState
from obd_diag.services.session import Session
from obd_diag.transport.discovery import PortInfo
from obd_diag.ui.backend import Backend
from tests.samples import CREATED

P0420 = DtcInfo(
    "P0420",
    "Katalysatorwirkungsgrad unter Schwellwert (Bank 1)",
    description="Die Sauerstoffspeicherfähigkeit des Hauptkatalysators ist zu gering.",
    causes=(Cause("Katalysator gealtert", "high"), Cause("Abgasleck vor Katalysator", "low")),
    symptoms=("Motorkontrollleuchte an",),
    mil=True,
    emissions_relevant=True,
    repair_difficulty="hard",
    cost_eur=(600, 2500),
)

SCAN = ScanResult(
    adapter="ELM327 v1.5",
    protocol="ISO 15765-4 (CAN 11/500)",
    voltage=12.4,
    codes=[
        DiagnosticCode("P0420", DtcKind.STORED, P0420),
        DiagnosticCode("P1234", DtcKind.STORED),
        DiagnosticCode("U0100", DtcKind.PERMANENT),
        DiagnosticCode("P0420", DtcKind.PENDING, P0420),
    ],
)


class SyncRunner:
    """Führt Jobs sofort im aufrufenden Thread aus (für Tests ohne Threads)."""

    def run(
        self,
        job: Callable[[], Any],
        on_success: Callable[[Any], None],
        on_error: Callable[[Exception], None],
    ) -> None:
        try:
            result = job()
        except Exception as e:
            on_error(e)
        else:
            on_success(result)


def as_session(result: Session | ScanResult) -> Session:
    if isinstance(result, Session):
        return result
    return Session(created=CREATED, scan=result)


def after_clear(session: Session, after: ScanResult) -> Session:
    """Was eine neue Diagnose nach Mode 04 liefert: Kontroll-Scan, offene Monitore,
    leerer Freeze Frame; die FIN bleibt."""
    readiness = session.readiness
    if readiness is not None:
        monitors = tuple(
            replace(m, state=MonitorState.INCOMPLETE)
            if m.state is not MonitorState.NOT_SUPPORTED
            else m
            for m in readiness.monitors
        )
        readiness = replace(readiness, mil_on=False, dtc_count=0, monitors=monitors)
    return replace(session, scan=after, readiness=readiness, freeze_frame=FreezeFrame())


class FakeBackend:
    """Backend ohne seriellen Port; merkt sich die Aufrufe.

    ``diagnose`` liefert ``session`` (ein bloßer ``ScanResult`` wird zur Sitzung ohne
    Readiness, Freeze Frame und FIN). Nach einem erfolgreichen ``clear`` liefert es den
    Zustand nach dem Löschen. Speichern, Laden und Export sind die echten Funktionen.
    """

    def __init__(
        self,
        result: Session | ScanResult = SCAN,
        *,
        ports: list[PortInfo] | None = None,
        catalog: bool = True,
    ) -> None:
        self.session = as_session(result)
        self.ports = ports
        self.catalog = catalog
        self.diagnose_error: Exception | None = None
        self.clear_error: Exception | None = None
        self.clear_result: ClearResult | None = None
        self.calls: list[tuple[str, str, int]] = []
        self.online_flags: list[bool] = []
        self.online_code_flags: list[bool] = []
        self.tracing: list[bool] = []
        self.exports: list[tuple[str, Path]] = []

    def diagnose(
        self, port: str, baud: int, online_vin_lookup: bool, online_dtc_lookup: bool
    ) -> Session:
        self.calls.append(("diagnose", port, baud))
        self.online_flags.append(online_vin_lookup)
        self.online_code_flags.append(online_dtc_lookup)
        if self.diagnose_error is not None:
            raise self.diagnose_error
        return self.session

    def clear(self, port: str, baud: int) -> ClearResult:
        self.calls.append(("clear", port, baud))
        if self.clear_error is not None:
            raise self.clear_error
        before = self.session.scan
        result = self.clear_result
        if result is None:
            after = ScanResult(before.adapter, before.protocol, 12.3)
            result = ClearResult(Path("/tmp/backup.json"), before, after)
        self.session = after_clear(self.session, result.after)
        return result

    def list_ports(self) -> list[PortInfo]:
        if self.ports is None:
            raise NotImplementedError
        return self.ports

    def catalog_available(self) -> bool:
        return self.catalog

    def export_pdf(self, session: Session, path: Path) -> None:
        self.exports.append(("pdf", path))
        export_pdf(session, path)

    def export_csv(self, session: Session, path: Path) -> None:
        self.exports.append(("csv", path))
        export_csv(session, path)

    def as_backend(self) -> Backend:
        return Backend(
            diagnose=self.diagnose,
            clear=self.clear,
            list_ports=self.list_ports,
            catalog_available=self.catalog_available,
            export_pdf=self.export_pdf,
            export_csv=self.export_csv,
            set_tracing=self.tracing.append,
        )


@pytest.fixture(autouse=True)
def _isolated_user_dirs(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Sitzungen (XDG_DATA_HOME) und Einstellungen (QSettings) nie im echten Home ablegen."""
    data = tmp_path_factory.mktemp("data")
    config = tmp_path_factory.mktemp("config")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("XDG_DATA_HOME", str(data))
        try:
            from PySide6.QtCore import QSettings
        except ImportError:
            yield
            return
        for fmt in (QSettings.Format.NativeFormat, QSettings.Format.IniFormat):
            QSettings.setPath(fmt, QSettings.Scope.UserScope, str(config))
        yield


@pytest.fixture
def fake_backend() -> FakeBackend:
    return FakeBackend()
