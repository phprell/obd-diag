"""Sitzungsvergleich: was sich zwischen zwei gespeicherten Diagnosen geändert hat.

Arbeitet nur auf gespeicherten Daten (``Session``), spricht also nie mit dem Fahrzeug.
Die ältere Sitzung ist „vorher“, die jüngere „nachher“ (nach ``Session.created``).

Jeder Teil der Sitzung hat eine eigene Vergleichsfunktion in ``SECTIONS``
(Schlüssel, Überschrift, Funktion). Kommen neue Daten in die Sitzung (z. B. Mode 06
oder weitere PIDs), bekommen sie dort eine eigene Funktion; die Ausgabe in
Kommandozeile und Oberfläche zeigt jeden Abschnitt ohne weitere Änderung an.

Texte entstehen beim Vergleich in der eingestellten Sprache (``tr``); nach einem
Sprachwechsel einfach neu vergleichen. Fehlercode-Titel bleiben in der Sprache des Scans.
"""

import dataclasses
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from obd_diag.i18n import N_, decimal, tr
from obd_diag.protocol.obd import FreezeFrame
from obd_diag.protocol.pids import PIDS
from obd_diag.services.diagnostics import DiagnosticCode, DtcKind, ScanResult
from obd_diag.services.readiness import ALL_COMPLETE_LABEL, MonitorState, ReadinessStatus
from obd_diag.services.session import Session


class ChangeKind(StrEnum):
    ADDED = "added"  # nur nachher vorhanden
    REMOVED = "removed"  # nur vorher vorhanden
    CHANGED = "changed"  # in beiden, aber verschieden
    UNCHANGED = "unchanged"  # in beiden gleich (nur wo es für sich etwas aussagt)


class Trend(StrEnum):
    BETTER = "better"
    WORSE = "worse"
    NEUTRAL = "neutral"


@dataclass(frozen=True)
class Change:
    kind: ChangeKind
    subject: str  # z. B. "P0420 Katalysator ..." oder "Kontrollleuchte (MIL)"
    before: str | None  # Wert vorher als Text; None = nicht vorhanden
    after: str | None
    trend: Trend = Trend.NEUTRAL


@dataclass(frozen=True)
class Section:
    key: str  # stabil, z. B. "codes"
    title: str  # in der eingestellten Sprache
    changes: tuple[Change, ...]

    @property
    def differences(self) -> tuple[Change, ...]:
        """Alles außer ``UNCHANGED``."""
        return tuple(c for c in self.changes if c.kind is not ChangeKind.UNCHANGED)


@dataclass(frozen=True)
class SessionComparison:
    before: Session
    after: Session
    sections: tuple[Section, ...]
    swapped: bool  # True: die Sitzungen wurden in zeitliche Reihenfolge gebracht

    @property
    def same_vehicle(self) -> bool | None:
        """Gleiche FIN? ``None``, wenn eine der Sitzungen keine FIN hat."""
        a, b = self.before.vehicle, self.after.vehicle
        if a is None or b is None:
            return None
        return a.vin == b.vin

    @property
    def has_differences(self) -> bool:
        return any(s.differences for s in self.sections)


Comparer = Callable[[Session, Session], list[Change]]

# Bezeichnungen deutsch, bei der Ausgabe mit ``tr`` übersetzt
_KIND_LABELS = {
    DtcKind.STORED: N_("gespeichert"),
    DtcKind.PENDING: N_("ausstehend"),
    DtcKind.PERMANENT: N_("permanent"),
}
_STATE_LABELS = {
    MonitorState.COMPLETE: N_("abgeschlossen"),
    MonitorState.INCOMPLETE: N_("nicht abgeschlossen"),
    MonitorState.NOT_SUPPORTED: N_("nicht unterstützt"),
}
# Schlüssel aus FreezeFrame.values: Bezeichnung, Einheit, Nachkommastellen (wie im PDF)
_FREEZE_LABELS: dict[str, tuple[str, str, int]] = {
    "engine_load_pct": (N_("Motorlast"), "%", 1),
    "coolant_temp_c": (N_("Kühlmitteltemperatur"), "°C", 0),
    "rpm": (N_("Drehzahl"), N_("1/min"), 0),
    "speed_kmh": (N_("Geschwindigkeit"), "km/h", 0),
}
VOLTAGE_TOLERANCE = 0.1  # Volt; kleinere Unterschiede sind Messrauschen
DIFFERENT_VEHICLE_WARNING = N_(
    "Die Sitzungen stammen von verschiedenen Fahrzeugen (FIN verschieden)."
)


def _kind(before: object, after: object) -> ChangeKind:
    """Art der Änderung zwischen zwei verschiedenen Werten (``None`` = nicht vorhanden)."""
    if before is None:
        return ChangeKind.ADDED
    return ChangeKind.REMOVED if after is None else ChangeKind.CHANGED


def _yes_no(value: bool) -> str:
    return tr("ja") if value else tr("nein")


def _on_off(value: bool) -> str:
    return tr("an") if value else tr("aus")


# --- Abschnitte ------------------------------------------------------------------


def _compare_vehicle(before: Session, after: Session) -> list[Change]:
    a, b = before.vehicle, after.vehicle
    if a is None and b is None:
        return []
    vin_a = a.vin if a is not None else None
    vin_b = b.vin if b is not None else None
    if vin_a == vin_b:
        return []
    return [Change(_kind(vin_a, vin_b), tr("FIN"), vin_a, vin_b)]


def _code_title(code: str, *scans: ScanResult) -> str:
    for scan in scans:
        for c in scan.codes:
            if c.code == code and c.info is not None:
                return f"{code}  {c.info.title}"
    return code


def _kinds_text(kinds: set[DtcKind]) -> str:
    return ", ".join(tr(_KIND_LABELS[k]) for k in DtcKind if k in kinds)


def _code_kinds(codes: list[DiagnosticCode]) -> dict[str, set[DtcKind]]:
    found: dict[str, set[DtcKind]] = {}
    for c in codes:
        found.setdefault(c.code, set()).add(c.kind)
    return found


def _compare_codes(before: Session, after: Session) -> list[Change]:
    """Je Code: neu, verschwunden, Art geändert oder weiterhin vorhanden.

    Schlechter heißt: nachher gespeichert (bestätigt), vorher nicht; besser umgekehrt.
    Ein Wechsel nur zwischen ausstehend und permanent ist neutral.
    """
    old, new = _code_kinds(before.scan.codes), _code_kinds(after.scan.codes)
    changes: list[Change] = []
    for code in sorted(old.keys() | new.keys()):
        a, b = old.get(code, set()), new.get(code, set())
        subject = _code_title(code, after.scan, before.scan)
        if DtcKind.STORED in b and DtcKind.STORED not in a:
            trend = Trend.WORSE
        elif DtcKind.STORED in a and DtcKind.STORED not in b:
            trend = Trend.BETTER
        else:
            trend = Trend.WORSE if not a else Trend.BETTER if not b else Trend.NEUTRAL
        if not a:
            changes.append(Change(ChangeKind.ADDED, subject, None, _kinds_text(b), trend))
        elif not b:
            changes.append(Change(ChangeKind.REMOVED, subject, _kinds_text(a), None, trend))
        elif a != b:
            changes.append(
                Change(ChangeKind.CHANGED, subject, _kinds_text(a), _kinds_text(b), trend)
            )
        else:
            text = _kinds_text(a)
            changes.append(Change(ChangeKind.UNCHANGED, subject, text, text))
    # Neue und geänderte zuerst, verschwundene danach, unveränderte zuletzt
    order = [ChangeKind.ADDED, ChangeKind.CHANGED, ChangeKind.REMOVED, ChangeKind.UNCHANGED]
    return sorted(changes, key=lambda c: order.index(c.kind))


def _compare_readiness(before: Session, after: Session) -> list[Change]:
    a, b = before.readiness, after.readiness
    if a is None and b is None:
        return []
    if a is None or b is None:
        present = tr("verfügbar")
        return [
            Change(
                _kind(a, b),
                tr("Readiness"),
                None if a is None else present,
                None if b is None else present,
            )
        ]
    return _readiness_changes(a, b)


def _readiness_changes(a: ReadinessStatus, b: ReadinessStatus) -> list[Change]:
    changes: list[Change] = []
    if a.mil_on != b.mil_on:
        trend = Trend.WORSE if b.mil_on else Trend.BETTER
        changes.append(
            Change(
                ChangeKind.CHANGED,
                tr("Kontrollleuchte (MIL)"),
                _on_off(a.mil_on),
                _on_off(b.mil_on),
                trend,
            )
        )
    if a.dtc_count != b.dtc_count:
        trend = Trend.WORSE if b.dtc_count > a.dtc_count else Trend.BETTER
        changes.append(
            Change(
                ChangeKind.CHANGED,
                tr("Gemeldete Fehlercodes"),
                str(a.dtc_count),
                str(b.dtc_count),
                trend,
            )
        )
    if a.compression_ignition != b.compression_ignition:
        engines = (tr("Otto"), tr("Diesel"))
        changes.append(
            Change(
                ChangeKind.CHANGED,
                tr("Motorart"),
                engines[a.compression_ignition],
                engines[b.compression_ignition],
            )
        )
    old = {m.key: m for m in a.monitors}
    new = {m.key: m for m in b.monitors}
    for key in [*old, *(k for k in new if k not in old)]:
        m_a, m_b = old.get(key), new.get(key)
        name = tr((m_b or m_a).name)  # type: ignore[union-attr]
        state_a = None if m_a is None else m_a.state
        state_b = None if m_b is None else m_b.state
        if state_a == state_b:
            continue
        if state_a is MonitorState.COMPLETE and state_b is MonitorState.INCOMPLETE:
            trend = Trend.WORSE
        elif state_a is MonitorState.INCOMPLETE and state_b is MonitorState.COMPLETE:
            trend = Trend.BETTER
        else:
            trend = Trend.NEUTRAL
        changes.append(
            Change(
                _kind(state_a, state_b),
                name,
                None if state_a is None else tr(_STATE_LABELS[state_a]),
                None if state_b is None else tr(_STATE_LABELS[state_b]),
                trend,
            )
        )
    if a.all_complete != b.all_complete:
        changes.append(
            Change(
                ChangeKind.CHANGED,
                tr(ALL_COMPLETE_LABEL),
                _yes_no(a.all_complete),
                _yes_no(b.all_complete),
                Trend.BETTER if b.all_complete else Trend.WORSE,
            )
        )
    return changes


def _freeze_value(key: str, value: float) -> str:
    _label, unit, decimals = _FREEZE_LABELS.get(key, (key, "", 2))
    shown = decimal(value, decimals)
    return f"{shown} {tr(unit)}" if unit else shown


def _compare_freeze_frame(before: Session, after: Session) -> list[Change]:
    a = before.freeze_frame or FreezeFrame()
    b = after.freeze_frame or FreezeFrame()
    changes: list[Change] = []
    if a.dtc != b.dtc:
        changes.append(Change(_kind(a.dtc, b.dtc), tr("Auslösender Fehlercode"), a.dtc, b.dtc))
    keys = [*a.values, *(k for k in b.values if k not in a.values)]
    for key in keys:
        v_a, v_b = a.values.get(key), b.values.get(key)
        if v_a == v_b:
            continue
        label = _FREEZE_LABELS[key][0] if key in _FREEZE_LABELS else key
        changes.append(
            Change(
                _kind(v_a, v_b),
                tr(label),
                None if v_a is None else _freeze_value(key, v_a),
                None if v_b is None else _freeze_value(key, v_b),
            )
        )
    return changes


# Feld in ``ClearCounters``, Schlüssel in ``PIDS`` (Bezeichnung, Einheit), zählt seit
# dem Löschen (True) oder mit Warnleuchte (False)
_COUNTERS: tuple[tuple[str, str, bool], ...] = (
    ("warmups", "warmups_since_clear", True),
    ("distance_km", "distance_since_clear", True),
    ("time_min", "time_since_clear", True),
    ("mil_distance_km", "distance_with_mil", False),
    ("mil_time_min", "time_with_mil", False),
)


def _compare_clear_counters(before: Session, after: Session) -> list[Change]:
    """Zähler des Gebrauchtwagen-Checks (PID 30, 31, 4E, 21, 4D).

    Fällt ein Zähler seit dem Löschen, wurde dazwischen gelöscht (oder die Batterie
    abgeklemmt): schlechter, weil die Vorgeschichte damit weg ist. Mehr Strecke oder
    Zeit mit Warnleuchte ist ebenfalls schlechter.
    """
    a, b = before.clear_counters, after.clear_counters
    if a is None and b is None:
        return []
    # asdict statt getattr: der Architekturtest verbietet berechnete Attributnamen
    old = {} if a is None else dataclasses.asdict(a)
    new = {} if b is None else dataclasses.asdict(b)
    changes: list[Change] = []
    for field, key, since_clear in _COUNTERS:
        v_a, v_b = old.get(field), new.get(field)
        if v_a == v_b:
            continue
        spec = PIDS[key]
        trend = Trend.NEUTRAL
        if v_a is not None and v_b is not None:
            if since_clear and v_b < v_a:
                trend = Trend.WORSE
            elif not since_clear:
                trend = Trend.WORSE if v_b > v_a else Trend.BETTER

        def text(value: int | None, unit: str = spec.unit) -> str | None:
            if value is None:
                return None
            return f"{value} {tr(unit)}" if unit else str(value)

        changes.append(Change(_kind(v_a, v_b), spec.name, text(v_a), text(v_b), trend))
    return changes


def _voltage_text(voltage: float | None) -> str:
    return tr("unbekannt") if voltage is None else f"{decimal(voltage)} V"


def _compare_connection(before: Session, after: Session) -> list[Change]:
    a, b = before.scan, after.scan
    changes: list[Change] = []
    if a.adapter != b.adapter:
        changes.append(Change(ChangeKind.CHANGED, tr("Adapter"), a.adapter, b.adapter))
    if a.protocol != b.protocol:
        changes.append(Change(ChangeKind.CHANGED, tr("Protokoll"), a.protocol, b.protocol))
    voltage_differs = (a.voltage is None) != (b.voltage is None) or (
        a.voltage is not None
        and b.voltage is not None
        and abs(a.voltage - b.voltage) >= VOLTAGE_TOLERANCE
    )
    if voltage_differs:
        if b.low_voltage and not a.low_voltage:
            trend = Trend.WORSE
        elif a.low_voltage and not b.low_voltage:
            trend = Trend.BETTER
        else:
            trend = Trend.NEUTRAL
        changes.append(
            Change(
                ChangeKind.CHANGED,
                tr("Bordspannung"),
                _voltage_text(a.voltage),
                _voltage_text(b.voltage),
                trend,
            )
        )
    return changes


def change_text(change: Change) -> str:
    """Wert einer Änderung als Text, z. B. „ausstehend → gespeichert“ oder „neu: gespeichert“."""
    if change.kind is ChangeKind.ADDED:
        return tr("neu: {value}").format(value=change.after)
    if change.kind is ChangeKind.REMOVED:
        return tr("entfällt (vorher: {value})").format(value=change.before)
    if change.kind is ChangeKind.UNCHANGED:
        return tr("weiterhin {value}").format(value=change.after)
    return f"{change.before} → {change.after}"


# Reihenfolge der Abschnitte in der Ausgabe; neue Sitzungsdaten hier anhängen
SECTIONS: list[tuple[str, str, Comparer]] = [
    ("vehicle", N_("Fahrzeug"), _compare_vehicle),
    ("codes", N_("Fehlercodes"), _compare_codes),
    ("readiness", N_("Readiness"), _compare_readiness),
    ("freeze_frame", N_("Freeze Frame"), _compare_freeze_frame),
    ("clear_counters", N_("Zähler seit dem Löschen"), _compare_clear_counters),
    ("connection", N_("Verbindung"), _compare_connection),
]


def compare_sessions(first: Session, second: Session) -> SessionComparison:
    """Vergleicht zwei Sitzungen; die ältere gilt als „vorher“ (Reihenfolge egal)."""
    try:
        swapped = second.created < first.created
    except TypeError:  # Zeitstempel mit und ohne Zeitzone: Reihenfolge wie übergeben
        swapped = False
    before, after = (second, first) if swapped else (first, second)
    sections = tuple(
        Section(key, tr(title), tuple(compare(before, after))) for key, title, compare in SECTIONS
    )
    return SessionComparison(before=before, after=after, sections=sections, swapped=swapped)


def comparison_to_dict(comparison: SessionComparison) -> dict[str, Any]:
    """JSON-taugliche Form (``obd-diag compare --json``); Texte in der eingestellten Sprache."""
    return {
        "before": comparison.before.created.isoformat(),
        "after": comparison.after.created.isoformat(),
        "same_vehicle": comparison.same_vehicle,
        "has_differences": comparison.has_differences,
        "sections": [
            {
                "key": s.key,
                "title": s.title,
                "changes": [
                    {
                        "kind": c.kind.value,
                        "subject": c.subject,
                        "before": c.before,
                        "after": c.after,
                        "trend": c.trend.value,
                    }
                    for c in s.changes
                ],
            }
            for s in comparison.sections
        ],
    }
