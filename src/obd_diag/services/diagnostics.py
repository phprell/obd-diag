"""Diagnose-Abläufe: Fehlercodes lesen und mit Klartext anreichern."""

from dataclasses import dataclass, field
from enum import StrEnum

from obd_diag.data.dtc_catalog import DtcCatalog, DtcInfo
from obd_diag.protocol.elm327 import Elm327

LOW_VOLTAGE = 11.8  # Volt; darunter warnen (Motor aus, Zündung an)


class DtcKind(StrEnum):
    STORED = "stored"  # Mode 03
    PENDING = "pending"  # Mode 07
    PERMANENT = "permanent"  # Mode 0A


@dataclass(frozen=True)
class DiagnosticCode:
    code: str
    kind: DtcKind
    info: DtcInfo | None = None


@dataclass
class ScanResult:
    adapter: str
    protocol: str
    voltage: float | None
    codes: list[DiagnosticCode] = field(default_factory=list)

    @property
    def low_voltage(self) -> bool:
        return self.voltage is not None and self.voltage < LOW_VOLTAGE


def scan(elm: Elm327, catalog: DtcCatalog | None, lang: str = "de") -> ScanResult:
    raise NotImplementedError
