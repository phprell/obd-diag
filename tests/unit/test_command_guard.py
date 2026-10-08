"""Was an Adapter und Fahrzeug gesendet wird: Freigabeliste, Byte-Format und die exakte
Befehlsfolge jeder Funktion, die mit dem Fahrzeug spricht.

Die erwarteten Folgen sind gegen das ELM327-Datenblatt und SAE J1979 geprüft, nicht
aus dem Code abgeschrieben:

- ``ATZ`` Reset, ``ATE0``/``ATL0``/``ATS0``/``ATH0`` Echo, Zeilenvorschub, Leerzeichen,
  Header aus, ``ATSP0`` Protokoll automatisch, ``ATRV`` Spannung, ``ATDPN``/``ATDP``
  Protokollnummer/-name: Adapter-Befehle, nichts davon erreicht das Fahrzeug.
- ``0100`` unterstützte PIDs, ``0101`` Monitorstatus, ``010C`` Drehzahl (Service $01).
- ``02 PID 00``: Freeze Frame, Frame-Nummer 00 (Service $02).
- ``03``/``07``/``0A``: gespeicherte/ausstehende/permanente Codes, ohne Parameter.
- ``0902``: FIN (Service $09, InfoType 02).
- ``04``: Löschen (Service $04), der einzige schreibende Befehl.
"""

import contextlib
import os
import re
from collections.abc import Callable
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from obd_diag import cli
from obd_diag.data.dtc_catalog import DtcCatalog
from obd_diag.protocol import obd, pids
from obd_diag.protocol.elm327 import (
    AT_COMMANDS,
    CLEAR_COMMAND,
    Elm327,
    ElmError,
    ForbiddenCommandError,
    is_read_only,
)
from obd_diag.services import clear, diagnostics, readiness, session, vehicle
from obd_diag.services.clear import ClearRefused
from obd_diag.transport import TransportError
from tests.fakes import CAN_CAR_ENGINE_OFF, CAN_CAR_FULL, CLEARED, FakeTransport

INIT = ["ATZ", "ATE0", "ATL0", "ATS0", "ATH0", "ATSP0"]
PROTOCOL = ["0100", "ATDPN", "ATDP"]
DTCS = ["03", "07", "0A"]
SCAN = [*INIT, "ATRV", *PROTOCOL, *DTCS]
FREEZE = ["020200", "020400", "020500", "020C00", "020D00"]
PRECONDITIONS = ["0100", "ATRV", "010C"]

# Genau so sieht ein Befehl auf der Leitung aus: ASCII, Großbuchstaben/Ziffern, ein CR.
WIRE = re.compile(rb"[0-9A-Z]+\r")


class WireCheckingTransport(FakeTransport):
    """Prüft jedes gesendete Byte-Paket gegen das ELM327-Befehlsformat."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.wire: list[bytes] = []

    def write(self, data: bytes) -> None:
        assert WIRE.fullmatch(data), f"falsches Format auf der Leitung: {data!r}"
        self.wire.append(data)
        super().write(data)


def _elm(responses: dict[str, str] = CAN_CAR_FULL, **kwargs: object) -> Elm327:
    return Elm327(WireCheckingTransport(responses, **kwargs))


def _sent(elm: Elm327) -> list[str]:
    transport = elm.transport
    assert isinstance(transport, WireCheckingTransport)
    return transport.sent


# --- Freigabeliste --------------------------------------------------------------


@pytest.mark.parametrize(
    "cmd",
    [
        *sorted(AT_COMMANDS),
        "0100",
        "0101",
        "010C",
        "01FF",
        "0202",
        "020200",
        "020C00",
        *DTCS,
        "0902",
    ],
)
def test_read_only_commands_are_allowed(cmd: str) -> None:
    assert is_read_only(cmd)
    elm = _elm({})
    elm.command(cmd)
    assert _sent(elm) == [cmd]


@pytest.mark.parametrize(
    "cmd",
    [
        "04",  # Löschen außerhalb von clear_dtcs
        "05",  # Lambdasonden-Tests (alt)
        "06",  # Testergebnisse, nicht benötigt
        "08",
        "0800",  # Service $08: Steuergerät-Komponenten ansteuern
        "0904",  # Kalibrierungs-ID, nicht benötigt
        "1003",  # UDS: Diagnosesitzung wechseln
        "1101",  # UDS: Steuergerät-Reset
        "14FFFFFF",  # UDS: Fehlerspeicher löschen
        "2EF190",  # UDS: Daten schreiben
        "3101",  # UDS: Routine starten
        "3B01",  # KWP: Daten schreiben
        "ATPP0CSV",  # Adapter-EEPROM beschreiben
        "ATMA",  # Bus mithören
        "ATSH7E0",  # Header setzen: anderes Steuergerät
        "ATWS",
        "ATI",
        "atz",  # Kleinschreibung
        "01 00",  # Leerzeichen
        "0100\r04",  # zweiter Befehl hinter einem CR
        "0100\n",
        "010C;04",
        "",
        "0902\x00",
        "04\r",
    ],
)
def test_other_commands_are_refused_and_nothing_is_sent(cmd: str) -> None:
    elm = _elm({})
    with pytest.raises(ForbiddenCommandError):
        elm.command(cmd)
    assert _sent(elm) == []


def test_forbidden_command_is_no_elm_error() -> None:
    # Sonst fingen Aufrufer wie voltage()/run_diagnosis ihn als „Angabe fehlt“ ab.
    assert not issubclass(ForbiddenCommandError, ElmError)
    assert not issubclass(ForbiddenCommandError, TransportError)


def test_clear_is_allowed_only_inside_the_context() -> None:
    elm = _elm({"04": "44"})
    with elm.allow_clear():
        elm.command(CLEAR_COMMAND)
    with pytest.raises(ForbiddenCommandError):
        elm.command(CLEAR_COMMAND)
    with pytest.raises(RuntimeError), elm.allow_clear():
        raise RuntimeError("Abbruch im Block")
    with pytest.raises(ForbiddenCommandError):  # nach einer Ausnahme wieder gesperrt
        elm.command(CLEAR_COMMAND)
    assert _sent(elm) == ["04"]


@given(st.text(max_size=12))
def test_any_text_is_either_allowed_or_never_sent(cmd: str) -> None:
    elm = Elm327(FakeTransport({}))
    if is_read_only(cmd):
        elm.command(cmd)
        assert elm.transport.sent == [cmd]  # type: ignore[attr-defined]
    else:
        with pytest.raises(ForbiddenCommandError):
            elm.command(cmd)
        assert elm.transport.sent == []  # type: ignore[attr-defined]


# --- exakte Befehlsfolge je Funktion ---------------------------------------------


@pytest.mark.parametrize(
    ("name", "call", "expected"),
    [
        ("initialize", lambda e: e.initialize(), INIT),
        ("voltage", lambda e: e.voltage(), ["ATRV"]),
        ("protocol", lambda e: e.protocol(), PROTOCOL),
        ("read_dtcs 03", lambda e: obd.read_dtcs(e, 0x03, can=True), ["03"]),
        ("read_dtcs 07", lambda e: obd.read_dtcs(e, 0x07, can=True), ["07"]),
        ("read_dtcs 0A", lambda e: obd.read_dtcs(e, 0x0A, can=True), ["0A"]),
        ("read_rpm", obd.read_rpm, ["010C"]),
        ("read_rpms", obd.read_rpms, ["010C"]),
        ("read_pid", lambda e: obd.read_pid(e, 0x0C), ["010C"]),
        # 0100 setzt in CAN_CAR_FULL das Bit für 0x20 (A8 13): Kette mit 0120 (unterstützte
        # PIDs 21-40); dessen Antwort ist unbrauchbar, also endet die Kette dort.
        ("read_supported_pids", pids.read_supported_pids, ["0100", "0120"]),
        ("read_value rpm", lambda e: pids.read_value(e, pids.pid_by_key("rpm")), ["010C"]),
        ("read_value odometer", lambda e: pids.read_value(e, pids.PIDS["odometer"]), ["01A6"]),
        ("read_freeze_frame", obd.read_freeze_frame, FREEZE),
        ("read_readiness", readiness.read_readiness, ["0101"]),
        ("read_vin", vehicle.read_vin, ["0902"]),
        ("scan", lambda e: diagnostics.scan(e, None), SCAN),
        (
            "run_diagnosis",
            lambda e: session.run_diagnosis(e, None),
            [*SCAN, "0101", *FREEZE, "0902"],
        ),
    ],
)
def test_read_function_sends_exactly(name: str, call: object, expected: list[str]) -> None:
    elm = _elm()
    call(elm)  # type: ignore[operator]
    assert _sent(elm) == expected, name
    assert all(is_read_only(c) for c in _sent(elm))


def test_preconditions_send_only_reads() -> None:
    elm = _elm(CAN_CAR_ENGINE_OFF)
    clear.check_preconditions(elm)
    assert _sent(elm) == PRECONDITIONS


def test_clear_sends_04_exactly_once_after_the_backup(tmp_path: Path) -> None:
    backups_at_04: list[int] = []

    class Watching(WireCheckingTransport):
        def write(self, data: bytes) -> None:
            if data == b"04\r":
                backups_at_04.append(len(list(tmp_path.glob("dtc-backup-*.json"))))
            super().write(data)

    elm = Elm327(Watching(CAN_CAR_ENGINE_OFF, after_clear=CLEARED))
    clear.clear_codes(elm, None, backup_dir=tmp_path)
    assert _sent(elm) == [*SCAN, *PRECONDITIONS, *FREEZE, "04", *SCAN]
    assert backups_at_04 == [1]  # Sicherung lag beim Senden von 04 schon vollständig vor


def test_refused_clear_sends_only_reads(tmp_path: Path) -> None:
    elm = _elm({**CAN_CAR_FULL, "010C": "410C1AF8"})  # Motor läuft (1726 1/min)
    with pytest.raises(ClearRefused):
        clear.clear_codes(elm, None, backup_dir=tmp_path)
    assert _sent(elm) == [*SCAN, *PRECONDITIONS]
    assert all(is_read_only(c) for c in _sent(elm))


# --- Kommandozeile -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["info"], [*INIT, "ATRV"]),
        (["scan"], SCAN),
        (["diagnose"], [*SCAN, "0101", *FREEZE, "0902"]),
        (["vin"], [*INIT, *PROTOCOL, "0902"]),
    ],
)
def test_cli_read_commands_send_exactly(
    argv: list[str], expected: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    transports: list[WireCheckingTransport] = []

    def open_port(port: str, baud: int) -> WireCheckingTransport:
        transports.append(WireCheckingTransport(CAN_CAR_FULL))
        return transports[-1]

    monkeypatch.setattr(cli, "SerialTransport", open_port)
    monkeypatch.setattr(DtcCatalog, "default", classmethod(lambda cls: None))
    assert cli.main(argv) == 0
    assert [c for t in transports for c in t.sent] == expected


# --- Fehlerpfade: auch bei kaputten Antworten wird nur gelesen ----------------------

_GARBAGE = st.sampled_from(
    ["NO DATA", "?", "CAN ERROR", "STOPPED", "7F0112", "41", "43", "4301", "ZZ", "", "OK"]
)
_READ_CALLS: list[Callable[[Elm327], object]] = [
    lambda e: diagnostics.scan(e, None),
    lambda e: session.run_diagnosis(e, None),
    lambda e: vehicle.read_vin(e),
    lambda e: readiness.read_readiness(e),
    lambda e: obd.read_freeze_frame(e),
    lambda e: e.protocol(),
]


@given(
    st.dictionaries(st.sampled_from([*SCAN, "0101", *FREEZE, "0902", "0202"]), _GARBAGE),
    st.sampled_from(range(len(_READ_CALLS))),
)
def test_read_paths_never_write_even_on_broken_answers(broken: dict[str, str], which: int) -> None:
    transport = WireCheckingTransport({**CAN_CAR_FULL, **broken})
    with contextlib.suppress(ElmError, ValueError, TransportError):  # Abbruch ist in Ordnung
        _READ_CALLS[which](Elm327(transport))
    assert CLEAR_COMMAND not in transport.sent
    assert all(is_read_only(c) for c in transport.sent), transport.sent


# --- Architektur: es gibt keinen Weg an der Freigabeliste vorbei -------------------

SRC = Path(__file__).resolve().parents[2] / "src" / "obd_diag"

# mutmut schreibt den Quelltext für seine Mutanten um; Prüfungen des Quelltexts selbst
# laufen dort nicht (sie prüfen den Aufbau, nicht das Verhalten).
source_check = pytest.mark.skipif(
    "MUTANT_UNDER_TEST" in os.environ, reason="Quelltext von mutmut umgeschrieben"
)


@source_check
def test_only_elm327_command_writes_to_the_adapter() -> None:
    """Außer ``Elm327.command`` (nach ``_check``) schreibt niemand an einen Transport.
    ``transport/`` selbst reicht nur Bytes weiter (seriell, Mitschnitt)."""
    write_call = re.compile(r"\.write\(")
    offenders = []
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC).as_posix()
        if rel.startswith("transport/"):
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if write_call.search(code) and not re.search(r"\b(f|log|self\.log)\.write\(", code):
                offenders.append(f"{rel}:{number}: {line.strip()}")
    assert len(offenders) == 1, offenders
    assert offenders[0].startswith("protocol/elm327.py:"), offenders
    assert "self.transport.write(" in offenders[0], offenders

    source = (SRC / "protocol" / "elm327.py").read_text(encoding="utf-8")
    command = source[source.index("    def command(") : source.index("    def read_more(")]
    assert command.index("self._check(cmd)") < command.index("self.transport.write(")
