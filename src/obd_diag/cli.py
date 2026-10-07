"""Kommandozeile: ``obd-diag``."""

import argparse
import dataclasses
import json
import sys
import time
from pathlib import Path

from obd_diag import __version__
from obd_diag.data.dtc_catalog import DtcCatalog
from obd_diag.protocol.elm327 import Elm327, ElmError
from obd_diag.protocol.obd import FreezeFrame
from obd_diag.protocol.pids import PidSpec
from obd_diag.services.clear import ClearRefused, check_preconditions, clear_codes, clearable_codes
from obd_diag.services.diagnostics import (
    LOW_VOLTAGE,
    DiagnosticCode,
    DtcKind,
    ScanResult,
    scan,
    scan_to_dict,
)
from obd_diag.services.live import (
    DEFAULT_INTERVAL,
    LOW_VOLTAGE_INTERVAL,
    LiveRecorder,
    LiveSample,
    new_recording_path,
    prepare_live,
    recording_dir,
    run_live,
    select_pids,
)
from obd_diag.services.readiness import ALL_COMPLETE_LABEL, AU_NOTE, MonitorState, ReadinessStatus
from obd_diag.services.session import Session, run_diagnosis, save_session, session_to_dict
from obd_diag.services.storage import trace_dir
from obd_diag.services.vehicle import (
    VPIC_FIELDS,
    VinInfo,
    checksum_text,
    decode_vin,
    lookup_vpic,
    model_year_text,
    read_vin,
)
from obd_diag.transport import Transport, TransportError
from obd_diag.transport.discovery import list_ports
from obd_diag.transport.serial import SerialTransport
from obd_diag.transport.trace import FileTracingTransport, new_trace_path

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


_AUTO_TRACE = "auto"
_AUTO_RECORD = "auto"


def _transport(args: argparse.Namespace) -> Transport:
    """Serieller Transport aus ``--port``/``--baud``, bei ``--trace`` mit Mitschnitt."""
    transport: Transport = SerialTransport(args.port, args.baud)
    if args.trace is None:
        return transport
    path = new_trace_path(trace_dir()) if args.trace == _AUTO_TRACE else Path(args.trace)
    print(f"Mitschnitt: {path}", file=sys.stderr)
    return FileTracingTransport(transport, path, f"{args.port} {args.baud} Baud")


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
        with _transport(args) as transport:
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
        with _transport(args) as transport:
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


_MONITOR_STATES = {
    MonitorState.COMPLETE: "abgeschlossen",
    MonitorState.INCOMPLETE: "nicht abgeschlossen",
    MonitorState.NOT_SUPPORTED: "nicht unterstützt",
}

# Freeze-Frame-Werte: Schlüssel in ``FreezeFrame.values``, Bezeichnung, Einheit
_FREEZE_VALUES = (
    ("engine_load_pct", "Motorlast", "%"),
    ("coolant_temp_c", "Kühlmitteltemperatur", "°C"),
    ("rpm", "Drehzahl", "1/min"),
    ("speed_kmh", "Geschwindigkeit", "km/h"),
)


def _print_vehicle(vehicle: VinInfo | None) -> None:
    print("Fahrzeug:")
    if vehicle is None:
        print("  FIN nicht verfügbar (Mode 09 nicht unterstützt oder nicht lesbar).")
        return
    rows = [("FIN", vehicle.vin)]
    if not vehicle.valid:
        rows.append(("Hinweis", "FIN ungültig (Länge oder Zeichen)"))
    else:
        rows.append(("Prüfziffer", checksum_text(vehicle)))
    rows.append(("Hersteller", vehicle.manufacturer or "unbekannt"))
    rows.append(("Land", vehicle.country or "unbekannt"))
    year = model_year_text(vehicle)
    if year is not None:
        rows.append(("Modelljahr", year))
    rows += [
        (label, vehicle.online[key]) for key, label in VPIC_FIELDS.items() if key in vehicle.online
    ]
    width = max(len(label) for label, _ in rows) + 1
    for label, value in rows:
        print(f"  {label + ':':<{width}} {value}")


def _print_readiness(readiness: ReadinessStatus | None) -> None:
    if readiness is None:
        print("Readiness: nicht verfügbar (PID 01 nicht beantwortet).")
        return
    engine = "Diesel" if readiness.compression_ignition else "Otto"
    print(f"Readiness ({engine}-Motor):")
    print(f"  Kontrollleuchte (MIL): {'an' if readiness.mil_on else 'aus'}")
    print(f"  Gemeldete Fehlercodes: {readiness.dtc_count}")
    width = max(len(m.name) for m in readiness.monitors) + 1
    for m in readiness.monitors:
        print(f"  {m.name + ':':<{width}} {_MONITOR_STATES[m.state]}")
    print(f"  {ALL_COMPLETE_LABEL}: {'ja' if readiness.all_complete else 'nein'}")
    print(f"  Hinweis: {AU_NOTE}")


def _print_freeze_frame(freeze: FreezeFrame | None) -> None:
    if freeze is None:
        print("Freeze Frame: keiner gespeichert.")
        return
    print(f"Freeze Frame (ausgelöst durch {freeze.dtc or 'unbekannten Code'}):")
    for key, label, unit in _FREEZE_VALUES:
        if key in freeze.values:
            print(f"  {label + ':':<22} {freeze.values[key]:g} {unit}")


def _print_session(session: Session) -> None:
    _print_vehicle(session.vehicle)
    print()
    _print_scan(session.scan)
    print()
    _print_readiness(session.readiness)
    print()
    _print_freeze_frame(session.freeze_frame)


def _export(session: Session, pdf: Path | None, csv: Path | None) -> None:
    # erst hier importieren: ReportLab wird nur für den Export gebraucht
    from obd_diag.export.report import export_csv, export_pdf

    if pdf is not None:
        export_pdf(session, pdf)
        print(f"PDF-Bericht: {pdf}", file=sys.stderr)
    if csv is not None:
        export_csv(session, csv)
        print(f"CSV: {csv}", file=sys.stderr)


def _run_diagnose(args: argparse.Namespace) -> int:
    catalog = _open_catalog()
    try:
        with _transport(args) as transport:
            session = run_diagnosis(
                Elm327(transport), catalog, args.lang, online_vin_lookup=args.online_vin
            )
    finally:
        if catalog is not None:
            catalog.close()
    if args.json:
        print(json.dumps(session_to_dict(session), ensure_ascii=False, indent=2))
    else:
        _print_session(session)
    # Pfade auf stderr, damit stdout bei --json reines JSON bleibt
    try:
        if args.save:
            print(f"Sitzung gespeichert: {save_session(session)}", file=sys.stderr)
        _export(session, args.pdf, args.csv)
    except OSError as e:
        print(f"Fehler: {e.filename or ''}: {e.strerror or e}", file=sys.stderr)
        return 1
    return 0


def _run_vin(args: argparse.Namespace) -> int:
    vin = args.vin
    protocol: str | None = None  # nur bekannt, wenn die FIN aus dem Fahrzeug kommt
    if vin is None:
        with _transport(args) as transport:
            elm = Elm327(transport)
            elm.initialize()
            protocol = elm.protocol().name
            vin = read_vin(elm)
        if vin is None:
            print("Fehler: Fahrzeug liefert keine FIN (Mode 09 PID 02).", file=sys.stderr)
            return 1
    info = decode_vin(vin, protocol=protocol)
    if args.online_vin and info.valid:
        info = dataclasses.replace(info, online=lookup_vpic(info.vin))
    if args.json:
        print(json.dumps(dataclasses.asdict(info), ensure_ascii=False, indent=2))
    else:
        _print_vehicle(info)
    return 0


def _run_export(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if args.pdf is None and args.csv is None:
        parser.error("export: --pdf und/oder --csv angeben")
    # erst hier importieren: ReportLab wird nur für den Export gebraucht
    from obd_diag.export.report import export_csv, export_pdf
    from obd_diag.services.session import load_session

    try:
        session = load_session(args.session)
        if args.pdf is not None:
            export_pdf(session, args.pdf)
            print(f"PDF-Bericht: {args.pdf}")
        if args.csv is not None:
            export_csv(session, args.csv)
            print(f"CSV: {args.csv}")
    except OSError as e:
        print(f"Fehler: {e.filename or args.session}: {e.strerror or e}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 1
    return 0


# --- Live-Daten ---------------------------------------------------------------------

LIVE_SAFETY_NOTE = "Hinweis: Während der Fahrt nur durch Beifahrer bedienen."
MIN_INTERVAL = 0.1  # Sekunden; schneller kommt ein ELM327 ohnehin nicht hinterher
_MISSING = "-"  # nicht lesbarer Wert in der Tabelle (kein Gedankenstrich)


def _interval(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"keine Zahl: {text!r}") from None
    if not value >= MIN_INTERVAL:  # fängt auch nan ab
        raise argparse.ArgumentTypeError(f"mindestens {MIN_INTERVAL:g} Sekunden")
    return value


def _duration(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"keine Zahl: {text!r}") from None
    if not value > 0:
        raise argparse.ArgumentTypeError("muss größer als 0 sein")
    return value


def _keys(text: str) -> list[str]:
    return [key.strip().lower() for key in text.split(",") if key.strip()]


def _live_number(value: float | None) -> str:
    if value is None:
        return _MISSING
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


class _LiveTable:
    """Feste Spalten: Zeit, je Wert ``Name (Einheit)``, Bordspannung."""

    def __init__(self, pids: list[PidSpec]) -> None:
        self.pids = pids
        self.headers = ["Zeit (s)", *(f"{p.name} ({p.unit})" for p in pids), "Spannung (V)"]
        self.widths = [max(len(h), 8) for h in self.headers]
        self.rounds = 0
        self.throttled = False

    def print_header(self) -> None:
        print("  ".join(h.rjust(w) for h, w in zip(self.headers, self.widths, strict=True)))

    def on_sample(self, sample: LiveSample) -> None:
        if sample.throttled != self.throttled:
            self.throttled = sample.throttled
            if sample.throttled and sample.voltage is not None:
                print(
                    f"Hinweis: Bordspannung {sample.voltage:.1f} V unter {LOW_VOLTAGE:.1f} V, "
                    f"Abfrage nur alle {LOW_VOLTAGE_INTERVAL:g} s.",
                    file=sys.stderr,
                )
            else:
                print("Hinweis: Bordspannung wieder ausreichend.", file=sys.stderr)
        cells = [
            f"{sample.elapsed:.1f}",
            *(_live_number(sample.values.get(p.key)) for p in self.pids),
            _MISSING if sample.voltage is None else f"{sample.voltage:.1f}",
        ]
        print("  ".join(c.rjust(w) for c, w in zip(cells, self.widths, strict=True)), flush=True)
        self.rounds += 1


def _print_available(pids: list[PidSpec]) -> None:
    if not pids:
        print("Das Fahrzeug meldet keine bekannten Live-Werte.")
        return
    key_width = max(len("Schlüssel"), *(len(p.key) for p in pids))
    name_width = max(len("Name"), *(len(p.name) for p in pids))
    print(f"{'Schlüssel':<{key_width}}  {'Name':<{name_width}}  Einheit")
    for p in pids:
        print(f"{p.key:<{key_width}}  {p.name:<{name_width}}  {p.unit}")


def _run_live(args: argparse.Namespace) -> int:
    keys = None if args.pids is None else _keys(args.pids)
    if not args.list:
        print(LIVE_SAFETY_NOTE, file=sys.stderr)
    table: _LiveTable | None = None
    record: Path | None = None
    try:
        with _transport(args) as transport:
            elm = Elm327(transport)
            setup = prepare_live(elm)
            if args.list:
                _print_available(setup.available)
                return 0
            pids = select_pids(setup, keys)
            if not pids:
                print(
                    "Fehler: Das Fahrzeug unterstützt keinen der Standardwerte; "
                    "mit --list anzeigen, mit --pids wählen.",
                    file=sys.stderr,
                )
                return 1
            if args.record is not None:
                record = (
                    new_recording_path(recording_dir())
                    if args.record == _AUTO_RECORD
                    else Path(args.record)
                )
            print(f"Adapter: {setup.adapter}, Protokoll: {setup.protocol}", file=sys.stderr)
            recorder = None if record is None else LiveRecorder(record, pids)
            if record is not None:
                print(f"Aufzeichnung: {record}", file=sys.stderr)
            table = _LiveTable(pids)
            table.print_header()
            start = time.monotonic()
            duration: float | None = args.duration
            try:
                run_live(
                    elm,
                    pids,
                    on_sample=table.on_sample,
                    should_stop=lambda: (
                        duration is not None and time.monotonic() - start >= duration
                    ),
                    interval=args.interval,
                    recorder=recorder,
                    clock=time.monotonic,
                    sleep=time.sleep,
                )
            finally:
                if recorder is not None:
                    recorder.close()
    except KeyboardInterrupt:
        print(file=sys.stderr)  # hinter dem ^C des Terminals
    except ValueError as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"Fehler: {e.filename or ''}: {e.strerror or e}", file=sys.stderr)
        return 1
    rounds = 0 if table is None else table.rounds
    print(f"Beendet nach {rounds} {'Runde' if rounds == 1 else 'Runden'}.", file=sys.stderr)
    if record is not None and table is not None:
        print(f"Aufzeichnung: {record}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="obd-diag")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    connection = argparse.ArgumentParser(add_help=False)
    connection.add_argument("--port", default="/dev/ttyUSB0")
    connection.add_argument("--baud", type=int, default=38400)
    connection.add_argument(
        "--trace",
        nargs="?",
        const=_AUTO_TRACE,
        metavar="DATEI",
        help="Adapter-Kommunikation mitschneiden (ohne DATEI: unter "
        "$XDG_DATA_HOME/obd-diag/traces); enthält ggf. die FIN",
    )

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
    online_help = (
        "FIN zusätzlich bei NHTSA vPIC (USA) nachschlagen; sendet die FIN ins Internet, "
        "Ergebnis wird lokal gecacht"
    )
    diagnose_parser = sub.add_parser(
        "diagnose",
        parents=[connection],
        help="vollständige Diagnose: Fehlercodes, Readiness, Freeze Frame, FIN (nur lesend)",
    )
    diagnose_parser.add_argument("--lang", choices=("de", "en"), default="de")
    diagnose_parser.add_argument("--json", action="store_true", help="Sitzung als JSON ausgeben")
    diagnose_parser.add_argument("--online-vin", action="store_true", help=online_help)
    diagnose_parser.add_argument(
        "--save", action="store_true", help="Sitzung unter $XDG_DATA_HOME/obd-diag/sessions sichern"
    )
    diagnose_parser.add_argument("--pdf", type=Path, metavar="DATEI.pdf", help="PDF-Bericht")
    diagnose_parser.add_argument(
        "--csv", type=Path, metavar="DATEI.csv", help="CSV, eine Zeile pro Fehlercode"
    )
    vin_parser = sub.add_parser(
        "vin", parents=[connection], help="FIN lesen (Mode 09) und dekodieren"
    )
    vin_parser.add_argument(
        "vin", nargs="?", metavar="FIN", help="diese FIN dekodieren, ohne Adapter"
    )
    vin_parser.add_argument("--json", action="store_true", help="Ergebnis als JSON ausgeben")
    vin_parser.add_argument("--online-vin", action="store_true", help=online_help)
    live_parser = sub.add_parser(
        "live",
        parents=[connection],
        help="Live-Daten (Mode 01) fortlaufend anzeigen und aufzeichnen; Ende mit Strg+C",
    )
    live_parser.add_argument(
        "--pids",
        metavar="SCHLÜSSEL,...",
        help="Werte, z. B. rpm,speed,coolant_temp (Standard: die üblichen, soweit "
        "unterstützt; Schlüssel zeigt --list)",
    )
    live_parser.add_argument(
        "--list", action="store_true", help="unterstützte Werte mit Schlüssel anzeigen und beenden"
    )
    live_parser.add_argument(
        "--interval",
        type=_interval,
        default=DEFAULT_INTERVAL,
        metavar="SEK",
        help=f"Abstand der Abfragerunden (Standard {DEFAULT_INTERVAL:g}, mindestens "
        f"{MIN_INTERVAL:g})",
    )
    live_parser.add_argument(
        "--duration", type=_duration, metavar="SEK", help="nach SEK Sekunden beenden"
    )
    live_parser.add_argument(
        "--record",
        nargs="?",
        const=_AUTO_RECORD,
        metavar="DATEI.csv",
        help="als CSV aufzeichnen (ohne DATEI: unter $XDG_DATA_HOME/obd-diag/recordings); "
        "vorhandene Dateien werden nicht überschrieben",
    )
    sub.add_parser("ports", help="angeschlossene Adapter auflisten")
    export_parser = sub.add_parser(
        "export", help="gespeicherte Diagnosesitzung (JSON) als PDF-Bericht oder CSV ausgeben"
    )
    export_parser.add_argument("session", type=Path, metavar="SESSION.json")
    export_parser.add_argument("--pdf", type=Path, metavar="DATEI.pdf", help="PDF-Bericht")
    export_parser.add_argument(
        "--csv", type=Path, metavar="DATEI.csv", help="CSV, eine Zeile pro Fehlercode"
    )

    args = parser.parse_args(argv)
    try:
        if args.command == "info":
            with _transport(args) as transport:
                elm = Elm327(transport)
                print(f"Adapter:      {elm.initialize()}")
                print(f"Bordspannung: {elm.voltage():.1f} V")
        elif args.command == "scan":
            _run_scan(args)
        elif args.command == "clear":
            return _run_clear(args)
        elif args.command == "diagnose":
            return _run_diagnose(args)
        elif args.command == "vin":
            return _run_vin(args)
        elif args.command == "live":
            return _run_live(args)
        elif args.command == "ports":
            _run_ports()
        elif args.command == "export":
            return _run_export(args, export_parser)
    except (TransportError, ElmError) as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 1
    return 0
