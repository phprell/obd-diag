"""Diagnosesitzung: alles, was ein Durchgang ausliest, plus Speichern und Export."""

import dataclasses
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from obd_diag import __version__
from obd_diag.data.dtc_catalog import Cause, DtcInfo
from obd_diag.i18n import tr
from obd_diag.protocol.elm327 import Elm327, ElmError
from obd_diag.protocol.monitor_tests import TestResult, read_test_results
from obd_diag.protocol.obd import FreezeFrame, read_freeze_frame
from obd_diag.protocol.vehicle_info import (
    EcuInfo,
    InUsePerformance,
    PerformanceCounter,
    read_ecu_info,
)
from obd_diag.services.diagnostics import (
    DiagnosticCode,
    DtcKind,
    DtcLookup,
    ScanResult,
    add_online_explanations,
    scan,
    scan_to_dict,
)
from obd_diag.services.dtc_online import OnlineExplanation
from obd_diag.services.readiness import Monitor, MonitorState, ReadinessStatus, read_readiness
from obd_diag.services.storage import data_dir, write_new_json
from obd_diag.services.used_car import ClearCounters, read_clear_counters
from obd_diag.services.vehicle import VinInfo, decode_vin, lookup_vpic, read_vin

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Session:
    created: datetime
    scan: ScanResult
    readiness: ReadinessStatus | None = None
    freeze_frame: FreezeFrame | None = None
    vehicle: VinInfo | None = None
    # Gebrauchtwagen-Check; None in Sitzungen älterer Versionen
    clear_counters: ClearCounters | None = None
    # Mode 09 je Steuergerät und Mode 06; ``None``: nicht unterstützt oder nicht lesbar
    ecus: tuple[EcuInfo, ...] | None = None
    tests: tuple[TestResult, ...] | None = None


def run_diagnosis(
    elm: Elm327,
    catalog: DtcLookup | None,
    lang: str | None = None,
    *,
    online_vin_lookup: bool = False,
    online_dtc_lookup: bool = False,
) -> Session:
    """Scan, Readiness, Freeze Frame, FIN, Zähler seit dem Löschen, Steuergeräte-Angaben
    (Mode 09) und Testergebnisse (Mode 06) in einem Durchgang; nur lesend.

    Fehlt eine einzelne Angabe (Steuergerät antwortet nicht), bleibt sie ``None``;
    nur ein fehlgeschlagener Scan bricht ab.

    Der Scan setzt den Adapter zurück (``ATZ``) und handelt das Protokoll aus; danach
    folgen Readiness (``0101``), Freeze Frame (``02xx00``), FIN (``0902``), die Zähler
    für den Gebrauchtwagen-Check (``0130``, ``0131``, ``014E``, ``0121``, ``014D``), Mode 09
    (``0900`` und die gemeldeten InfoTypes) und Mode 06 (``0600`` … und die gemeldeten
    MIDs), die beiden letzten mit Headern. Ein
    ``ElmError`` in einem dieser Teile lässt nur diesen Teil leer; Verbindungsfehler
    (``TransportError``) brechen ab. Der Freeze Frame wird immer gelesen, bleibt aber
    ``None``, wenn er weder einen auslösenden Code noch Werte enthält (ohne
    gespeicherten Code ist er leer). Die FIN geht nur mit ``online_vin_lookup`` an
    NHTSA vPIC. Mit ``online_dtc_lookup`` bekommen Codes ohne Katalogtext eine
    Online-Erklärung (``services/dtc_online``; die FIN verlässt dabei nie den Rechner,
    nur der Hersteller entscheidet, welche Datei geladen wird).
    """
    created = datetime.now().astimezone()
    result = scan(elm, catalog, lang)
    readiness = _optional("Readiness", read_readiness, elm)
    freeze = _optional("Freeze Frame", read_freeze_frame, elm)
    if freeze is not None and freeze.dtc is None and not freeze.values:
        freeze = None
    vin = _optional("FIN", read_vin, elm)
    counters = _optional("PID 30/31/4E/21/4D", read_clear_counters, elm)
    vehicle = None if vin is None else decode_vin(vin, protocol=result.protocol)
    ecus = _optional("Mode 09", read_ecu_info, elm)
    tests = _optional("Mode 06", read_test_results, elm)
    if vehicle is not None and online_vin_lookup and vehicle.valid:
        vehicle = dataclasses.replace(vehicle, online=lookup_vpic(vehicle.vin))
    if online_dtc_lookup:
        manufacturer = vehicle.manufacturer if vehicle is not None and vehicle.valid else None
        result = add_online_explanations(result, manufacturer)
    return Session(
        created=created,
        scan=result,
        readiness=readiness,
        freeze_frame=freeze,
        vehicle=vehicle,
        clear_counters=counters,
        ecus=None if ecus is None else tuple(ecus),
        tests=None if tests is None else tuple(tests),
    )


def _optional[T](what: str, read: Callable[[Elm327], T], elm: Elm327) -> T | None:
    """``read(elm)`` oder ``None``, wenn der Adapter einen Fehler meldet."""
    try:
        return read(elm)
    except ElmError as e:
        log.warning("%s nicht lesbar: %s", what, e)
        return None


SESSION_FORMAT = "obd-diag-session"
SESSION_VERSION = 1


def default_session_dir() -> Path:
    """``$XDG_DATA_HOME/obd-diag/sessions`` (Standard: ~/.local/share/obd-diag/sessions)."""
    return data_dir() / "sessions"


def session_to_dict(session: Session) -> dict[str, Any]:
    """JSON-taugliche Form; ``session_from_dict`` kehrt sie um.

    Der Scan hat dieselbe Form wie bei ``obd-diag scan --json``.
    """
    readiness: dict[str, Any] | None = None
    if session.readiness is not None:
        readiness = dataclasses.asdict(session.readiness)
        # nur zur Information, wird beim Laden neu berechnet; "ready" ist der alte Name
        readiness["all_complete"] = session.readiness.all_complete
        readiness["ready"] = session.readiness.all_complete
    return {
        "format": SESSION_FORMAT,
        "version": SESSION_VERSION,
        "tool_version": __version__,
        "created": session.created.isoformat(),
        "scan": scan_to_dict(session.scan),
        "readiness": readiness,
        "freeze_frame": (
            None if session.freeze_frame is None else dataclasses.asdict(session.freeze_frame)
        ),
        "vehicle": None if session.vehicle is None else dataclasses.asdict(session.vehicle),
        "clear_counters": (
            None if session.clear_counters is None else dataclasses.asdict(session.clear_counters)
        ),
        "ecus": None if session.ecus is None else [_ecu_dict(e) for e in session.ecus],
        "tests": None if session.tests is None else [_test_dict(t) for t in session.tests],
    }


def _ecu_dict(ecu: EcuInfo) -> dict[str, Any]:
    data = dataclasses.asdict(ecu)
    if ecu.performance is not None:
        for counter, entry in zip(
            ecu.performance.counters, data["performance"]["counters"], strict=True
        ):
            entry["ratio"] = counter.ratio  # nur zur Information, beim Laden neu berechnet
    return data


def _test_dict(test: TestResult) -> dict[str, Any]:
    # Rohwerte sind maßgeblich; umgerechnete Werte und Urteil nur zur Information
    return {
        **dataclasses.asdict(test),
        "value": test.value,
        "min": test.minimum,
        "max": test.maximum,
        "unit": test.unit,
        "status": test.status.value,
    }


def _dtc_info(data: dict[str, Any]) -> DtcInfo:
    cost = data.get("cost_eur")
    return DtcInfo(
        code=data["code"],
        title=data["title"],
        description=data.get("description"),
        causes=tuple(Cause(c["label"], c["likelihood"]) for c in data.get("causes", ())),
        symptoms=tuple(data.get("symptoms", ())),
        mil=data.get("mil"),
        emissions_relevant=data.get("emissions_relevant"),
        repair_difficulty=data.get("repair_difficulty"),
        cost_eur=None if cost is None else (int(cost[0]), int(cost[1])),
    )


def _online(data: dict[str, Any] | None) -> OnlineExplanation | None:
    if data is None:  # fehlt auch in Sitzungen älterer Versionen
        return None
    return OnlineExplanation(
        text=str(data["text"]),
        source=str(data["source"]),
        url=str(data["url"]),
        manufacturer=data.get("manufacturer"),
    )


def _scan(data: dict[str, Any]) -> ScanResult:
    voltage = data.get("voltage")
    return ScanResult(
        adapter=data["adapter"],
        protocol=data["protocol"],
        voltage=None if voltage is None else float(voltage),
        codes=[
            DiagnosticCode(
                code=c["code"],
                kind=DtcKind(c["kind"]),
                info=None if c.get("info") is None else _dtc_info(c["info"]),
                online=_online(c.get("online")),
            )
            for c in data.get("codes", ())
        ],
    )


def _readiness(data: dict[str, Any]) -> ReadinessStatus:
    return ReadinessStatus(
        mil_on=bool(data["mil_on"]),
        dtc_count=int(data["dtc_count"]),
        compression_ignition=bool(data["compression_ignition"]),
        monitors=tuple(
            Monitor(key=m["key"], name=m["name"], state=MonitorState(m["state"]))
            for m in data["monitors"]
        ),
    )


def _freeze_frame(data: dict[str, Any]) -> FreezeFrame:
    return FreezeFrame(
        dtc=data.get("dtc"),
        raw={str(k): str(v) for k, v in data.get("raw", {}).items()},
        values={str(k): float(v) for k, v in data.get("values", {}).items()},
    )


def _vehicle(data: dict[str, Any]) -> VinInfo:
    year = data.get("model_year")
    return VinInfo(
        vin=data["vin"],
        valid=bool(data["valid"]),
        checksum_ok=data.get("checksum_ok"),
        wmi=data["wmi"],
        manufacturer=data.get("manufacturer"),
        country=data.get("country"),
        model_year=None if year is None else int(year),
        online={str(k): str(v) for k, v in data.get("online", {}).items()},
        # fehlt in Sitzungen älterer Versionen
        model_year_alternatives=tuple(int(y) for y in data.get("model_year_alternatives", ())),
    )


def _clear_counters(data: dict[str, Any]) -> ClearCounters:
    def number(key: str) -> int | None:
        value = data.get(key)
        return None if value is None else int(value)

    return ClearCounters(
        warmups=number("warmups"),
        distance_km=number("distance_km"),
        time_min=number("time_min"),
        mil_distance_km=number("mil_distance_km"),
        mil_time_min=number("mil_time_min"),
    )


def _ecu(data: dict[str, Any]) -> EcuInfo:
    performance = data.get("performance")
    return EcuInfo(
        ecu=str(data["ecu"]),
        name=data.get("name"),
        calibration_ids=tuple(str(c) for c in data.get("calibration_ids", ())),
        cvns=tuple(str(c) for c in data.get("cvns", ())),
        performance=None
        if performance is None
        else InUsePerformance(
            compression_ignition=bool(performance["compression_ignition"]),
            obd_conditions=int(performance["obd_conditions"]),
            ignition_cycles=int(performance["ignition_cycles"]),
            counters=tuple(
                PerformanceCounter(str(c["key"]), int(c["completed"]), int(c["conditions"]))
                for c in performance.get("counters", ())
            ),
        ),
        supported=tuple(int(t) for t in data.get("supported", ())),
    )


def _test(data: dict[str, Any]) -> TestResult:
    return TestResult(
        ecu=str(data["ecu"]),
        **{k: int(data[k]) for k in ("mid", "tid", "uasid", "value_raw", "min_raw", "max_raw")},
    )


def session_from_dict(data: dict[str, Any]) -> Session:
    """Gegenstück zu ``session_to_dict``; ``ValueError`` bei fremden oder kaputten Daten."""
    if not isinstance(data, dict) or data.get("format") != SESSION_FORMAT:
        raise ValueError(
            tr("Keine obd-diag-Diagnosesitzung (Kennung 'format' fehlt oder ist falsch).")
        )
    version = data.get("version")
    if version != SESSION_VERSION:
        if isinstance(version, int) and version > SESSION_VERSION:
            raise ValueError(
                tr(
                    "Sitzung hat Format-Version {version}, diese obd-diag-Version kennt nur "
                    "bis {known}. Bitte obd-diag aktualisieren."
                ).format(version=version, known=SESSION_VERSION)
            )
        raise ValueError(
            tr("Unbekannte Format-Version der Sitzung: {version}.").format(version=repr(version))
        )
    try:
        created = datetime.fromisoformat(data["created"])
        readiness, freeze, vehicle, counters = (
            data.get(key) for key in ("readiness", "freeze_frame", "vehicle", "clear_counters")
        )
        return Session(
            created=created,
            scan=_scan(data["scan"]),
            readiness=None if readiness is None else _readiness(readiness),
            freeze_frame=None if freeze is None else _freeze_frame(freeze),
            vehicle=None if vehicle is None else _vehicle(vehicle),
            # fehlen in Sitzungen älterer Versionen
            clear_counters=None if counters is None else _clear_counters(counters),
            ecus=None if data.get("ecus") is None else tuple(_ecu(e) for e in data["ecus"]),
            tests=None if data.get("tests") is None else tuple(_test(t) for t in data["tests"]),
        )
    except (KeyError, TypeError, ValueError, AttributeError, IndexError) as e:
        raise ValueError(
            tr("Diagnosesitzung ist unvollständig oder beschädigt ({error}).").format(error=repr(e))
        ) from e


def save_session(session: Session, directory: Path | None = None) -> Path:
    """Speichert als JSON mit Zeitstempel im Namen; überschreibt nie.

    Dateiname: ``session-YYYYmmdd-HHMMSS.json`` nach ``session.created``, bei
    Namensgleichheit mit ``-2``, ``-3`` usw.
    """
    target = directory if directory is not None else default_session_dir()
    stem = f"session-{session.created:%Y%m%d-%H%M%S}"
    return write_new_json(target, stem, session_to_dict(session))


def load_session(path: Path) -> Session:
    """Liest eine mit ``save_session`` gespeicherte Sitzung.

    ``ValueError`` (Meldung in der eingestellten Sprache), wenn die Datei kein gültiges
    JSON oder keine obd-diag-Sitzung ist; ``OSError``, wenn sie sich nicht lesen lässt.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError(
            tr("{path}: kein gültiges JSON ({error}).").format(path=path, error=e)
        ) from e
    try:
        return session_from_dict(data)
    except ValueError as e:
        raise ValueError(f"{path}: {e}") from e
