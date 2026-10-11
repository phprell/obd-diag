"""Kommandozeile: ``obd-diag``."""

import argparse
import dataclasses
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from obd_diag import __version__
from obd_diag.data.dtc_catalog import DtcCatalog
from obd_diag.i18n import LANGUAGES, N_, set_language, system_language, tr, trn
from obd_diag.protocol.elm327 import Elm327, ElmError, NoConnectionError
from obd_diag.protocol.obd import FreezeFrame
from obd_diag.protocol.pids import PidSpec
from obd_diag.services.clear import (
    CLEAR_DISABLED_MESSAGE,
    ClearRefused,
    check_preconditions,
    clear_codes,
    clear_enabled,
    clearable_codes,
)
from obd_diag.services.diagnostics import (
    LOW_VOLTAGE,
    DiagnosticCode,
    DtcKind,
    ScanResult,
    add_online_explanations,
    scan,
    scan_to_dict,
)
from obd_diag.services.dtc_online import search_url
from obd_diag.services.live import (
    DEFAULT_INTERVAL,
    LOW_VOLTAGE_INTERVAL,
    LiveRecorder,
    LiveSample,
    SelectionError,
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

LOW_VOLTAGE_WARNING = N_("Batteriespannung niedrig – Ergebnisse können unzuverlässig sein")  # noqa: RUF001

_SECTIONS = (
    (DtcKind.STORED, N_("Gespeichert")),
    (DtcKind.PENDING, N_("Ausstehend")),
    (DtcKind.PERMANENT, N_("Permanent")),
)


def _error(message: object) -> None:
    print(tr("Fehler: {message}").format(message=message), file=sys.stderr)


def _hint(message: object, file: TextIO | None = None) -> None:
    print(tr("Hinweis: {message}").format(message=message), file=file or sys.stderr)


def _labelled(rows: list[tuple[str, str]], indent: str = "") -> None:
    """``Bezeichnung: Wert`` mit bündigen Werten (Breite nach der längsten Bezeichnung)."""
    width = max(len(label) for label, _value in rows) + 1
    for label, value in rows:
        print(f"{indent}{label + ':':<{width}} {value}")


def _print_codes(
    codes: list[DiagnosticCode], manufacturer: str | None = None, *, web_search: bool = False
) -> None:
    """Code und Titel; mit ``web_search`` (``--online-codes``) zu Codes ohne Katalogtext
    die Online-Erklärung und einen Link für die Websuche."""
    for c in codes:
        if c.info is not None:
            print(f"  {c.code}  {c.info.title}")
            continue
        print(f"  {c.code}  {tr('(keine Beschreibung im Katalog)')}")
        if not web_search:
            continue
        if c.online is not None:
            print("         " + tr("Online, ungeprüft: {text}").format(text=c.online.text))
            print(
                "         "
                + tr("Quelle: {source}, {url}").format(source=c.online.source, url=c.online.url)
            )
        print("         " + tr("Im Web suchen: {url}").format(url=search_url(c.code, manufacturer)))


def _print_scan(
    result: ScanResult, manufacturer: str | None = None, *, web_search: bool = False
) -> None:
    voltage = tr("unbekannt") if result.voltage is None else f"{result.voltage:.1f} V"
    _labelled(
        [
            (tr("Adapter"), result.adapter),
            (tr("Protokoll"), result.protocol),
            (tr("Bordspannung"), voltage),
        ]
    )
    if result.low_voltage:
        print(tr("Warnung: {message}").format(message=tr(LOW_VOLTAGE_WARNING)))
    print()
    if not result.codes:
        print(tr("Keine Fehlercodes gespeichert."))
        return
    for kind, heading in _SECTIONS:
        codes = [c for c in result.codes if c.kind is kind]
        if not codes:
            continue
        print(f"{tr(heading)}:")
        _print_codes(codes, manufacturer, web_search=web_search)


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
    print(tr("Mitschnitt: {path}").format(path=path), file=sys.stderr)
    return FileTracingTransport(transport, path, f"{args.port} {args.baud} Baud")


def _open_catalog() -> DtcCatalog | None:
    catalog = DtcCatalog.default()
    if catalog is None:
        _hint(
            tr(
                "Fehlercode-Katalog fehlt, Codes werden ohne Beschreibung angezeigt. "
                "Erzeugen mit: uv run python tools/build_dtc_db.py"
            )
        )
    return catalog


def _run_scan(args: argparse.Namespace) -> None:
    catalog = _open_catalog()
    try:
        with _transport(args) as transport:
            result = scan(Elm327(transport), catalog)
    finally:
        if catalog is not None:
            catalog.close()
    if args.online_codes:  # ohne FIN: nur genormte Codes
        result = add_online_explanations(result, None)
    if args.json:
        print(_scan_json(result))
    else:
        _print_scan(result, web_search=args.online_codes)


def _run_ports() -> None:
    ports = list_ports()
    if not ports:
        print(tr("Keine Adapter gefunden (USB-Seriell oder /dev/rfcomm*)."))
        return
    width = max(len(p.device) for p in ports)
    for p in ports:
        print(f"{p.device:<{width}}  {p.description}")


def _confirm() -> bool:
    try:
        answer = input(tr('Wirklich löschen? Zum Bestätigen "ja" eingeben: '))
    except EOFError:
        return False
    return answer.strip().lower() == tr("ja")


def _run_clear(args: argparse.Namespace) -> int:
    if not clear_enabled():
        _error(tr(CLEAR_DISABLED_MESSAGE))
        return 1
    catalog = _open_catalog()
    try:
        with _transport(args) as transport:
            elm = Elm327(transport)
            preview = scan(elm, catalog)
            _print_scan(preview)
            print()
            codes = clearable_codes(preview)
            if not codes:
                print(
                    tr(
                        "Keine gespeicherten oder ausstehenden Fehlercodes, "
                        "es wird nichts gelöscht."
                    )
                )
                return 0
            check_preconditions(elm)
            print(tr("Folgende Fehlercodes werden im Steuergerät gelöscht:"))
            unique: dict[str, DiagnosticCode] = {}
            for c in codes:
                unique.setdefault(c.code, c)  # gespeichert und ausstehend: nur einmal nennen
            _print_codes(list(unique.values()))
            print(
                tr(
                    "Achtung: Dabei gehen auch Freeze Frame und Readiness-Status verloren.\n"
                    "Die Codes werden vorher gesichert. Ist der Fehler nicht behoben,\n"
                    "kommen sie wieder."
                )
            )
            if not args.yes and not _confirm():
                print(tr("Abgebrochen, nichts gelöscht."))
                return 1
            result = clear_codes(elm, catalog)
    except ClearRefused as e:
        _error(e)
        return 1
    finally:
        if catalog is not None:
            catalog.close()
    print(tr("Gelöscht. Sicherung: {path}").format(path=result.backup_path))
    print()
    print(tr("Kontroll-Scan:"))
    _print_scan(result.after)
    if clearable_codes(result.after):
        print()
        _hint(
            tr("Einige Codes sind sofort wieder da, der Fehler besteht vermutlich noch."),
            file=sys.stdout,
        )
    return 0


_MONITOR_STATES = {
    MonitorState.COMPLETE: N_("abgeschlossen"),
    MonitorState.INCOMPLETE: N_("nicht abgeschlossen"),
    MonitorState.NOT_SUPPORTED: N_("nicht unterstützt"),
}

# Freeze-Frame-Werte: Schlüssel in ``FreezeFrame.values``, Bezeichnung, Einheit
_FREEZE_VALUES = (
    ("engine_load_pct", N_("Motorlast"), "%"),
    ("coolant_temp_c", N_("Kühlmitteltemperatur"), "°C"),
    ("rpm", N_("Drehzahl"), N_("1/min")),
    ("speed_kmh", N_("Geschwindigkeit"), "km/h"),
)


def _print_vehicle(vehicle: VinInfo | None) -> None:
    print(tr("Fahrzeug:"))
    if vehicle is None:
        print("  " + tr("FIN nicht verfügbar (Mode 09 nicht unterstützt oder nicht lesbar)."))
        return
    unknown = tr("unbekannt")
    rows = [(tr("FIN"), vehicle.vin)]
    if not vehicle.valid:
        rows.append((tr("Hinweis"), tr("FIN ungültig (Länge oder Zeichen)")))
    else:
        rows.append((tr("Prüfziffer"), checksum_text(vehicle)))
    rows.append((tr("Hersteller"), tr(vehicle.manufacturer) if vehicle.manufacturer else unknown))
    rows.append((tr("Land"), tr(vehicle.country) if vehicle.country else unknown))
    year = model_year_text(vehicle)
    if year is not None:
        rows.append((tr("Modelljahr"), year))
    rows += [
        (tr(label), vehicle.online[key])
        for key, label in VPIC_FIELDS.items()
        if key in vehicle.online
    ]
    _labelled(rows, "  ")


def _print_readiness(readiness: ReadinessStatus | None) -> None:
    if readiness is None:
        print(tr("Readiness: nicht verfügbar (PID 01 nicht beantwortet)."))
        return
    engine = tr("Diesel-Motor") if readiness.compression_ignition else tr("Otto-Motor")
    print(tr("Readiness ({engine}):").format(engine=engine))
    print(
        "  "
        + tr("Kontrollleuchte (MIL): {state}").format(
            state=tr("an") if readiness.mil_on else tr("aus")
        )
    )
    print("  " + tr("Gemeldete Fehlercodes: {count}").format(count=readiness.dtc_count))
    _labelled([(tr(m.name), tr(_MONITOR_STATES[m.state])) for m in readiness.monitors], "  ")
    complete = tr("ja") if readiness.all_complete else tr("nein")
    print(f"  {tr(ALL_COMPLETE_LABEL)}: {complete}")
    print("  " + tr("Hinweis: {message}").format(message=tr(AU_NOTE)))


def _print_freeze_frame(freeze: FreezeFrame | None) -> None:
    if freeze is None:
        print(tr("Freeze Frame: keiner gespeichert."))
        return
    print(
        tr("Freeze Frame (ausgelöst durch {code}):").format(
            code=freeze.dtc or tr("unbekannten Code")
        )
    )
    for key, label, unit in _FREEZE_VALUES:
        if key in freeze.values:
            print(f"  {tr(label) + ':':<22} {freeze.values[key]:g} {tr(unit)}")


def _print_session(session: Session, *, web_search: bool = False) -> None:
    _print_vehicle(session.vehicle)
    print()
    vehicle = session.vehicle
    manufacturer = vehicle.manufacturer if vehicle is not None else None
    _print_scan(session.scan, manufacturer, web_search=web_search)
    print()
    _print_readiness(session.readiness)
    print()
    _print_freeze_frame(session.freeze_frame)


def _export(session: Session, pdf: Path | None, csv: Path | None) -> None:
    # erst hier importieren: ReportLab wird nur für den Export gebraucht
    from obd_diag.export.report import export_csv, export_pdf

    if pdf is not None:
        export_pdf(session, pdf)
        print(tr("PDF-Bericht: {path}").format(path=pdf), file=sys.stderr)
    if csv is not None:
        export_csv(session, csv)
        print(tr("CSV: {path}").format(path=csv), file=sys.stderr)


def _run_diagnose(args: argparse.Namespace) -> int:
    catalog = _open_catalog()
    try:
        with _transport(args) as transport:
            session = run_diagnosis(
                Elm327(transport),
                catalog,
                online_vin_lookup=args.online_vin,
                online_dtc_lookup=args.online_codes,
            )
    finally:
        if catalog is not None:
            catalog.close()
    if args.json:
        print(json.dumps(session_to_dict(session), ensure_ascii=False, indent=2))
    else:
        _print_session(session, web_search=args.online_codes)
    # Pfade auf stderr, damit stdout bei --json reines JSON bleibt
    try:
        if args.save:
            print(
                tr("Sitzung gespeichert: {path}").format(path=save_session(session)),
                file=sys.stderr,
            )
        _export(session, args.pdf, args.csv)
    except OSError as e:
        _error(f"{e.filename or ''}: {e.strerror or e}")
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
            _error(tr("Fahrzeug liefert keine FIN (Mode 09 PID 02)."))
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
        parser.error(tr("export: --pdf und/oder --csv angeben"))
    # erst hier importieren: ReportLab wird nur für den Export gebraucht
    from obd_diag.export.report import export_csv, export_pdf
    from obd_diag.services.session import load_session

    try:
        session = load_session(args.session)
        if args.pdf is not None:
            export_pdf(session, args.pdf)
            print(tr("PDF-Bericht: {path}").format(path=args.pdf))
        if args.csv is not None:
            export_csv(session, args.csv)
            print(tr("CSV: {path}").format(path=args.csv))
    except OSError as e:
        _error(f"{e.filename or args.session}: {e.strerror or e}")
        return 1
    except ValueError as e:
        _error(e)
        return 1
    return 0


# --- Live-Daten ---------------------------------------------------------------------

LIVE_SAFETY_NOTE = N_("Hinweis: Während der Fahrt nur durch Beifahrer bedienen.")
MIN_INTERVAL = 0.1  # Sekunden; schneller kommt ein ELM327 ohnehin nicht hinterher
_MISSING = "-"  # nicht lesbarer Wert in der Tabelle (kein Gedankenstrich)


def _number(text: str, valid: Callable[[float], bool], requirement: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(tr("keine Zahl: {text}").format(text=repr(text))) from None
    if not valid(value):  # nan erfüllt keinen Vergleich
        raise argparse.ArgumentTypeError(requirement)
    return value


def _interval(text: str) -> float:
    return _number(
        text,
        lambda v: v >= MIN_INTERVAL,
        tr("mindestens {seconds:g} Sekunden").format(seconds=MIN_INTERVAL),
    )


def _duration(text: str) -> float:
    return _number(text, lambda v: v > 0, tr("muss größer als 0 sein"))


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
        self.headers = [
            tr("Zeit (s)"),
            *(f"{p.name} ({tr(p.unit)})" for p in pids),
            tr("Spannung (V)"),
        ]
        self.widths = [max(len(h), 8) for h in self.headers]
        self.rounds = 0
        self.throttled = False

    def print_header(self) -> None:
        print("  ".join(h.rjust(w) for h, w in zip(self.headers, self.widths, strict=True)))

    def on_sample(self, sample: LiveSample) -> None:
        if sample.throttled != self.throttled:
            self.throttled = sample.throttled
            if sample.throttled and sample.voltage is not None:
                _hint(
                    tr(
                        "Bordspannung {voltage:.1f} V unter {minimum:.1f} V, "
                        "Abfrage nur alle {seconds:g} s."
                    ).format(
                        voltage=sample.voltage, minimum=LOW_VOLTAGE, seconds=LOW_VOLTAGE_INTERVAL
                    )
                )
            else:
                _hint(tr("Bordspannung wieder ausreichend."))
        cells = [
            f"{sample.elapsed:.1f}",
            *(_live_number(sample.values.get(p.key)) for p in self.pids),
            _MISSING if sample.voltage is None else f"{sample.voltage:.1f}",
        ]
        print("  ".join(c.rjust(w) for c, w in zip(cells, self.widths, strict=True)), flush=True)
        self.rounds += 1


def _print_available(pids: list[PidSpec]) -> None:
    if not pids:
        print(tr("Das Fahrzeug meldet keine bekannten Live-Werte."))
        return
    key_title, name_title, unit_title = tr("Schlüssel"), tr("Name"), tr("Einheit")
    key_width = max(len(key_title), *(len(p.key) for p in pids))
    name_width = max(len(name_title), *(len(p.name) for p in pids))
    print(f"{key_title:<{key_width}}  {name_title:<{name_width}}  {unit_title}")
    for p in pids:
        print(f"{p.key:<{key_width}}  {p.name:<{name_width}}  {tr(p.unit)}")


def _run_live(args: argparse.Namespace) -> int:
    keys = None if args.pids is None else _keys(args.pids)
    if not args.list:
        print(tr(LIVE_SAFETY_NOTE), file=sys.stderr)
    table: _LiveTable | None = None
    record: Path | None = None
    try:
        with _transport(args) as transport:
            elm = Elm327(transport)
            setup = prepare_live(elm)
            if args.list:
                _print_available(setup.available)
                return 0
            try:
                pids = select_pids(setup, keys)
            except SelectionError as e:
                _error(e)
                if keys is None:
                    print(tr("Mit --list anzeigen, mit --pids wählen."), file=sys.stderr)
                return 1
            if args.record is not None:
                record = (
                    new_recording_path(recording_dir())
                    if args.record == _AUTO_RECORD
                    else Path(args.record)
                )
            print(
                tr("Adapter: {adapter}, Protokoll: {protocol}").format(
                    adapter=setup.adapter, protocol=setup.protocol
                ),
                file=sys.stderr,
            )
            recorder = None if record is None else LiveRecorder(record, pids)
            if record is not None:
                print(tr("Aufzeichnung: {path}").format(path=record), file=sys.stderr)
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
        _error(tr("Unerwartete Antwort vom Fahrzeug: {error}").format(error=e))
        return 1
    except OSError as e:
        _error(f"{e.filename or ''}: {e.strerror or e}")
        return 1
    rounds = 0 if table is None else table.rounds
    print(
        trn("Beendet nach {n} Runde.", "Beendet nach {n} Runden.", rounds).format(n=rounds),
        file=sys.stderr,
    )
    if record is not None and table is not None:
        print(tr("Aufzeichnung: {path}").format(path=record), file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Alle Befehle und Optionen; auch Quelle der CLI-Referenz in der Dokumentation.

    Die Hilfetexte stehen in der beim Aufruf eingestellten Sprache (``obd_diag.i18n``).
    """
    parser = argparse.ArgumentParser(prog="obd-diag")
    parser.add_argument("--version", action="version", version=__version__)
    lang_help = tr(
        "Sprache der Ausgabe und der Fehlercode-Texte (Standard: wie die Systemsprache, "
        "Deutsch nur bei deutscher Systemsprache)"
    )
    parser.add_argument("--lang", choices=LANGUAGES, help=lang_help)
    sub = parser.add_subparsers(dest="command", required=True)

    # Auch nach dem Befehl erlaubt (``obd-diag scan --lang en``); SUPPRESS, damit ein
    # fehlendes ``--lang`` dort die Angabe vor dem Befehl nicht überschreibt.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--lang", choices=LANGUAGES, default=argparse.SUPPRESS, help=lang_help)

    file_metavar = tr("DATEI")
    connection = argparse.ArgumentParser(add_help=False, parents=[common])
    connection.add_argument("--port", default="/dev/ttyUSB0")
    connection.add_argument("--baud", type=int, default=38400)
    connection.add_argument(
        "--trace",
        nargs="?",
        const=_AUTO_TRACE,
        metavar=file_metavar,
        help=tr(
            "Adapter-Kommunikation mitschneiden (ohne DATEI: unter "
            "$XDG_DATA_HOME/obd-diag/traces); enthält ggf. die FIN"
        ),
    )
    pdf_metavar, csv_metavar = tr("DATEI.pdf"), tr("DATEI.csv")
    json_help = tr("Ergebnis als JSON ausgeben")

    sub.add_parser(
        "info", parents=[connection], help=tr("Adapter-Version und Bordspannung anzeigen")
    )
    scan_parser = sub.add_parser(
        "scan",
        parents=[connection],
        help=tr("Fehlercodes lesen (gespeichert, ausstehend, permanent)"),
    )
    scan_parser.add_argument("--json", action="store_true", help=json_help)
    online_codes_help = tr(
        "Codes ohne Katalogtext online nachschlagen (ungeprüfte Kurzbeschreibung, "
        "englisch); lädt eine Datei der Quelle, sendet weder Code noch FIN"
    )
    scan_parser.add_argument("--online-codes", action="store_true", help=online_codes_help)

    clear_parser = sub.add_parser(
        "clear",
        parents=[connection],
        help=tr("Fehlercodes löschen (Mode 04), vorher sichern; nur bei Motor aus, Zündung an"),
    )
    clear_parser.add_argument("--yes", action="store_true", help=tr("ohne Rückfrage löschen"))
    online_help = tr(
        "FIN zusätzlich bei NHTSA vPIC (USA) nachschlagen; sendet die FIN ins Internet, "
        "Ergebnis wird lokal gecacht"
    )
    diagnose_parser = sub.add_parser(
        "diagnose",
        parents=[connection],
        help=tr("vollständige Diagnose: Fehlercodes, Readiness, Freeze Frame, FIN (nur lesend)"),
    )
    diagnose_parser.add_argument(
        "--json", action="store_true", help=tr("Sitzung als JSON ausgeben")
    )
    diagnose_parser.add_argument("--online-vin", action="store_true", help=online_help)
    diagnose_parser.add_argument("--online-codes", action="store_true", help=online_codes_help)
    diagnose_parser.add_argument(
        "--save",
        action="store_true",
        help=tr("Sitzung unter $XDG_DATA_HOME/obd-diag/sessions sichern"),
    )
    pdf_help, csv_help = tr("PDF-Bericht"), tr("CSV, eine Zeile pro Fehlercode")
    diagnose_parser.add_argument("--pdf", type=Path, metavar=pdf_metavar, help=pdf_help)
    diagnose_parser.add_argument("--csv", type=Path, metavar=csv_metavar, help=csv_help)
    vin_parser = sub.add_parser(
        "vin", parents=[connection], help=tr("FIN lesen (Mode 09) und dekodieren")
    )
    vin_parser.add_argument(
        "vin", nargs="?", metavar=tr("FIN"), help=tr("diese FIN dekodieren, ohne Adapter")
    )
    vin_parser.add_argument("--json", action="store_true", help=json_help)
    vin_parser.add_argument("--online-vin", action="store_true", help=online_help)
    live_parser = sub.add_parser(
        "live",
        parents=[connection],
        help=tr("Live-Daten (Mode 01) fortlaufend anzeigen und aufzeichnen; Ende mit Strg+C"),
    )
    live_parser.add_argument(
        "--pids",
        metavar=tr("SCHLÜSSEL,..."),
        help=tr(
            "Werte, z. B. rpm,speed,coolant_temp (Standard: die üblichen, soweit "
            "unterstützt; Schlüssel zeigt --list)"
        ),
    )
    live_parser.add_argument(
        "--list",
        action="store_true",
        help=tr("unterstützte Werte mit Schlüssel anzeigen und beenden"),
    )
    seconds_metavar = tr("SEK")
    live_parser.add_argument(
        "--interval",
        type=_interval,
        default=DEFAULT_INTERVAL,
        metavar=seconds_metavar,
        help=tr("Abstand der Abfragerunden (Standard {default:g}, mindestens {minimum:g})").format(
            default=DEFAULT_INTERVAL, minimum=MIN_INTERVAL
        ),
    )
    live_parser.add_argument(
        "--duration",
        type=_duration,
        metavar=seconds_metavar,
        help=tr("nach SEK Sekunden beenden"),
    )
    live_parser.add_argument(
        "--record",
        nargs="?",
        const=_AUTO_RECORD,
        metavar=csv_metavar,
        help=tr(
            "als CSV aufzeichnen (ohne DATEI: unter $XDG_DATA_HOME/obd-diag/recordings); "
            "vorhandene Dateien werden nicht überschrieben"
        ),
    )
    sub.add_parser("ports", parents=[common], help=tr("angeschlossene Adapter auflisten"))
    export_parser = sub.add_parser(
        "export",
        parents=[common],
        help=tr("gespeicherte Diagnosesitzung (JSON) als PDF-Bericht oder CSV ausgeben"),
    )
    export_parser.add_argument("session", type=Path, metavar=tr("SESSION.json"))
    export_parser.add_argument("--pdf", type=Path, metavar=pdf_metavar, help=pdf_help)
    export_parser.add_argument("--csv", type=Path, metavar=csv_metavar, help=csv_help)
    # für Fehlermeldungen mit der Aufrufzeile von ``export``
    export_parser.set_defaults(export_parser=export_parser)
    return parser


def language_from_argv(argv: list[str]) -> str:
    """Sprache laut ``--lang`` (die letzte Angabe zählt), sonst die Systemsprache.

    Vorab gelesen, weil schon die Hilfetexte des Parsers in dieser Sprache stehen.
    """
    found = None
    for i, arg in enumerate(argv):
        if arg == "--lang" and i + 1 < len(argv):
            found = argv[i + 1]
        elif arg.startswith("--lang="):
            found = arg.removeprefix("--lang=")
    return found if found in LANGUAGES else system_language()


def main(argv: list[str] | None = None) -> int:
    args_list = sys.argv[1:] if argv is None else argv
    set_language(language_from_argv(args_list))
    parser = build_parser()
    args = parser.parse_args(args_list)
    try:
        if args.command == "info":
            with _transport(args) as transport:
                elm = Elm327(transport)
                adapter = elm.initialize()
                voltage = f"{elm.voltage():.1f} V"
                _labelled([(tr("Adapter"), adapter), (tr("Bordspannung"), voltage)])
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
            return _run_export(args, args.export_parser)
    except (TransportError, ElmError) as e:
        _error(e)
        if isinstance(e, NoConnectionError):
            _hint(tr(NoConnectionError.HINT))
        return 1
    return 0
