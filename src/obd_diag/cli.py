"""Kommandozeile: ``obd-diag``."""

import argparse

from obd_diag import __version__
from obd_diag.protocol.elm327 import Elm327
from obd_diag.transport.serial import SerialTransport


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="obd-diag")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    info = sub.add_parser("info", help="Adapter-Version und Bordspannung anzeigen")
    info.add_argument("--port", default="/dev/ttyUSB0")
    info.add_argument("--baud", type=int, default=38400)

    args = parser.parse_args(argv)
    if args.command == "info":
        with SerialTransport(args.port, args.baud) as transport:
            elm = Elm327(transport)
            print(f"Adapter:      {elm.initialize()}")
            print(f"Bordspannung: {elm.voltage():.1f} V")
    return 0
