"""Fehlercodes löschen (Mode 04): die einzige schreibende Aktion des Tools.

Ablauf: Vorbedingungen prüfen (Zündung an, Motor aus), Codes und Freeze Frame
sichern, erst dann löschen und zur Kontrolle neu einlesen. Schlägt irgendein Schritt
vor dem Löschen fehl, wird Mode 04 nicht gesendet.
"""

import dataclasses
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from obd_diag import __version__
from obd_diag.protocol.elm327 import Elm327, ElmError, NoDataError
from obd_diag.protocol.obd import (
    FreezeFrame,
    NegativeResponseError,
    clear_dtcs,
    read_freeze_frame,
    read_rpm,
)
from obd_diag.services.diagnostics import (
    LOW_VOLTAGE,
    DiagnosticCode,
    DtcKind,
    DtcLookup,
    ScanResult,
    scan,
    scan_to_dict,
)
from obd_diag.services.storage import data_dir, write_new_json


class ClearRefused(Exception):
    """Eine Vorbedingung ist nicht erfüllt; die Meldung ist für Nutzer gedacht."""


@dataclass(frozen=True)
class ClearResult:
    backup_path: Path  # JSON mit Codes und Freeze Frame vor dem Löschen
    before: ScanResult
    after: ScanResult  # Kontroll-Scan nach dem Löschen


# Mode 04 löscht gespeicherte und ausstehende Codes; permanente (Mode 0A) löscht erst
# das Steuergerät selbst nach erfolgreichen Fahrzyklen.
CLEARABLE_KINDS = frozenset({DtcKind.STORED, DtcKind.PENDING})


def clearable_codes(result: ScanResult) -> list[DiagnosticCode]:
    """Die Codes aus ``result``, die Mode 04 löschen würde."""
    return [c for c in result.codes if c.kind in CLEARABLE_KINDS]


def default_backup_dir() -> Path:
    """``$XDG_DATA_HOME/obd-diag/backups`` (Standard: ~/.local/share/obd-diag/backups)."""
    return data_dir() / "backups"


def check_preconditions(elm: Elm327) -> None:
    """Wirft ``ClearRefused``, wenn nicht gelöscht werden darf (z. B. Motor läuft).

    Geprüft wird: das Steuergerät antwortet (Zündung an), die Bordspannung ist nicht
    zu niedrig und die Drehzahl ist 0 (Motor aus). Ist die Drehzahl nicht lesbar, wird
    vorsichtshalber ebenfalls abgelehnt. Sendet nur lesende Befehle.
    """
    try:
        answer = elm.query("0100")
    except ElmError as e:
        raise ClearRefused(f"Keine Verbindung zum Steuergerät ({e}). Ist die Zündung an?") from e
    if answer is None or not any(
        line.replace(" ", "").startswith("4100") for line in answer.splitlines()
    ):
        reason = "NO DATA" if answer is None else f"Antwort {answer!r}"
        raise ClearRefused(f"Keine Verbindung zum Steuergerät ({reason}). Ist die Zündung an?")

    try:
        voltage: float | None = elm.voltage()
    except (ElmError, ValueError):
        voltage = None  # manche Adapter kennen ATRV nicht; wie beim Scan kein Abbruch
    if voltage is not None and voltage < LOW_VOLTAGE:
        raise ClearRefused(
            f"Bordspannung zu niedrig ({voltage:.1f} V, mindestens {LOW_VOLTAGE:.1f} V). "
            "Batterie laden oder Ladegerät anschließen."
        )

    try:
        rpm = read_rpm(elm)
    except ElmError as e:
        raise ClearRefused(f"Drehzahl nicht lesbar ({e}). Motor aus, Zündung an?") from e
    if rpm is None:
        raise ClearRefused(
            "Drehzahl nicht lesbar, daher wird nicht gelöscht. Motor aus, Zündung an?"
        )
    if rpm > 0:
        raise ClearRefused(f"Motor läuft ({rpm:.0f} 1/min). Motor abstellen, Zündung an lassen.")


def _now() -> datetime:
    return datetime.now().astimezone()


def _write_backup(backup_dir: Path, data: dict[str, Any], now: datetime) -> Path:
    """Schreibt ``data`` atomar als neue Datei; vorhandene Sicherungen bleiben unberührt."""
    return write_new_json(backup_dir, f"dtc-backup-{now:%Y%m%d-%H%M%S}", data)


def _backup_data(before: ScanResult, freeze: FreezeFrame, now: datetime) -> dict[str, Any]:
    return {
        "created": now.isoformat(timespec="seconds"),
        "tool_version": __version__,
        "adapter": before.adapter,
        "protocol": before.protocol,
        "scan": scan_to_dict(before),
        "freeze_frame": dataclasses.asdict(freeze),
    }


def clear_codes(
    elm: Elm327,
    catalog: DtcLookup | None,
    *,
    backup_dir: Path | None = None,
    lang: str = "de",
) -> ClearResult:
    """Löscht die Fehlercodes nach Prüfung und Sicherung und liest danach neu ein.

    Reihenfolge: Scan, Vorbedingungen, Freeze Frame lesen, Sicherung schreiben, erst
    dann Mode 04, zum Schluss Kontroll-Scan. ``ClearRefused`` vor dem Löschen heißt:
    nichts wurde verändert. Lehnt das Steuergerät Mode 04 ab, bleibt die Sicherung
    liegen und ``ClearRefused`` nennt den Grund.
    """
    before = scan(elm, catalog, lang)
    if not clearable_codes(before):
        raise ClearRefused("Keine gespeicherten oder ausstehenden Fehlercodes, nichts zu löschen.")
    check_preconditions(elm)
    freeze = read_freeze_frame(elm)
    now = _now()
    target = backup_dir if backup_dir is not None else default_backup_dir()
    try:
        backup_path = _write_backup(target, _backup_data(before, freeze, now), now)
    except OSError as e:
        raise ClearRefused(f"Sicherung nach {target} fehlgeschlagen ({e}), nichts gelöscht.") from e

    try:
        clear_dtcs(elm)
    except NegativeResponseError as e:
        raise ClearRefused(
            f"Steuergerät lehnt das Löschen ab: {e}. Sicherung: {backup_path}"
        ) from e
    except NoDataError as e:
        raise ClearRefused(
            f"Steuergerät hat das Löschen nicht bestätigt (NO DATA). Sicherung: {backup_path}"
        ) from e
    except ElmError as e:
        raise ClearRefused(
            f"Löschen nicht bestätigt ({e}), Ergebnis mit 'obd-diag scan' prüfen. "
            f"Sicherung: {backup_path}"
        ) from e

    after = scan(elm, catalog, lang)
    return ClearResult(backup_path, before, after)
