"""View-Model des Sitzungsvergleichs (Datei → Mit Sitzung vergleichen …).

Vergleicht die angezeigte Sitzung (frisch gelesen oder geöffnet) mit einer gespeicherten
Datei. Das Laden ist schnell und läuft im GUI-Thread, wie „Sitzung öffnen“; der Adapter
wird nicht gebraucht, daher geht es auch, während Live-Daten laufen.
"""

from pathlib import Path
from typing import Any

from PySide6.QtCore import Property, QObject, Signal, Slot

from obd_diag.i18n import N_, tr
from obd_diag.services.compare import (
    DIFFERENT_VEHICLE_WARNING,
    SessionComparison,
    change_text,
    compare_sessions,
)
from obd_diag.services.session import Session
from obd_diag.ui.viewmodels.diagnosis import DiagnosisViewModel, local_path

_CURRENT = N_("angezeigte Diagnose")


class CompareViewModel(QObject):
    changed = Signal()
    compared = Signal()  # Ergebnis oder Fehlermeldung steht bereit

    def __init__(self, diagnosis: DiagnosisViewModel, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._diagnosis = diagnosis
        self._other: Session | None = None
        self._other_path: Path | None = None
        self._current: Session | None = None
        self._comparison: SessionComparison | None = None
        self._error = ""

    @Property(bool, notify=changed)
    def hasResult(self) -> bool:
        return self._comparison is not None

    @Property(str, notify=changed)
    def errorMessage(self) -> str:
        return self._error

    @Property(dict, notify=changed)
    def comparison(self) -> dict[str, Any]:
        """Kopf und Abschnitte für den Dialog; leer ohne Ergebnis."""
        result = self._comparison
        if result is None:
            return {}
        current, other = tr(_CURRENT), str(self._other_path)
        before_name = other if result.before is self._other else current
        after_name = other if result.after is self._other else current
        sections = [
            {
                "key": s.key,
                "title": s.title,
                "changes": [
                    {
                        "kind": c.kind.value,
                        "subject": c.subject,
                        "text": change_text(c),
                        "trend": c.trend.value,
                    }
                    for c in s.changes
                ],
            }
            for s in result.sections
            if s.changes
        ]
        return {
            "before": f"{result.before.created:%Y-%m-%d %H:%M} · {before_name}",
            "after": f"{result.after.created:%Y-%m-%d %H:%M} · {after_name}",
            "warning": tr(DIFFERENT_VEHICLE_WARNING) if result.same_vehicle is False else "",
            "summary": "" if result.has_differences else tr("Keine Unterschiede."),
            "sections": sections,
        }

    @Slot(str)
    def compareWith(self, target: str) -> None:
        """Vergleicht die angezeigte Sitzung mit der Datei ``target``."""
        current = self._diagnosis.session
        if current is None or not target.strip():
            return
        path = local_path(target.strip())
        try:
            other = self._diagnosis.backend.load_session(path)
        except (OSError, ValueError) as e:
            self._comparison = None
            reason = str(e)
            if isinstance(e, OSError):
                reason = f"{e.strerror or e} ({e.filename or path})"
            self._error = tr("Sitzung ließ sich nicht öffnen: {error}").format(error=reason)
        else:
            self._current, self._other, self._other_path = current, other, path
            self._error = ""
            # Datei zuerst: bei gleichem Zeitpunkt gilt sie als „vorher“
            self._comparison = compare_sessions(other, current)
        self.changed.emit()
        self.compared.emit()

    def retranslate(self) -> None:
        """Texte entstehen beim Vergleich; nach einem Sprachwechsel neu vergleichen."""
        if self._current is not None and self._other is not None:
            self._comparison = compare_sessions(self._other, self._current)
        self.changed.emit()
