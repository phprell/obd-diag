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
from obd_diag.services.storage import trace_dir
from obd_diag.transport.discovery import PortInfo, list_ports
from obd_diag.transport.trace import new_trace_path, open_serial


def _save_default(session: Session) -> Path:
    return save_session(session)


def _no_tracing(enabled: bool) -> None:
    pass


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
    # Adapter-Mitschnitt für folgende Jobs ein-/ausschalten (siehe transport/trace.py)
    set_tracing: Callable[[bool], None] = _no_tracing


def _trace_path(trace: bool) -> Path | None:
    return new_trace_path(trace_dir()) if trace else None


def diagnose_port(
    port: str, baud: int, online_vin_lookup: bool = False, lang: str = "de", trace: bool = False
) -> Session:
    """Scan, Readiness, Freeze Frame und FIN; nur lesend."""
    catalog = DtcCatalog.default()
    try:
        with open_serial(port, baud, _trace_path(trace)) as transport:
            return run_diagnosis(
                Elm327(transport), catalog, lang, online_vin_lookup=online_vin_lookup
            )
    finally:
        if catalog is not None:
            catalog.close()


def clear_port(port: str, baud: int, lang: str = "de", trace: bool = False) -> ClearResult:
    catalog = DtcCatalog.default()
    try:
        with open_serial(port, baud, _trace_path(trace)) as transport:
            return clear_codes(Elm327(transport), catalog, lang=lang)
    finally:
        if catalog is not None:
            catalog.close()


def catalog_available() -> bool:
    return DtcCatalog.default() is not None


def serial_backend() -> Backend:
    # Wird im GUI-Thread gesetzt und beim Start eines Jobs im Worker gelesen.
    tracing = {"enabled": False}

    def set_tracing(enabled: bool) -> None:
        tracing["enabled"] = enabled

    return Backend(
        diagnose=lambda port, baud, online: diagnose_port(
            port, baud, online, trace=tracing["enabled"]
        ),
        clear=lambda port, baud: clear_port(port, baud, trace=tracing["enabled"]),
        list_ports=list_ports,
        catalog_available=catalog_available,
        set_tracing=set_tracing,
    )
