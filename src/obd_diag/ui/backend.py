"""Anbindung der Oberfläche an Transport und Services.

Ein Job öffnet den seriellen Port, erledigt seine Arbeit und schließt den Port
wieder; zwischen Diagnose und Löschen bleibt nichts offen. So kann das Kabel zwischen
zwei Aktionen abgezogen werden, ohne dass die Oberfläche einen toten Port hält,
und jeder Job beginnt mit einem frisch initialisierten Adapter.

Auch der Fehlercode-Katalog wird je Job geöffnet: SQLite-Verbindungen gehören dem
Thread, der sie angelegt hat, und Jobs laufen nicht im GUI-Thread.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from obd_diag.data.dtc_catalog import DtcCatalog
from obd_diag.export.report import export_csv, export_pdf
from obd_diag.protocol.elm327 import Elm327
from obd_diag.protocol.pids import PidSpec
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
from obd_diag.services.session import Session, load_session, run_diagnosis, save_session
from obd_diag.services.storage import trace_dir
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
    Pfad der Aufzeichnung, ``on_sample`` nach jeder Runde.
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
    ) -> LiveResult: ...


def _no_live(
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
) -> LiveResult:
    raise NotImplementedError("Live-Daten sind in diesem Backend nicht verfügbar")


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
    # Live-Daten (blockiert bis zum Stopp, Worker-Thread); siehe ``live_port``
    live: LiveFunction = _no_live


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
    trace: bool = False,
) -> LiveResult:
    """Live-Werte (Mode 01) lesen, bis ``should_stop`` True liefert; nur lesend.

    ``keys`` wie bei ``select_pids`` (``None``: die üblichen Werte, soweit unterstützt).
    Mit ``record`` wird jede Runde sofort in eine neue CSV-Datei geschrieben.
    """
    with open_serial(port, baud, _trace_path(trace)) as transport:
        elm = Elm327(transport)
        setup = prepare_live(elm)
        on_setup(setup)
        pids = select_pids(setup, keys)
        if not record:
            on_start(pids, None)
            count = run_live(
                elm,
                pids,
                on_sample=on_sample,
                should_stop=should_stop,
                interval=interval,
            )
            return LiveResult(count, None)
        path = new_recording_path(recording_dir())
        with LiveRecorder(path, pids) as recorder:
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


def catalog_available() -> bool:
    return DtcCatalog.default() is not None


def serial_backend() -> Backend:
    # Wird im GUI-Thread gesetzt und beim Start eines Jobs im Worker gelesen.
    tracing = {"enabled": False}

    def set_tracing(enabled: bool) -> None:
        tracing["enabled"] = enabled

    def live(
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
    ) -> LiveResult:
        return live_port(
            port,
            baud,
            keys,
            interval,
            record,
            on_setup=on_setup,
            on_start=on_start,
            on_sample=on_sample,
            should_stop=should_stop,
            trace=tracing["enabled"],
        )

    return Backend(
        diagnose=lambda port, baud, online: diagnose_port(
            port, baud, online, trace=tracing["enabled"]
        ),
        clear=lambda port, baud: clear_port(port, baud, trace=tracing["enabled"]),
        list_ports=list_ports,
        catalog_available=catalog_available,
        set_tracing=set_tracing,
        live=live,
    )
