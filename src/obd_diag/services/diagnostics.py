"""Diagnose-Abläufe: Fehlercodes lesen und mit Klartext anreichern."""

import dataclasses
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from obd_diag.data.dtc_catalog import DtcInfo
from obd_diag.protocol.elm327 import Elm327, ElmError, UnknownCommandError
from obd_diag.protocol.obd import read_dtcs

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


_MODES = ((DtcKind.STORED, 0x03), (DtcKind.PENDING, 0x07), (DtcKind.PERMANENT, 0x0A))


class DtcLookup(Protocol):
    """Was ``scan`` vom Katalog braucht; erfüllt z. B. von ``DtcCatalog``."""

    def lookup(self, code: str, lang: str) -> DtcInfo | None: ...


def scan(elm: Elm327, catalog: DtcLookup | None, lang: str = "de") -> ScanResult:
    """Initialisiert den Adapter und liest gespeicherte, ausstehende und permanente Codes.

    Nur lesend: es wird nichts gelöscht (kein Mode 04).
    """
    adapter = elm.initialize()
    try:
        voltage: float | None = elm.voltage()
    except (ElmError, ValueError):
        voltage = None  # manche Adapter kennen ATRV nicht; kein Grund abzubrechen
    protocol = elm.protocol()
    result = ScanResult(adapter=adapter, protocol=protocol.name, voltage=voltage)
    infos: dict[str, DtcInfo | None] = {}
    for kind, mode in _MODES:
        try:
            found = read_dtcs(elm, mode, can=protocol.is_can)
        except UnknownCommandError:
            if kind is not DtcKind.PERMANENT:
                raise
            found = []  # ältere Steuergeräte kennen Mode 0A (permanente Codes) nicht
        for code in found:
            if code not in infos:
                infos[code] = catalog.lookup(code, lang) if catalog is not None else None
            result.codes.append(DiagnosticCode(code, kind, infos[code]))
    return result


def scan_to_dict(result: ScanResult) -> dict[str, Any]:
    """JSON-taugliche Form eines Scans (wie ``obd-diag scan --json``)."""
    data = dataclasses.asdict(result)
    data["low_voltage"] = result.low_voltage
    return data
