"""Anbindung der Oberfläche an Transport und Services.

Ein Job öffnet den seriellen Port, erledigt seine Arbeit und schließt den Port
wieder; zwischen Scan und Löschen bleibt nichts offen. So kann das Kabel zwischen
zwei Aktionen abgezogen werden, ohne dass die Oberfläche einen toten Port hält,
und jeder Job beginnt mit einem frisch initialisierten Adapter.

Auch der Fehlercode-Katalog wird je Job geöffnet: SQLite-Verbindungen gehören dem
Thread, der sie angelegt hat, und Jobs laufen nicht im GUI-Thread.
"""

from collections.abc import Callable
from dataclasses import dataclass

from obd_diag.data.dtc_catalog import DtcCatalog
from obd_diag.protocol.elm327 import Elm327
from obd_diag.services.clear import ClearResult, clear_codes
from obd_diag.services.diagnostics import ScanResult, scan
from obd_diag.transport.discovery import PortInfo, list_ports
from obd_diag.transport.serial import SerialTransport


@dataclass(frozen=True)
class Backend:
    """Die Aktionen, die das View-Model braucht; Tests setzen eigene Funktionen ein.

    ``scan`` und ``clear`` blockieren und laufen im Worker-Thread, ``list_ports`` und
    ``catalog_available`` sind schnell und laufen im GUI-Thread.
    """

    scan: Callable[[str, int], ScanResult]
    clear: Callable[[str, int], ClearResult]
    list_ports: Callable[[], list[PortInfo]]
    catalog_available: Callable[[], bool]


def scan_port(port: str, baud: int, lang: str = "de") -> ScanResult:
    catalog = DtcCatalog.default()
    try:
        with SerialTransport(port, baud) as transport:
            return scan(Elm327(transport), catalog, lang)
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
        scan=scan_port,
        clear=clear_port,
        list_ports=list_ports,
        catalog_available=catalog_available,
    )
