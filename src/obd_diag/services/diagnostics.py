"""Diagnose-Abläufe: Fehlercodes lesen und mit Klartext anreichern."""

import dataclasses
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from obd_diag.data.dtc_catalog import DtcInfo
from obd_diag.protocol.elm327 import Elm327, ElmError, UnknownCommandError
from obd_diag.protocol.obd import read_dtcs
from obd_diag.protocol.pids import PIDS, read_values
from obd_diag.services.dtc_online import OnlineExplanation, lookup_online

LOW_VOLTAGE = 11.8  # Volt; darunter warnen (Motor aus, Zündung an)
# Steuergerätespannung (PID 42) nur in diesem Bereich als Bordspannung übernehmen
_ECU_VOLTAGE_RANGE = (5.0, 30.0)


def check_low_voltage(elm: Elm327, voltage: float | None) -> float | None:
    """Bestätigt eine niedrige Adapter-Messung (``ATRV``) mit der Steuergerätespannung.

    Der Adapter misst an Pin 16 der Diagnosebuchse, oft hinter einer Schutzdiode: Am
    Mercedes A 180 d (W177) zeigte er 11,2 V, das Motorsteuergerät meldete 12,0 V.
    Liegt ``voltage`` unter ``LOW_VOLTAGE``, wird daher PID 42 gelesen und, wenn
    plausibel, statt ``voltage`` geliefert (auch wenn er niedriger ist). Sonst, auch
    bei ``None``, bleibt es bei ``voltage``. Nur nach ausgehandeltem Protokoll
    aufrufen (sonst liefe die Protokollsuche mit der kurzen Wartezeit).
    """
    if voltage is None or voltage >= LOW_VOLTAGE:
        return voltage
    try:
        ecu = read_values(elm, [PIDS["control_voltage"]])["control_voltage"]
    except ElmError:
        return voltage
    low, high = _ECU_VOLTAGE_RANGE
    if ecu is None or not low <= ecu <= high:
        return voltage
    return ecu


class DtcKind(StrEnum):
    STORED = "stored"  # Mode 03
    PENDING = "pending"  # Mode 07
    PERMANENT = "permanent"  # Mode 0A


@dataclass(frozen=True)
class DiagnosticCode:
    code: str
    kind: DtcKind
    info: DtcInfo | None = None
    online: OnlineExplanation | None = None  # nur nach Opt-in, nur ohne Katalogtext


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
    voltage = check_low_voltage(elm, voltage)
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


def add_online_explanations(
    result: ScanResult,
    manufacturer: str | None,
    lookup: Callable[[list[str], str | None], dict[str, OnlineExplanation]] | None = None,
) -> ScanResult:
    """Ergänzt Codes ohne Katalogtext um eine Online-Erklärung (nur nach Opt-in).

    Codes mit Katalogtext werden nicht nachgeschlagen; ohne solche Codes geht nichts
    ins Netz. ``manufacturer`` kommt aus der FIN, sonst ``None``.
    """
    missing = sorted({c.code for c in result.codes if c.info is None})
    if not missing:
        return result
    found = (lookup or lookup_online)(missing, manufacturer)
    return dataclasses.replace(
        result,
        codes=[
            dataclasses.replace(c, online=found[c.code])
            if c.info is None and c.code in found
            else c
            for c in result.codes
        ],
    )


def scan_to_dict(result: ScanResult) -> dict[str, Any]:
    """JSON-taugliche Form eines Scans (wie ``obd-diag scan --json``)."""
    data = dataclasses.asdict(result)
    data["low_voltage"] = result.low_voltage
    return data
