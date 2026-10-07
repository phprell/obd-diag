"""Readiness, Freeze Frame und Fahrzeug einer Sitzung als einfache Werte für QML."""

from typing import Any

from PySide6.QtCore import (
    QAbstractListModel,
    QByteArray,
    QModelIndex,
    QPersistentModelIndex,
    Qt,
    Signal,
)

from obd_diag.export.report import FREEZE_LABELS, MONITOR_STATE_LABELS  # wie im PDF
from obd_diag.protocol.obd import FreezeFrame
from obd_diag.services.diagnostics import DiagnosticCode
from obd_diag.services.readiness import (
    ALL_COMPLETE_LABEL,
    AU_NOTE,
    Monitor,
    MonitorState,
    ReadinessStatus,
)
from obd_diag.services.vehicle import VPIC_FIELDS, VinInfo, checksum_text, model_year_text


def number_text(value: float, decimals: int = 0) -> str:
    """Deutsche Schreibweise: Tausenderpunkt, Dezimalkomma."""
    text = f"{value:,.{decimals}f}"
    return text.replace(",", "\0").replace(".", ",").replace("\0", ".")


# --- Readiness -----------------------------------------------------------------

_MONITOR_ROLE_NAMES = ("key", "name", "state", "stateLabel")
_MONITOR_ROLES = {Qt.ItemDataRole.UserRole + 1 + i: n for i, n in enumerate(_MONITOR_ROLE_NAMES)}


def monitor_entry(m: Monitor) -> dict[str, Any]:
    return {
        "key": m.key,
        "name": m.name,
        "state": m.state.value,
        "stateLabel": MONITOR_STATE_LABELS[m.state],
    }


class MonitorListModel(QAbstractListModel):
    """Die Readiness-Monitore in der Reihenfolge, die das Steuergerät meldet."""

    countChanged = Signal()

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._entries: list[dict[str, Any]] = []

    def set_monitors(self, monitors: tuple[Monitor, ...] | list[Monitor]) -> None:
        self.beginResetModel()
        self._entries = [monitor_entry(m) for m in monitors]
        self.endResetModel()
        self.countChanged.emit()

    def entry(self, row: int) -> dict[str, Any] | None:
        return self._entries[row] if 0 <= row < len(self._entries) else None

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex | None = None) -> int:
        if parent is not None and parent.isValid():
            return 0
        return len(self._entries)

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = 0) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self._entries):
            return None
        entry = self._entries[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return entry["name"]
        name = _MONITOR_ROLES.get(role)
        return entry[name] if name is not None else None

    def roleNames(self) -> dict[int, QByteArray]:
        return {role: QByteArray(name.encode()) for role, name in _MONITOR_ROLES.items()}


def readiness_entry(status: ReadinessStatus | None) -> dict[str, Any]:
    """Kopfangaben der Readiness; ``{"available": False}`` ohne Antwort."""
    if status is None:
        return {"available": False}
    incomplete = [m.name for m in status.monitors if m.state is MonitorState.INCOMPLETE]
    supported = [m for m in status.monitors if m.state is not MonitorState.NOT_SUPPORTED]
    complete = sum(m.state is MonitorState.COMPLETE for m in status.monitors)
    done = status.all_complete
    if done:
        summary = f"Alle {len(supported)} unterstützten Tests abgeschlossen."
    elif len(incomplete) == 1:
        summary = f"1 Test nicht abgeschlossen: {incomplete[0]}."
    else:
        summary = f"{len(incomplete)} Tests nicht abgeschlossen: {', '.join(incomplete)}."
    return {
        "available": True,
        "ready": done,  # alle unterstützten Tests abgeschlossen (kein AU-Urteil)
        "readyLabel": ALL_COMPLETE_LABEL if done else "Nicht alle Tests abgeschlossen",
        "auNote": AU_NOTE,
        "summary": summary,
        "milOn": status.mil_on,
        "milLabel": "an" if status.mil_on else "aus",
        "dtcCount": status.dtc_count,
        "engineLabel": "Diesel (Selbstzünder)" if status.compression_ignition else "Otto",
        "completeCount": complete,
        "incompleteCount": len(incomplete),
        "supportedCount": len(supported),
    }


# --- Freeze Frame --------------------------------------------------------------


def freeze_frame_entry(
    frame: FreezeFrame | None, codes: list[DiagnosticCode] | None = None
) -> dict[str, Any]:
    """Auslösender Code und Messwerte als Zeilen (Bezeichnung, Wert, Einheit).

    ``available`` ist falsch, wenn das Steuergerät nicht geantwortet hat; ``empty``,
    wenn es zwar antwortet, aber keinen Freeze Frame gespeichert hat.
    """
    if frame is None:
        return {"available": False, "empty": True, "dtc": "", "dtcTitle": "", "rows": []}
    rows: list[dict[str, str]] = []
    for key, value in frame.values.items():
        label, unit, decimals = FREEZE_LABELS.get(key, (key, "", 2))
        rows.append(
            {"key": key, "label": label, "value": number_text(value, decimals), "unit": unit}
        )
    title = ""
    if frame.dtc is not None:
        for c in codes or ():
            if c.code == frame.dtc and c.info is not None:
                title = c.info.title
                break
    return {
        "available": True,
        "empty": frame.dtc is None and not rows,
        "dtc": frame.dtc or "",
        "dtcTitle": title,
        "rows": rows,
    }


# --- Fahrzeug ------------------------------------------------------------------


def vehicle_entry(vin: VinInfo | None) -> dict[str, Any]:
    """FIN und was sich daraus ablesen lässt; ``{"available": False}`` ohne FIN."""
    if vin is None:
        return {"available": False}
    facts = [
        ("Hersteller", vin.manufacturer or "unbekannt"),
        ("Land", vin.country or "unbekannt"),
        # Stelle 10 wiederholt sich alle 30 Jahre; außerhalb Nordamerikas nicht eindeutig
        ("Modelljahr", model_year_text(vin) or "unbekannt"),
        ("Herstellercode (WMI)", vin.wmi),
        ("Prüfziffer", checksum_text(vin)),
    ]
    return {
        "available": True,
        "vin": vin.vin,
        "valid": vin.valid,
        "checksumOk": vin.checksum_ok,
        "manufacturer": vin.manufacturer or "",
        "country": vin.country or "",
        "modelYear": vin.model_year if vin.model_year is not None else 0,
        "modelYearAlternatives": list(vin.model_year_alternatives),
        "facts": [{"label": label, "value": value} for label, value in facts],
        "online": [
            {"label": VPIC_FIELDS.get(key, key), "value": value}
            for key, value in vin.online.items()
            if value.strip()
        ],
    }
