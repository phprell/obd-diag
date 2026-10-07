"""Kommandozeile: ``obd-diag``."""

import argparse
import dataclasses
import json
import sys

from obd_diag import __version__
from obd_diag.data.dtc_catalog import DtcCatalog
from obd_diag.protocol.elm327 import Elm327, ElmError
from obd_diag.services.diagnostics import DtcKind, ScanResult, scan
from obd_diag.transport import TransportError
from obd_diag.transport.serial import SerialTransport

LOW_VOLTAGE_WARNING = "Batteriespannung niedrig – Ergebnisse können unzuverlässig sein"  # noqa: RUF001

_SECTIONS = (
    (DtcKind.STORED, "Gespeichert"),
    (DtcKind.PENDING, "Ausstehend"),
    (DtcKind.PERMANENT, "Permanent"),
)


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
        for c in codes:
            title = c.info.title if c.info is not None else "(keine Beschreibung im Katalog)"
            print(f"  {c.code}  {title}")


def _scan_json(result: ScanResult) -> str:
    data = dataclasses.asdict(result)
    data["low_voltage"] = result.low_voltage
    return json.dumps(data, ensure_ascii=False, indent=2)


def _run_scan(args: argparse.Namespace) -> None:
    catalog = DtcCatalog.default()
    if catalog is None:
        print(
            "Hinweis: Fehlercode-Katalog fehlt, Codes werden ohne Beschreibung angezeigt. "
            "Erzeugen mit: uv run python tools/build_dtc_db.py",
            file=sys.stderr,
        )
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

    args = parser.parse_args(argv)
    try:
        if args.command == "info":
            with SerialTransport(args.port, args.baud) as transport:
                elm = Elm327(transport)
                print(f"Adapter:      {elm.initialize()}")
                print(f"Bordspannung: {elm.voltage():.1f} V")
        elif args.command == "scan":
            _run_scan(args)
    except (TransportError, ElmError) as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 1
    return 0
