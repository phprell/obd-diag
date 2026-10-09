"""Listenmodell der Fehlercodes für die QML-Liste und die Detailansicht."""

from typing import Any

from PySide6.QtCore import (
    QAbstractListModel,
    QByteArray,
    QModelIndex,
    QPersistentModelIndex,
    Qt,
    Signal,
)

from obd_diag.services.diagnostics import DiagnosticCode, DtcKind
from obd_diag.services.dtc_online import search_url

KIND_LABELS = {
    DtcKind.STORED: "Gespeichert",
    DtcKind.PENDING: "Ausstehend",
    DtcKind.PERMANENT: "Permanent",
}

LIKELIHOOD_LABELS = {"high": "hoch", "medium": "mittel", "low": "niedrig"}

DIFFICULTY_LABELS = {"easy": "einfach", "medium": "mittel", "hard": "schwierig"}

NO_INFO_TITLE = "Keine Beschreibung im Katalog"


def cost_text(cost: tuple[int, int] | None) -> str:
    if cost is None:
        return ""
    low, high = cost
    if low == high:
        return f"ca. {low} €"
    return f"ca. {low}–{high} €"  # noqa: RUF001


def code_entry(c: DiagnosticCode, manufacturer: str | None = None) -> dict[str, Any]:
    """Alle Angaben zu einem Code als einfache Werte, wie QML sie lesen kann.

    ``manufacturer`` (aus der FIN) fließt nur in den Link für die Websuche ein.
    """
    info = c.info
    online = c.online
    return {
        "code": c.code,
        "kind": c.kind.value,
        "kindLabel": KIND_LABELS[c.kind],
        "hasInfo": info is not None,
        "title": info.title if info is not None else NO_INFO_TITLE,
        "description": (info.description or "") if info is not None else "",
        "causes": [
            {
                "label": cause.label,
                "likelihood": cause.likelihood,
                "likelihoodLabel": LIKELIHOOD_LABELS.get(cause.likelihood, cause.likelihood),
            }
            for cause in (info.causes if info is not None else ())
        ],
        "symptoms": list(info.symptoms) if info is not None else [],
        # None bedeutet: im Katalog nicht angegeben
        "mil": info.mil if info is not None else None,
        "emissionsRelevant": info.emissions_relevant if info is not None else None,
        "difficultyLabel": (
            DIFFICULTY_LABELS.get(info.repair_difficulty, info.repair_difficulty)
            if info is not None and info.repair_difficulty is not None
            else ""
        ),
        "costText": cost_text(info.cost_eur if info is not None else None),
        # ungeprüfte Online-Erklärung (nur nach Opt-in), sonst leer
        "onlineText": online.text if online is not None else "",
        "onlineSource": online.source if online is not None else "",
        "onlineUrl": online.url if online is not None else "",
        # Websuche im Browser; obd-diag ruft den Link nie selbst ab
        "searchUrl": search_url(c.code, manufacturer),
    }


_ROLE_NAMES = (
    "code",
    "kind",
    "kindLabel",
    "hasInfo",
    "title",
    "description",
    "causes",
    "symptoms",
    "mil",
    "emissionsRelevant",
    "difficultyLabel",
    "costText",
    "onlineText",
    "onlineSource",
    "onlineUrl",
    "searchUrl",
)
_ROLES = {Qt.ItemDataRole.UserRole + 1 + i: name for i, name in enumerate(_ROLE_NAMES)}


class CodeListModel(QAbstractListModel):
    """Die Codes des letzten Scans, in der Reihenfolge gespeichert, ausstehend, permanent."""

    countChanged = Signal()

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._entries: list[dict[str, Any]] = []
        self._codes: list[DiagnosticCode] = []

    def set_codes(self, codes: list[DiagnosticCode], manufacturer: str | None = None) -> None:
        order = list(KIND_LABELS)
        self.beginResetModel()
        self._codes = sorted(codes, key=lambda c: order.index(c.kind))  # stabil
        self._entries = [code_entry(c, manufacturer) for c in self._codes]
        self.endResetModel()
        self.countChanged.emit()

    @property
    def codes(self) -> list[DiagnosticCode]:
        return list(self._codes)

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
            return entry["code"]
        name = _ROLES.get(role)
        return entry[name] if name is not None else None

    def roleNames(self) -> dict[int, QByteArray]:
        return {role: QByteArray(name.encode()) for role, name in _ROLES.items()}
