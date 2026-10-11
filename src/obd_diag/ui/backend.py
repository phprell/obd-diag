"""Anbindung der Oberfläche an Transport und Services.

Ein Job öffnet den seriellen Port, erledigt seine Arbeit und schließt den Port
wieder; zwischen Diagnose und Löschen bleibt nichts offen. So kann das Kabel zwischen
zwei Aktionen abgezogen werden, ohne dass die Oberfläche einen toten Port hält,
und jeder Job beginnt mit einem frisch initialisierten Adapter.

Auch der Fehlercode-Katalog wird je Job geöffnet: SQLite-Verbindungen gehören dem
Thread, der sie angelegt hat, und Jobs laufen nicht im GUI-Thread.
"""

from collections.abc import Callable, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from obd_diag.data.dtc_catalog import DtcCatalog
from obd_diag.export.report import export_csv, export_pdf
from obd_diag.i18n import tr
from obd_diag.protocol.elm327 import Elm327
from obd_diag.protocol.pids import PidSpec
from obd_diag.services.adapter_check import AdapterReport, check_adapter
from obd_diag.services.battery import BatteryProgress, BatteryTestResult, run_battery_test
from obd_diag.services.clear import ClearResult, clear_codes
from obd_diag.services.live import (
    LiveRecorder,
    LiveSample,
    LiveSetup,
    new_recording_path,
    prepare_live,
    recording_dir,
    run_live,
    select_pids,
)
from obd_diag.services.readiness_assist import ReadinessCheck, check_readiness
from obd_diag.services.session import Session, load_session, run_diagnosis, save_session
from obd_diag.services.storage import trace_dir
from obd_diag.transport.demo import DEMO_DESCRIPTION, DEMO_PORT
from obd_diag.transport.discovery import PortInfo, list_ports
from obd_diag.transport.trace import new_trace_path, open_serial


def _save_default(session: Session) -> Path:
    return save_session(session)


def _no_tracing(enabled: bool) -> None:
    pass


@dataclass(frozen=True)
class LiveResult:
    """Ergebnis einer beendeten Live-Abfrage."""

    samples: int  # Anzahl der Abfragerunden
    recording: Path | None  # CSV-Aufzeichnung, None ohne Aufzeichnung


class LiveFunction(Protocol):
    """Live-Daten lesen, bis ``should_stop`` True liefert; läuft im Worker-Thread.

    Die Rückrufe kommen aus dem Worker-Thread: ``on_setup`` nach der Initialisierung
    (unterstützte Werte), ``on_start`` mit den tatsächlich abgefragten Werten und dem
    Pfad der Aufzeichnung, ``on_sample`` nach jeder Runde. ``skip_unsupported`` wie
    bei ``select_pids``.
    """

    def __call__(
        self,
        port: str,
        baud: int,
        keys: Sequence[str] | None,
        interval: float,
        record: bool,
        *,
        on_setup: Callable[[LiveSetup], None],
        on_start: Callable[[list[PidSpec], Path | None], None],
        on_sample: Callable[[LiveSample], None],
        should_stop: Callable[[], bool],
        skip_unsupported: bool = False,
    ) -> LiveResult: ...


def _no_live(*args: Any, **kwargs: Any) -> LiveResult:
    raise NotImplementedError(tr("Live-Daten sind in diesem Backend nicht verfügbar"))


def _no_adapter_check(port: str, baud: int) -> AdapterReport:
    raise NotImplementedError(tr("Der Adapter-Check ist in diesem Backend nicht verfügbar"))


class BatteryFunction(Protocol):
    """Batterie- und Anlasstest; läuft im Worker-Thread, ``on_progress`` kommt von dort."""

    def __call__(
        self,
        port: str,
        baud: int,
        *,
        on_progress: Callable[[BatteryProgress], None],
        should_stop: Callable[[], bool],
    ) -> BatteryTestResult: ...


def _no_battery(*args: Any, **kwargs: Any) -> BatteryTestResult:
    raise NotImplementedError(tr("Der Batterietest ist in diesem Backend nicht verfügbar"))


def _no_readiness_check(port: str, baud: int) -> ReadinessCheck:
    raise NotImplementedError(tr("Der Readiness-Assistent ist in diesem Backend nicht verfügbar"))


@dataclass(frozen=True)
class Backend:
    """Die Aktionen, die das View-Model braucht; Tests setzen eigene Funktionen ein.

    ``diagnose`` (Port, Baudrate, FIN bzw. Fehlercodes online nachschlagen) und
    ``clear`` blockieren und laufen im Worker-Thread, ebenso ``export_pdf``/``export_csv``
    (ReportLab braucht etwas). ``list_ports``, ``catalog_available``, ``save_session`` und
    ``load_session`` sind schnell und laufen im GUI-Thread.
    """

    diagnose: Callable[[str, int, bool, bool], Session]
    clear: Callable[[str, int], ClearResult]
    list_ports: Callable[[], list[PortInfo]]
    catalog_available: Callable[[], bool]
    save_session: Callable[[Session], Path] = _save_default
    load_session: Callable[[Path], Session] = load_session
    export_pdf: Callable[[Session, Path], None] = export_pdf
    export_csv: Callable[[Session, Path], None] = export_csv
    # Adapter-Mitschnitt für folgende Jobs ein-/ausschalten (siehe transport/trace.py)
    set_tracing: Callable[[bool], None] = _no_tracing
    # Live-Daten (blockiert bis zum Stopp, Worker-Thread); siehe ``live_port``
    live: LiveFunction = _no_live
    # Adapter-Check (Worker-Thread); siehe ``adapter_port``
    check_adapter: Callable[[str, int], AdapterReport] = _no_adapter_check
    # Batterie- und Anlasstest (Worker-Thread); siehe ``battery_port``
    battery_test: BatteryFunction = _no_battery
    # Readiness-Assistent: PID 01 und 41 (Worker-Thread); siehe ``readiness_port``
    check_readiness: Callable[[str, int], ReadinessCheck] = _no_readiness_check


def _trace_path(trace: bool) -> Path | None:
    return new_trace_path(trace_dir()) if trace else None


def diagnose_port(
    port: str,
    baud: int,
    online_vin_lookup: bool = False,
    online_dtc_lookup: bool = False,
    lang: str | None = None,
    trace: bool = False,
) -> Session:
    """Scan, Readiness, Freeze Frame und FIN; nur lesend. ``lang`` ohne Angabe: die
    eingestellte Sprache."""
    catalog = DtcCatalog.default()
    try:
        with open_serial(port, baud, _trace_path(trace)) as transport:
            return run_diagnosis(
                Elm327(transport),
                catalog,
                lang,
                online_vin_lookup=online_vin_lookup,
                online_dtc_lookup=online_dtc_lookup,
            )
    finally:
        if catalog is not None:
            catalog.close()


def clear_port(port: str, baud: int, lang: str | None = None, trace: bool = False) -> ClearResult:
    catalog = DtcCatalog.default()
    try:
        with open_serial(port, baud, _trace_path(trace)) as transport:
            return clear_codes(Elm327(transport), catalog, lang=lang)
    finally:
        if catalog is not None:
            catalog.close()


def adapter_port(port: str, baud: int, trace: bool = False) -> AdapterReport:
    """Adapter-Check: nur Adapter-Befehle, das Fahrzeug wird nicht angesprochen."""
    with open_serial(port, baud, _trace_path(trace)) as transport:
        return check_adapter(Elm327(transport))


def battery_port(
    port: str,
    baud: int,
    *,
    on_progress: Callable[[BatteryProgress], None],
    should_stop: Callable[[], bool],
    trace: bool = False,
) -> BatteryTestResult:
    """Batterie- und Anlasstest (``services.battery``); nur lesend. Das Demo-Fahrzeug
    steht dafür zuerst und springt von selbst an."""
    with open_serial(port, baud, _trace_path(trace), engine_start=True) as transport:
        return run_battery_test(Elm327(transport), on_progress=on_progress, should_stop=should_stop)


def readiness_port(port: str, baud: int, trace: bool = False) -> ReadinessCheck:
    """Readiness seit dem Löschen und in dieser Fahrt (PID 01 und 41); nur lesend."""
    with open_serial(port, baud, _trace_path(trace)) as transport:
        return check_readiness(Elm327(transport))


def live_port(
    port: str,
    baud: int,
    keys: Sequence[str] | None,
    interval: float,
    record: bool,
    *,
    on_setup: Callable[[LiveSetup], None],
    on_start: Callable[[list[PidSpec], Path | None], None],
    on_sample: Callable[[LiveSample], None],
    should_stop: Callable[[], bool],
    skip_unsupported: bool = False,
    trace: bool = False,
) -> LiveResult:
    """Live-Werte (Mode 01) lesen, bis ``should_stop`` True liefert; nur lesend.

    ``keys`` und ``skip_unsupported`` wie bei ``select_pids`` (``None``: die üblichen
    Werte, soweit unterstützt; keiner davon: ``SelectionError``). Mit ``record`` wird
    jede Runde sofort in eine neue CSV-Datei geschrieben. Wird schon während der
    Initialisierung gestoppt, endet der Job ohne Abfrage und ohne Aufzeichnung.
    """
    with open_serial(port, baud, _trace_path(trace)) as transport:
        elm = Elm327(transport)
        setup = prepare_live(elm)
        on_setup(setup)
        pids = select_pids(setup, keys, skip_unsupported=skip_unsupported)
        if should_stop():
            return LiveResult(0, None)
        with ExitStack() as stack:
            path = new_recording_path(recording_dir()) if record else None
            recorder = None if path is None else stack.enter_context(LiveRecorder(path, pids))
            on_start(pids, path)
            count = run_live(
                elm,
                pids,
                on_sample=on_sample,
                should_stop=should_stop,
                interval=interval,
                recorder=recorder,
            )
        return LiveResult(count, path)


def ports_with_demo() -> list[PortInfo]:
    """Gefundene Adapter, dahinter immer das Demo-Fahrzeug (``transport/demo.py``)."""
    return [*list_ports(), PortInfo(DEMO_PORT, tr(DEMO_DESCRIPTION))]


def catalog_available() -> bool:
    return DtcCatalog.default() is not None


def serial_backend() -> Backend:
    # Wird im GUI-Thread gesetzt und beim Start eines Jobs im Worker gelesen.
    tracing = {"enabled": False}

    def set_tracing(enabled: bool) -> None:
        tracing["enabled"] = enabled

    def live(*args: Any, **kwargs: Any) -> LiveResult:
        # Signatur wie ``LiveFunction``; nur der Mitschnitt kommt hinzu.
        return live_port(*args, **kwargs, trace=tracing["enabled"])

    def battery_test(*args: Any, **kwargs: Any) -> BatteryTestResult:
        return battery_port(*args, **kwargs, trace=tracing["enabled"])

    return Backend(
        diagnose=lambda port, baud, online_vin, online_codes: diagnose_port(
            port, baud, online_vin, online_codes, trace=tracing["enabled"]
        ),
        clear=lambda port, baud: clear_port(port, baud, trace=tracing["enabled"]),
        list_ports=ports_with_demo,
        catalog_available=catalog_available,
        set_tracing=set_tracing,
        live=live,
        check_adapter=lambda port, baud: adapter_port(port, baud, trace=tracing["enabled"]),
        battery_test=battery_test,
        check_readiness=lambda port, baud: readiness_port(port, baud, trace=tracing["enabled"]),
    )
