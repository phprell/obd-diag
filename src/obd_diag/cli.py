"""Kommandozeile: ``obd-diag``."""

import argparse
import dataclasses
import json
import sys
import textwrap
import time
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from obd_diag import __version__
from obd_diag.data.dtc_catalog import DtcCatalog
from obd_diag.i18n import LANGUAGES, N_, set_language, system_language, tr, trn
from obd_diag.protocol.elm327 import Elm327, ElmError, NoConnectionError
from obd_diag.protocol.monitor_tests import (
    STATUS_LABELS,
    TestResult,
    TestStatus,
    mid_name,
    read_test_results,
    tid_name,
)
from obd_diag.protocol.obd import FreezeFrame
from obd_diag.protocol.pids import PidSpec
from obd_diag.protocol.vehicle_info import EcuInfo, read_ecu_info
from obd_diag.services.adapter_check import (
    UDS_NOTE,
    check_adapter,
    kind_text,
    parameters_text,
    probe_text,
    report_to_dict,
    uds_text,
    version_text,
    voltage_text,
)
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
from obd_diag.services.readiness_assist import (
    CYCLE_STATE_LABELS,
    ReadinessCheck,
    check_readiness,
    check_to_dict,
)
from obd_diag.services.session import Session, run_diagnosis, save_session, session_to_dict
from obd_diag.services.setup_check import SetupReport, Status, run_setup_check
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
from obd_diag.transport.demo import DEMO_NOTICE, DEMO_PORT, DemoTransport
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
    """Serieller Transport aus ``--port``/``--baud``, bei ``--trace`` mit Mitschnitt;
    mit ``--demo`` (oder ``--port demo``) das Demo-Fahrzeug statt eines Adapters."""
    demo = args.demo or args.port == DEMO_PORT
    transport: Transport
    if demo:
        _hint(tr(DEMO_NOTICE))
        transport, label = DemoTransport(), tr("Demo-Fahrzeug")
    else:
        transport, label = SerialTransport(args.port, args.baud), f"{args.port} {args.baud} Baud"
    if args.trace is None:
        return transport
    path = new_trace_path(trace_dir()) if args.trace == _AUTO_TRACE else Path(args.trace)
    print(tr("Mitschnitt: {path}").format(path=path), file=sys.stderr)
    return FileTracingTransport(transport, path, label)


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


def _run_adapter(args: argparse.Namespace) -> None:
    with _transport(args) as transport:
        report = check_adapter(Elm327(transport))
    if args.json:
        print(json.dumps(report_to_dict(report), ensure_ascii=False, indent=2))
        return
    _labelled(
        [
            (tr("Adapter"), report.identification),
            (tr("Beschreibung"), report.description or tr("nicht lesbar (AT@1)")),
            (tr("Version"), version_text(report)),
            (tr("Urteil"), kind_text(report)),
            (tr("Bordspannung"), voltage_text(report)),
            (tr("Parameter"), parameters_text(report)),
            (tr("Für UDS (v0.3)"), uds_text(report)),
        ]
    )
    print()
    print(tr("Geprüfte Befehle:"))
    _labelled([(r.probe.command, probe_text(r)) for r in report.results], indent="  ")
    print()
    _hint(tr(UDS_NOTE), file=sys.stdout)


_STATUS_LABELS = {
    Status.OK: N_("OK"),
    Status.WARNING: N_("Warnung"),
    Status.ERROR: N_("Fehler"),
    Status.INFO: N_("Hinweis"),
}


def _print_setup(report: SetupReport) -> None:
    labels = {status: tr(label) for status, label in _STATUS_LABELS.items()}
    width = max(len(label) for label in labels.values())
    indent = " " * (width + 4)
    for item in report.items:
        print(f"  {labels[item.status]:<{width}}  {item.title}")
        if item.detail:
            print(textwrap.fill(item.detail, 100, initial_indent=indent, subsequent_indent=indent))
        if item.fix:
            print(indent + tr("Abhilfe: {command}").format(command=item.fix))
    print()
    adapter = report.adapter
    if adapter is None:
        print(tr("Ergebnis: kein nutzbarer Adapter gefunden."))
        return
    command = f"obd-diag diagnose --port {adapter.port} --baud {adapter.baud}"
    if report.ok:
        print(tr("Ergebnis: bereit. Weiter mit: {command}").format(command=command))
    else:
        print(tr("Ergebnis: Adapter antwortet, aber nicht alles ist eingerichtet."))


def _run_setup_check(args: argparse.Namespace) -> int:
    report = run_setup_check(args.port)
    _print_setup(report)
    return 0 if report.adapter is not None else 1


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
    if not readiness.all_complete:
        print("  " + tr("Welche Fahrt die offenen Tests abschließt: obd-diag readiness"))


def _print_readiness_check(check: ReadinessCheck) -> None:
    """Ausgabe von ``obd-diag readiness``: Stand seit dem Löschen und in dieser Fahrt."""
    _labelled([(tr("Adapter"), check.adapter), (tr("Protokoll"), check.protocol)])
    print()
    readiness, cycle = check.readiness, check.drive_cycle
    if readiness is None:
        print(tr("Readiness: nicht verfügbar (PID 01 nicht beantwortet)."))
        return
    engine = tr("Diesel-Motor") if readiness.compression_ignition else tr("Otto-Motor")
    print(tr("Readiness-Assistent ({engine}):").format(engine=engine))
    mil = tr("an") if readiness.mil_on else tr("aus")
    print("  " + tr("Kontrollleuchte (MIL): {state}").format(state=mil))
    print("  " + tr("Gemeldete Fehlercodes: {count}").format(count=readiness.dtc_count))
    rows = []
    for m in readiness.monitors:
        since = tr(_MONITOR_STATES[m.state])
        state = None if cycle is None else cycle.state(m.key)
        if m.state is MonitorState.NOT_SUPPORTED or state is None:
            rows.append((tr(m.name), since))
        else:
            rows.append((tr(m.name), f"{since:<20} {tr(CYCLE_STATE_LABELS[state])}"))
    label_width = max(len(label) for label, _ in rows) + 1
    header = f"{tr('Seit dem Löschen'):<20}"
    if cycle is not None:
        header += " " + tr("In dieser Fahrt")
    print(f"  {'':<{label_width}} {header}")
    _labelled(rows, "  ")
    complete = tr("ja") if readiness.all_complete else tr("nein")
    print(f"  {tr(ALL_COMPLETE_LABEL)}: {complete}")
    advice = check.advice
    if advice:
        print()
        print(tr("Offene Tests und welche Fahrt sie abschließt:"))
        for a in advice:
            title = tr(a.name)
            if a.cycle is not None:
                title += (
                    " ("
                    + tr("in dieser Fahrt: {state}").format(state=tr(CYCLE_STATE_LABELS[a.cycle]))
                    + ")"
                )
            print("  " + title)
            print(
                textwrap.fill(tr(a.hint), width=88, initial_indent="    ", subsequent_indent="    ")
            )
    print()
    for note in [*check.notes, AU_NOTE]:
        print(textwrap.fill(tr("Hinweis: {message}").format(message=tr(note)), width=88))


def _run_readiness(args: argparse.Namespace) -> None:
    with _transport(args) as transport:
        check = check_readiness(Elm327(transport))
    if args.json:
        print(json.dumps(check_to_dict(check), ensure_ascii=False, indent=2))
    else:
        _print_readiness_check(check)


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


def _print_ecus(ecus: tuple[EcuInfo, ...] | list[EcuInfo] | None) -> None:
    if ecus is None:
        print(tr("Steuergeräte (Mode 09): nicht verfügbar."))
        return
    print(tr("Steuergeräte (Mode 09):"))
    for ecu in ecus:
        print(f"  {ecu.ecu}" + (f"  {ecu.name}" if ecu.name else ""))
        rows = [(tr("Kalibrierungs-ID"), c) for c in ecu.calibration_ids]
        rows += [(tr("CVN"), c) for c in ecu.cvns]
        ipt = ecu.performance
        if ipt is not None:
            rows.append(
                (
                    tr("Überwachungszähler"),
                    tr("{cycles} Fahrzyklen, {ignitions} Zündungen").format(
                        cycles=ipt.obd_conditions, ignitions=ipt.ignition_cycles
                    ),
                )
            )
            for counter in ipt.counters:
                ratio = "" if counter.ratio is None else f" ({counter.ratio:.2f})"
                rows.append(
                    (
                        "  " + tr(counter.name),
                        tr("{completed} von {conditions}").format(
                            completed=counter.completed, conditions=counter.conditions
                        )
                        + ratio,
                    )
                )
        if not rows:
            rows.append((tr("Hinweis"), tr("keine weiteren Angaben")))
        _labelled(rows, "    ")


def _measure(test: TestResult, value: float | None, raw: int) -> str:
    if value is None:
        return tr("{raw} (Rohwert)").format(raw=raw)
    text = f"{value:.{test.decimals}f}"
    return f"{text} {test.unit}" if test.unit else text


def _print_tests(tests: tuple[TestResult, ...] | list[TestResult] | None) -> None:
    if tests is None:
        print(tr("Testergebnisse (Mode 06): nicht verfügbar."))
        return
    failed = sum(t.status is TestStatus.FAILED for t in tests)
    print(
        trn(
            "Testergebnisse (Mode 06, {n} Test, davon {failed} nicht bestanden):",
            "Testergebnisse (Mode 06, {n} Tests, davon {failed} nicht bestanden):",
            len(tests),
        ).format(n=len(tests), failed=failed)
    )
    ecu = None
    for test in tests:
        if test.ecu != ecu:
            ecu = test.ecu
            print("  " + tr("Steuergerät {ecu}").format(ecu=ecu))
        limits = tr("Grenzen {low} bis {high}").format(
            low=_measure(test, test.minimum, test.min_raw),
            high=_measure(test, test.maximum, test.max_raw),
        )
        print(f"    {mid_name(test.mid)} · {tid_name(test.tid)}")
        if test.status is TestStatus.NO_RESULT:
            print(f"      {tr(STATUS_LABELS[test.status])}")
            continue
        print(
            f"      {_measure(test, test.value, test.value_raw)}  ({limits})  "
            f"{tr(STATUS_LABELS[test.status])}"
        )


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
    print()
    _print_ecus(session.ecus)
    print()
    _print_tests(session.tests)


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


def _run_ecus(args: argparse.Namespace) -> int:
    with _transport(args) as transport:
        elm = Elm327(transport)
        elm.initialize()
        elm.protocol()
        ecus = read_ecu_info(elm)
    if args.json:
        data = None if ecus is None else [dataclasses.asdict(e) for e in ecus]
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        _print_ecus(ecus)
    return 0 if ecus is not None else 1


def _run_tests(args: argparse.Namespace) -> int:
    with _transport(args) as transport:
        elm = Elm327(transport)
        elm.initialize()
        elm.protocol()
        tests = read_test_results(elm)
    if args.json:
        data = None if tests is None else [_test_json(t) for t in tests]
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        _print_tests(tests)
    return 0 if tests is not None else 1


def _test_json(test: TestResult) -> dict[str, object]:
    return {
        **dataclasses.asdict(test),
        "value": test.value,
        "min": test.minimum,
        "max": test.maximum,
        "unit": test.unit,
        "status": test.status.value,
    }


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
        "--demo",
        action="store_true",
        help=tr(
            "Demo-Fahrzeug statt Adapter: Beispieldaten eines Mercedes A 180 d, zum "
            "Ausprobieren ohne Auto (gleich wie --port demo)"
        ),
    )
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
    adapter_parser = sub.add_parser(
        "adapter",
        parents=[connection],
        help=tr(
            "Adapter-Check: Version, Spannung und unterstützte Befehle prüfen, Urteil für "
            "UDS (nur Adapter-Befehle, das Fahrzeug wird nicht angesprochen)"
        ),
    )
    adapter_parser.add_argument("--json", action="store_true", help=json_help)
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
        help=tr(
            "vollständige Diagnose: Fehlercodes, Readiness, Freeze Frame, FIN, Steuergeräte "
            "und Testergebnisse (nur lesend)"
        ),
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
    readiness_parser = sub.add_parser(
        "readiness",
        parents=[connection],
        help=tr(
            "Readiness-Assistent: offene Tests seit dem Löschen und in dieser Fahrt "
            "(PID 01 und 41) mit Hinweisen, welche Fahrt sie abschließt"
        ),
    )
    readiness_parser.add_argument("--json", action="store_true", help=json_help)
    vin_parser = sub.add_parser(
        "vin", parents=[connection], help=tr("FIN lesen (Mode 09) und dekodieren")
    )
    vin_parser.add_argument(
        "vin", nargs="?", metavar=tr("FIN"), help=tr("diese FIN dekodieren, ohne Adapter")
    )
    vin_parser.add_argument("--json", action="store_true", help=json_help)
    vin_parser.add_argument("--online-vin", action="store_true", help=online_help)
    ecus_parser = sub.add_parser(
        "ecus",
        parents=[connection],
        help=tr("Steuergeräte (Mode 09): Name, Kalibrierungs-IDs, CVN und Überwachungszähler"),
    )
    ecus_parser.add_argument("--json", action="store_true", help=json_help)
    tests_parser = sub.add_parser(
        "tests",
        parents=[connection],
        help=tr("Testergebnisse der Abgas-Eigendiagnose mit Grenzwerten (Mode 06, nur CAN)"),
    )
    tests_parser.add_argument("--json", action="store_true", help=json_help)
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
    setup_parser = sub.add_parser(
        "setup-check",
        parents=[common],
        help=tr(
            "Einrichtung prüfen: Adapter finden, Zugriffsrechte und Baudrate (38400, 115200); "
            "sendet nur Adapter-Befehle (ATZ ...)"
        ),
    )
    setup_parser.add_argument(
        "--port", help=tr("nur diesen Port prüfen (Standard: alle gefundenen Adapter)")
    )
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
        elif args.command == "adapter":
            _run_adapter(args)
        elif args.command == "scan":
            _run_scan(args)
        elif args.command == "clear":
            return _run_clear(args)
        elif args.command == "diagnose":
            return _run_diagnose(args)
        elif args.command == "readiness":
            _run_readiness(args)
        elif args.command == "vin":
            return _run_vin(args)
        elif args.command == "ecus":
            return _run_ecus(args)
        elif args.command == "tests":
            return _run_tests(args)
        elif args.command == "live":
            return _run_live(args)
        elif args.command == "ports":
            _run_ports()
        elif args.command == "setup-check":
            return _run_setup_check(args)
        elif args.command == "export":
            return _run_export(args, args.export_parser)
    except (TransportError, ElmError) as e:
        _error(e)
        if isinstance(e, NoConnectionError):
            _hint(tr(NoConnectionError.HINT))
        return 1
    return 0
