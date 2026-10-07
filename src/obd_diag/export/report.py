"""PDF-Bericht und CSV einer Diagnosesitzung."""

from pathlib import Path

from obd_diag.services.session import Session


def export_csv(session: Session, path: Path) -> None:
    """Eine Zeile pro Fehlercode (Code, Art, Titel, ...), UTF-8 mit BOM für Excel."""
    raise NotImplementedError


def export_pdf(session: Session, path: Path) -> None:
    """Bericht: Fahrzeug, Adapter/Protokoll/Spannung, Fehlercodes mit Erklärung,
    Freeze Frame, Readiness. Deutsch, DIN A4."""
    raise NotImplementedError
