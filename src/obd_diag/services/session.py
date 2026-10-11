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
from obd_diag.protocol.obd import FreezeFrame, read_freeze_frame
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
from obd_diag.services.vehicle import VinInfo, decode_vin, lookup_vpic, read_vin

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Session:
    created: datetime
    scan: ScanResult
    readiness: ReadinessStatus | None = None
    freeze_frame: FreezeFrame | None = None
    vehicle: VinInfo | None = None


def run_diagnosis(
    elm: Elm327,
    catalog: DtcLookup | None,
    lang: str | None = None,
    *,
    online_vin_lookup: bool = False,
    online_dtc_lookup: bool = False,
) -> Session:
    """Scan, Readiness, Freeze Frame und FIN in einem Durchgang; nur lesend.

    Fehlt eine einzelne Angabe (Steuergerät antwortet nicht), bleibt sie ``None``;
    nur ein fehlgeschlagener Scan bricht ab.

    Der Scan setzt den Adapter zurück (``ATZ``) und handelt das Protokoll aus; danach
    folgen Readiness (``0101``), Freeze Frame (``02xx00``) und FIN (``0902``). Ein
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
    vehicle = None if vin is None else decode_vin(vin, protocol=result.protocol)
    if vehicle is not None and online_vin_lookup and vehicle.valid:
        vehicle = dataclasses.replace(vehicle, online=lookup_vpic(vehicle.vin))
    if online_dtc_lookup:
        manufacturer = vehicle.manufacturer if vehicle is not None and vehicle.valid else None
        result = add_online_explanations(result, manufacturer)
    return Session(
        created=created, scan=result, readiness=readiness, freeze_frame=freeze, vehicle=vehicle
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
        readiness, freeze, vehicle = (
            data.get(key) for key in ("readiness", "freeze_frame", "vehicle")
        )
        return Session(
            created=created,
            scan=_scan(data["scan"]),
            readiness=None if readiness is None else _readiness(readiness),
            freeze_frame=None if freeze is None else _freeze_frame(freeze),
            vehicle=None if vehicle is None else _vehicle(vehicle),
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
