"""Gemeinsames für die GUI-Tests: Qt ohne Bildschirm, Fake-Backend, Beispieldaten."""

import os

# Vor dem Anlegen der QApplication setzen; überschreibt bewusst eine Desktop-Sitzung
# (z. B. QT_QPA_PLATFORM=wayland), damit Tests nie Fenster öffnen.
os.environ["QT_QPA_PLATFORM"] = "offscreen"
# Software-Rendering: kein OpenGL/EGL nötig (CI-Runner ohne GPU)
os.environ.setdefault("QT_QUICK_BACKEND", "software")

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from obd_diag.data.dtc_catalog import Cause, DtcInfo
from obd_diag.services.clear import ClearResult
from obd_diag.services.diagnostics import DiagnosticCode, DtcKind, ScanResult
from obd_diag.transport.discovery import PortInfo
from obd_diag.ui.backend import Backend

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


class FakeBackend:
    """Backend ohne seriellen Port; merkt sich die Aufrufe."""

    def __init__(
        self,
        scan_result: ScanResult = SCAN,
        *,
        ports: list[PortInfo] | None = None,
        catalog: bool = True,
    ) -> None:
        self.scan_result = scan_result
        self.ports = ports
        self.catalog = catalog
        self.scan_error: Exception | None = None
        self.clear_error: Exception | None = None
        self.clear_result: ClearResult | None = None
        self.calls: list[tuple[str, str, int]] = []

    def scan(self, port: str, baud: int) -> ScanResult:
        self.calls.append(("scan", port, baud))
        if self.scan_error is not None:
            raise self.scan_error
        return self.scan_result

    def clear(self, port: str, baud: int) -> ClearResult:
        self.calls.append(("clear", port, baud))
        if self.clear_error is not None:
            raise self.clear_error
        if self.clear_result is not None:
            return self.clear_result
        after = ScanResult(self.scan_result.adapter, self.scan_result.protocol, 12.3)
        return ClearResult(Path("/tmp/backup.json"), self.scan_result, after)

    def list_ports(self) -> list[PortInfo]:
        if self.ports is None:
            raise NotImplementedError
        return self.ports

    def catalog_available(self) -> bool:
        return self.catalog

    def as_backend(self) -> Backend:
        return Backend(
            scan=self.scan,
            clear=self.clear,
            list_ports=self.list_ports,
            catalog_available=self.catalog_available,
        )


@pytest.fixture
def fake_backend() -> FakeBackend:
    return FakeBackend()
