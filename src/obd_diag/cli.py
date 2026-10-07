"""Kommandozeile: ``obd-diag``."""

import argparse
import json
import sys

from obd_diag import __version__
from obd_diag.data.dtc_catalog import DtcCatalog
from obd_diag.protocol.elm327 import Elm327, ElmError
from obd_diag.services.clear import ClearRefused, check_preconditions, clear_codes, clearable_codes
from obd_diag.services.diagnostics import DiagnosticCode, DtcKind, ScanResult, scan, scan_to_dict
from obd_diag.transport import TransportError
from obd_diag.transport.discovery import list_ports
from obd_diag.transport.serial import SerialTransport

LOW_VOLTAGE_WARNING = "Batteriespannung niedrig – Ergebnisse können unzuverlässig sein"  # noqa: RUF001

_SECTIONS = (
    (DtcKind.STORED, "Gespeichert"),
    (DtcKind.PENDING, "Ausstehend"),
    (DtcKind.PERMANENT, "Permanent"),
)


def _print_codes(codes: list[DiagnosticCode]) -> None:
    for c in codes:
        title = c.info.title if c.info is not None else "(keine Beschreibung im Katalog)"
        print(f"  {c.code}  {title}")


def _print_scan(result: ScanResult) -> None:
    voltage = "unbekannt" if result.voltage is None else f"{result.voltage:.1f} V"
    print(f"Adapter:      {result.adapter}")
    print(f"Protokoll:    {result.protocol}")
    print(f"Bordspannung: {voltage}")
    if result.low_voltage:
        print(f"Warnung: {LOW_VOLTAGE_WARNING}")
    print()
    if not result.codes:
        print("Keine Fehlercodes gespeichert.")
        return
    for kind, heading in _SECTIONS:
        codes = [c for c in result.codes if c.kind is kind]
        if not codes:
            continue
        print(f"{heading}:")
        _print_codes(codes)


def _scan_json(result: ScanResult) -> str:
    return json.dumps(scan_to_dict(result), ensure_ascii=False, indent=2)


def _open_catalog() -> DtcCatalog | None:
    catalog = DtcCatalog.default()
    if catalog is None:
        print(
            "Hinweis: Fehlercode-Katalog fehlt, Codes werden ohne Beschreibung angezeigt. "
            "Erzeugen mit: uv run python tools/build_dtc_db.py",
            file=sys.stderr,
        )
    return catalog


def _run_scan(args: argparse.Namespace) -> None:
    catalog = _open_catalog()
    try:
        with SerialTransport(args.port, args.baud) as transport:
            result = scan(Elm327(transport), catalog, args.lang)
    finally:
        if catalog is not None:
            catalog.close()
    if args.json:
        print(_scan_json(result))
    else:
        _print_scan(result)


def _run_ports() -> None:
    ports = list_ports()
    if not ports:
        print("Keine Adapter gefunden (USB-Seriell oder /dev/rfcomm*).")
        return
    width = max(len(p.device) for p in ports)
    for p in ports:
        print(f"{p.device:<{width}}  {p.description}")


def _confirm() -> bool:
    try:
        answer = input('Wirklich löschen? Zum Bestätigen "ja" eingeben: ')
    except EOFError:
        return False
    return answer.strip().lower() == "ja"


def _run_clear(args: argparse.Namespace) -> int:
    catalog = _open_catalog()
    try:
        with SerialTransport(args.port, args.baud) as transport:
            elm = Elm327(transport)
            preview = scan(elm, catalog, args.lang)
            _print_scan(preview)
            print()
            codes = clearable_codes(preview)
            if not codes:
                print("Keine gespeicherten oder ausstehenden Fehlercodes, es wird nichts gelöscht.")
                return 0
            check_preconditions(elm)
            print("Folgende Fehlercodes werden im Steuergerät gelöscht:")
            unique: dict[str, DiagnosticCode] = {}
            for c in codes:
                unique.setdefault(c.code, c)  # gespeichert und ausstehend: nur einmal nennen
            _print_codes(list(unique.values()))
            print(
                "Achtung: Dabei gehen auch Freeze Frame und Readiness-Status verloren.\n"
                "Die Codes werden vorher gesichert. Ist der Fehler nicht behoben,\n"
                "kommen sie wieder."
            )
            if not args.yes and not _confirm():
                print("Abgebrochen, nichts gelöscht.")
                return 1
            result = clear_codes(elm, catalog, lang=args.lang)
    except ClearRefused as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 1
    finally:
        if catalog is not None:
            catalog.close()
    print(f"Gelöscht. Sicherung: {result.backup_path}")
    print()
    print("Kontroll-Scan:")
    _print_scan(result.after)
    if clearable_codes(result.after):
        print()
        print("Hinweis: Einige Codes sind sofort wieder da, der Fehler besteht vermutlich noch.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="obd-diag")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    connection = argparse.ArgumentParser(add_help=False)
    connection.add_argument("--port", default="/dev/ttyUSB0")
    connection.add_argument("--baud", type=int, default=38400)

    sub.add_parser("info", parents=[connection], help="Adapter-Version und Bordspannung anzeigen")
    scan_parser = sub.add_parser(
        "scan", parents=[connection], help="Fehlercodes lesen (gespeichert, ausstehend, permanent)"
    )
    scan_parser.add_argument("--lang", choices=("de", "en"), default="de")
    scan_parser.add_argument("--json", action="store_true", help="Ergebnis als JSON ausgeben")

    clear_parser = sub.add_parser(
        "clear",
        parents=[connection],
        help="Fehlercodes löschen (Mode 04), vorher sichern; nur bei Motor aus, Zündung an",
    )
    clear_parser.add_argument("--lang", choices=("de", "en"), default="de")
    clear_parser.add_argument("--yes", action="store_true", help="ohne Rückfrage löschen")
    sub.add_parser("ports", help="angeschlossene Adapter auflisten")

    args = parser.parse_args(argv)
    try:
        if args.command == "info":
            with SerialTransport(args.port, args.baud) as transport:
                elm = Elm327(transport)
                print(f"Adapter:      {elm.initialize()}")
                print(f"Bordspannung: {elm.voltage():.1f} V")
        elif args.command == "scan":
            _run_scan(args)
        elif args.command == "clear":
            return _run_clear(args)
        elif args.command == "ports":
            _run_ports()
    except (TransportError, ElmError) as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 1
    return 0
