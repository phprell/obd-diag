"""Diagnosesitzung: alles, was ein Durchgang ausliest, plus Speichern und Export."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from obd_diag.protocol.elm327 import Elm327
from obd_diag.protocol.obd import FreezeFrame
from obd_diag.services.diagnostics import DtcLookup, ScanResult
from obd_diag.services.readiness import ReadinessStatus
from obd_diag.services.vehicle import VinInfo


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
    lang: str = "de",
    *,
    online_vin_lookup: bool = False,
) -> Session:
    """Scan, Readiness, Freeze Frame und FIN in einem Durchgang; nur lesend.

    Fehlt eine einzelne Angabe (Steuergerät antwortet nicht), bleibt sie ``None``;
    nur ein fehlgeschlagener Scan bricht ab.
    """
    raise NotImplementedError


def default_session_dir() -> Path:
    """``$XDG_DATA_HOME/obd-diag/sessions`` (Standard: ~/.local/share/obd-diag/sessions)."""
    raise NotImplementedError


def session_to_dict(session: Session) -> dict[str, Any]:
    raise NotImplementedError


def session_from_dict(data: dict[str, Any]) -> Session:
    raise NotImplementedError


def save_session(session: Session, directory: Path | None = None) -> Path:
    """Speichert als JSON mit Zeitstempel im Namen; überschreibt nie."""
    raise NotImplementedError


def load_session(path: Path) -> Session:
    raise NotImplementedError
