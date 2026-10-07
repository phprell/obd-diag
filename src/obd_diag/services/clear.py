"""Fehlercodes löschen (Mode 04): die einzige schreibende Aktion des Tools.

Ablauf: Vorbedingungen prüfen (Zündung an, Motor aus), Codes und Freeze Frame
sichern, erst dann löschen und zur Kontrolle neu einlesen.
"""

from dataclasses import dataclass
from pathlib import Path

from obd_diag.protocol.elm327 import Elm327
from obd_diag.services.diagnostics import DtcLookup, ScanResult


class ClearRefused(Exception):
    """Eine Vorbedingung ist nicht erfüllt; die Meldung ist für Nutzer gedacht."""


@dataclass(frozen=True)
class ClearResult:
    backup_path: Path  # JSON mit Codes und Freeze Frame vor dem Löschen
    before: ScanResult
    after: ScanResult  # Kontroll-Scan nach dem Löschen


def default_backup_dir() -> Path:
    """``$XDG_DATA_HOME/obd-diag/backups`` (Standard: ~/.local/share/obd-diag/backups)."""
    raise NotImplementedError


def check_preconditions(elm: Elm327) -> None:
    """Wirft ``ClearRefused``, wenn nicht gelöscht werden darf (z. B. Motor läuft)."""
    raise NotImplementedError


def clear_codes(
    elm: Elm327,
    catalog: DtcLookup | None,
    *,
    backup_dir: Path | None = None,
    lang: str = "de",
) -> ClearResult:
    raise NotImplementedError
