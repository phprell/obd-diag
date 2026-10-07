"""Anbindung der Oberfläche an Transport und Services.

Ein Job öffnet den seriellen Port, erledigt seine Arbeit und schließt den Port
wieder; zwischen Diagnose und Löschen bleibt nichts offen. So kann das Kabel zwischen
zwei Aktionen abgezogen werden, ohne dass die Oberfläche einen toten Port hält,
und jeder Job beginnt mit einem frisch initialisierten Adapter.

Auch der Fehlercode-Katalog wird je Job geöffnet: SQLite-Verbindungen gehören dem
Thread, der sie angelegt hat, und Jobs laufen nicht im GUI-Thread.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from obd_diag.data.dtc_catalog import DtcCatalog
from obd_diag.export.report import export_csv, export_pdf
from obd_diag.protocol.elm327 import Elm327
from obd_diag.services.clear import ClearResult, clear_codes
from obd_diag.services.session import Session, load_session, run_diagnosis, save_session
from obd_diag.transport.discovery import PortInfo, list_ports
from obd_diag.transport.serial import SerialTransport


def _save_default(session: Session) -> Path:
    return save_session(session)


@dataclass(frozen=True)
class Backend:
    """Die Aktionen, die das View-Model braucht; Tests setzen eigene Funktionen ein.

    ``diagnose`` (Port, Baudrate, FIN online nachschlagen) und ``clear`` blockieren und
    laufen im Worker-Thread, ebenso ``export_pdf``/``export_csv`` (ReportLab braucht
    etwas). ``list_ports``, ``catalog_available``, ``save_session`` und
    ``load_session`` sind schnell und laufen im GUI-Thread.
    """

    diagnose: Callable[[str, int, bool], Session]
    clear: Callable[[str, int], ClearResult]
    list_ports: Callable[[], list[PortInfo]]
    catalog_available: Callable[[], bool]
    save_session: Callable[[Session], Path] = _save_default
    load_session: Callable[[Path], Session] = load_session
    export_pdf: Callable[[Session, Path], None] = export_pdf
    export_csv: Callable[[Session, Path], None] = export_csv


def diagnose_port(
    port: str, baud: int, online_vin_lookup: bool = False, lang: str = "de"
) -> Session:
    """Scan, Readiness, Freeze Frame und FIN; nur lesend."""
    catalog = DtcCatalog.default()
    try:
        with SerialTransport(port, baud) as transport:
            return run_diagnosis(
                Elm327(transport), catalog, lang, online_vin_lookup=online_vin_lookup
            )
    finally:
        if catalog is not None:
            catalog.close()


def clear_port(port: str, baud: int, lang: str = "de") -> ClearResult:
    catalog = DtcCatalog.default()
    try:
        with SerialTransport(port, baud) as transport:
            return clear_codes(Elm327(transport), catalog, lang=lang)
    finally:
        if catalog is not None:
            catalog.close()


def catalog_available() -> bool:
    return DtcCatalog.default() is not None


def serial_backend() -> Backend:
    return Backend(
        diagnose=diagnose_port,
        clear=clear_port,
        list_ports=list_ports,
        catalog_available=catalog_available,
    )
