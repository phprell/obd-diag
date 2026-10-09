"""Schutz des Fahrzeugs vor schreibenden Befehlen, ergänzend zu ``test_command_guard``.

Hier wird geprüft, was über einzelne Befehlsfolgen hinausgeht:

- Löschen mit beliebig kaputten Antworten (Hypothesis): Mode 04 geht höchstens einmal
  hinaus, und nur wenn alle Vorbedingungen nachweislich erfüllt sind und die Sicherung
  vollständig vorliegt.
- Mehrere Steuergeräte melden die Drehzahl: gelöscht wird nur, wenn alle 0 melden.
- Aufrufgraph (AST): nur die vorgesehenen Stellen erreichen ``allow_clear``,
  ``clear_dtcs``, ``clear_codes``, den Transport und pyserial.
- GUI-Backend und Kommandozeile ohne Adapterbezug senden nichts bzw. nur Lesendes.
- Der serielle Transport sendet beim Öffnen und Schließen nichts und gibt Befehle
  unverändert weiter.
"""

import ast
import contextlib
import json
import os
import tempfile
import tty
from collections.abc import Iterator
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from obd_diag import cli
from obd_diag.data.dtc_catalog import DtcCatalog
from obd_diag.protocol.elm327 import CLEAR_COMMAND, Elm327, ElmError, is_read_only
from obd_diag.services import clear
from obd_diag.services.clear import ClearRefused
from obd_diag.services.session import save_session
from obd_diag.transport import TransportError
from obd_diag.transport.serial import SerialTransport
from obd_diag.ui import backend
from tests.fakes import CAN_CAR_ENGINE_OFF, CAN_CAR_FULL, CLEARED
from tests.samples import full_session
from tests.unit.test_command_guard import (
    FREEZE,
    PRECONDITIONS,
    SCAN,
    WireCheckingTransport,
    source_check,
)

SRC = Path(__file__).resolve().parents[2] / "src" / "obd_diag"

# --- Löschen mit beliebigen Antworten -------------------------------------------------

# Antworten je Befehl; die Mengen ``*_OK`` sind von Hand nach J1979 bzw. Datenblatt
# bestimmt (nicht aus dem Code): nur damit darf gelöscht werden.
_RPM_ZERO = ["410C0000", "41 0C 00 00", "410C0000\r410C0000"]
_RPM_OTHER = [
    "410C1AF8",  # 1726 1/min
    "410C0001",  # 0,25 1/min: läuft (gerade an- oder ausgehend)
    "410C0100",
    "410C00",  # ein Datenbyte zu wenig
    "410C",
    "410D0000",  # falsche PID
    "410C0000\r410C1AF8",  # Motorsteuergerät 0, ein anderes meldet Drehzahl
    "410C1AF8\r410C0000",
    "7F010C12",
    "NO DATA",
    "?",
    "CAN ERROR",
    "STOPPED",
    "ZZ",
    "",
]
_PID00_OK = ["4100BE3FA813", "SEARCHING...\r4100BE3FA813", "41 00 BE 3F A8 13"]
_PID00_OTHER = ["NO DATA", "UNABLE TO CONNECT", "7F0100", "4101", "?", "OK", "BUS INIT: ...ERROR"]
_VOLTAGE_OK = ["12.4V", "11.8V", "14.1V"]
_VOLTAGE_UNREADABLE = ["?", "ZZ", "OK", ""]  # ohne ATRV ist Löschen bewusst erlaubt
_VOLTAGE_LOW = ["11.7V", "9.0V", "0.0V"]
# PID 42 (Steuergerätespannung), gelesen nur bei niedrigem ATRV
_ECU_VOLTAGE_OK = ["41422EDC", "41423138\r41422EDC"]  # 12,0 V; 12,6 V
_ECU_VOLTAGE_OTHER = ["41422AF8", "41420000", "4142FFFF", "414200", "7F014212", "NO DATA", "?"]
_CLEAR_ANSWERS = ["44", "7F0478", "7F0422", "7F0411", "NO DATA", "?", "STOPPED", "OK", "4300", ""]
_GARBAGE = ["NO DATA", "?", "CAN ERROR", "STOPPED", "7F0112", "ZZ", "", "OK", "41", "43"]


@st.composite
def _car(draw: st.DrawFn) -> tuple[dict[str, str], dict[str, list[str]]]:
    responses = dict(CAN_CAR_ENGINE_OFF)
    responses["010C"] = draw(st.sampled_from(_RPM_ZERO + _RPM_OTHER))
    responses["0100"] = draw(st.sampled_from(_PID00_OK + _PID00_OTHER))
    responses["ATRV"] = draw(st.sampled_from(_VOLTAGE_OK + _VOLTAGE_UNREADABLE + _VOLTAGE_LOW))
    responses["0142"] = draw(st.sampled_from(_ECU_VOLTAGE_OK + _ECU_VOLTAGE_OTHER))
    responses["04"] = draw(st.sampled_from(_CLEAR_ANSWERS))
    # Übrige Befehle des Ablaufs zufällig kaputt
    others = [*SCAN, *FREEZE]
    broken = draw(st.dictionaries(st.sampled_from(others), st.sampled_from(_GARBAGE), max_size=2))
    responses.update({k: v for k, v in broken.items() if k not in ("0100", "ATRV")})
    later = {"04": draw(st.lists(st.sampled_from(_CLEAR_ANSWERS), max_size=3))}
    return responses, later


def _has_clearable_codes(responses: dict[str, str]) -> bool:
    return responses["03"] == CAN_CAR_ENGINE_OFF["03"] or responses["07"] == "47010133"


class _BackupWatcher(WireCheckingTransport):
    """Merkt sich, welche Sicherungen beim Senden von 04 vollständig vorlagen."""

    def __init__(self, backup_dir: Path, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.backup_dir = backup_dir
        self.backups_at_clear: list[list[dict[str, object]]] = []

    def write(self, data: bytes) -> None:
        if data == b"04\r":
            self.backups_at_clear.append(
                [json.loads(p.read_text()) for p in self.backup_dir.glob("dtc-backup-*.json")]
            )
        super().write(data)


@settings(max_examples=400, deadline=None)
@given(car=_car(), backup_broken=st.booleans())
def test_clear_with_arbitrary_answers_sends_04_only_when_safe(
    car: tuple[dict[str, str], dict[str, list[str]]], backup_broken: bool
) -> None:
    responses, later = car
    with tempfile.TemporaryDirectory() as tmp:
        backup_dir = Path(tmp) / "backups"
        if backup_broken:
            backup_dir.write_text("keine Ablage")  # eine Datei, wo der Ordner hin soll
        transport = _BackupWatcher(backup_dir, responses, CLEARED, later=later)
        with contextlib.suppress(ClearRefused, ElmError, ValueError, TransportError):
            clear.clear_codes(Elm327(transport), None, backup_dir=backup_dir)

    sent = transport.sent
    assert all(is_read_only(c) or c == CLEAR_COMMAND for c in sent), sent
    assert sent.count(CLEAR_COMMAND) <= 1, "Mode 04 darf nie wiederholt werden"
    if CLEAR_COMMAND not in sent:
        return
    # Wurde gelöscht, müssen alle Vorbedingungen nachweislich erfüllt gewesen sein.
    assert responses["010C"] in _RPM_ZERO, responses["010C"]
    assert responses["0100"] in _PID00_OK, responses["0100"]
    low = responses["ATRV"] in _VOLTAGE_LOW
    # Niedriges ATRV nur, wenn das Steuergerät plausibel mindestens 11,8 V meldet.
    assert not low or responses["0142"] in _ECU_VOLTAGE_OK, responses
    preconditions = ["0100", "ATRV", "0142", "010C"] if low else PRECONDITIONS
    assert _has_clearable_codes(responses)
    assert not backup_broken
    # Die Prüfung der Drehzahl kam vor dem Löschen, danach nur noch Lesendes.
    index = sent.index(CLEAR_COMMAND)
    assert "010C" in sent[:index]
    # Unmittelbar vor 04: genau die Vorbedingungen, dann genau eine der drei möglichen
    # Freeze-Frame-Folgen (J1979-Format; Rückfall auf 0202 gescheitert; Rückfall geglückt,
    # dann alles ohne Frame-Nummer, siehe ``read_freeze_frame``).
    freeze_variants = [
        FREEZE,
        [FREEZE[0], "0202", *FREEZE[1:]],
        [FREEZE[0], *(c[:4] for c in FREEZE)],
    ]
    assert any(
        sent[index - len(f) - len(preconditions) : index] == [*preconditions, *f]
        for f in freeze_variants
    ), sent
    assert all(is_read_only(c) for c in sent[index + 1 :])
    # Beim Senden lag genau eine vollständige Sicherung mit den Codes vor.
    ((backup,),) = transport.backups_at_clear
    assert backup["scan"]["codes"], backup  # type: ignore[index]


@pytest.mark.parametrize(
    "rpm",
    [
        "410C0000\r410C1AF8",  # Motorsteuergerät meldet 0, Getriebesteuergerät nicht
        "410C1AF8\r410C0000",
        "410C0000\r410C00",  # zweites Steuergerät mit unvollständiger Antwort
        "410C0000\r7F010C12",  # zweites lehnt ab: mit Vorsicht ebenfalls kein Löschen
    ],
)
def test_clear_refused_unless_every_ecu_reports_zero_rpm(rpm: str, tmp_path: Path) -> None:
    transport = WireCheckingTransport({**CAN_CAR_ENGINE_OFF, "010C": rpm}, CLEARED)
    with pytest.raises(ClearRefused, match=r"Drehzahl|Motor läuft"):
        clear.clear_codes(Elm327(transport), None, backup_dir=tmp_path)
    assert CLEAR_COMMAND not in transport.sent
    assert not list(tmp_path.iterdir())


def test_clear_allowed_when_two_ecus_report_zero_rpm(tmp_path: Path) -> None:
    transport = WireCheckingTransport({**CAN_CAR_ENGINE_OFF, "010C": "410C0000\r410C0000"}, CLEARED)
    clear.clear_codes(Elm327(transport), None, backup_dir=tmp_path)
    assert transport.sent.count(CLEAR_COMMAND) == 1


# --- Aufrufgraph: wer darf an die schreibenden Stellen -------------------------------


def _modules() -> Iterator[tuple[str, ast.Module]]:
    for path in sorted(SRC.rglob("*.py")):
        yield path.relative_to(SRC).as_posix(), ast.parse(path.read_text(encoding="utf-8"))


def _uses(name: str, *, attribute_only: bool = False) -> set[str]:
    """``modul:funktion`` für jede Stelle, die ``name`` als Name oder Attribut nutzt."""
    found: set[str] = set()

    def matches(node: ast.AST) -> bool:
        if isinstance(node, ast.Attribute):
            return node.attr == name
        return not attribute_only and isinstance(node, ast.Name) and node.id == name

    for rel, tree in _modules():
        for func in ast.walk(tree):
            if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if any(matches(node) for node in ast.walk(func)):
                found.add(f"{rel}:{func.name}")
        for node in tree.body:  # Zugriffe auf Modulebene (außerhalb von Funktionen)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                continue
            if any(matches(sub) for sub in ast.walk(node)):
                found.add(f"{rel}:<modul>")
    return found


@source_check
@pytest.mark.parametrize(
    ("name", "allowed"),
    [
        # Mode 04 freischalten: nur clear_dtcs
        ("allow_clear", {"protocol/obd.py:clear_dtcs"}),
        (
            "_clear_allowed",
            {
                "protocol/elm327.py:__init__",
                "protocol/elm327.py:allow_clear",
                "protocol/elm327.py:_check",
            },
        ),
        # clear_dtcs: nur nach Vorbedingungen und Sicherung in clear_codes
        ("clear_dtcs", {"services/clear.py:clear_codes"}),
        # clear_codes: nur die Kommandozeile (nach Rückfrage) und die GUI (nach Dialog)
        ("clear_codes", {"cli.py:_run_clear", "ui/backend.py:clear_port"}),
        # Live-Daten: nur Kommandozeile und GUI-Backend starten die Abfrage, und nur der
        # Live-Dienst fragt Werte ab.
        ("run_live", {"cli.py:_run_live", "ui/backend.py:live_port"}),
        ("prepare_live", {"cli.py:_run_live", "ui/backend.py:live_port"}),
        (
            "read_values",
            {
                "services/live.py:_read_values",
                "services/diagnostics.py:check_low_voltage",  # PID 42 bei niedrigem ATRV
                "protocol/pids.py:read_value",
            },
        ),
        ("read_value", set()),  # nur für Tests; der Live-Dienst fragt je PID einmal
        ("read_supported_pids", {"services/live.py:prepare_live"}),
        # Den Transport eines Elm327 (``elm.transport``) fasst nur Elm327 selbst an.
        (
            "transport",
            {
                "protocol/elm327.py:__init__",
                "protocol/elm327.py:command",
                "protocol/elm327.py:_read",
            },
        ),
    ],
)
def test_only_intended_code_reaches_writing_paths(name: str, allowed: set[str]) -> None:
    assert _uses(name, attribute_only=name == "transport") == allowed


@source_check
def test_pyserial_is_used_only_in_the_transport_layer() -> None:
    importers: set[str] = set()
    for rel, tree in _modules():
        for node in ast.walk(tree):
            modules = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
            if any(m == "serial" or m.startswith("serial.") for m in modules):
                importers.add(rel)
    assert importers == {"transport/serial.py", "transport/discovery.py"}


# Ausnahme: der serielle Transport fängt ``termios.error`` (so meldet pyserial einen
# abgezogenen Adapter beim Leeren des Puffers). Er darf termios importieren, aber nur
# dieses eine Attribut benutzen; ``test_termios_is_only_used_for_its_error_type`` prüft das.
_ERROR_TYPE_ONLY = {("transport/serial.py", "termios")}


@source_check
def test_termios_is_only_used_for_its_error_type() -> None:
    for rel, module in _ERROR_TYPE_ONLY:
        tree = next(t for r, t in _modules() if r == rel)
        uses = [node for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id == module]
        attributes = {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == module
        }
        assert uses, f"{rel} benutzt {module} nicht mehr; Ausnahme entfernen"
        assert attributes == {"error"}, attributes
        # jeder Name-Knoten ist der Wert eines ``.error``-Zugriffs, nichts sonst
        assert len(uses) == sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == module
        )


@source_check
def test_no_raw_io_or_dynamic_attribute_access() -> None:
    """Keine Hintertür am Transport vorbei: kein Socket, kein os.write/ioctl, kein
    ``getattr``/``setattr`` mit berechnetem Namen, kein ``exec``/``eval``."""
    forbidden_modules = {"socket", "fcntl", "termios", "ctypes", "subprocess", "pty"}
    offenders: list[str] = []
    for rel, tree in _modules():
        for node in ast.walk(tree):
            if isinstance(node, ast.Import | ast.ImportFrom):
                names = (
                    [a.name for a in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module or ""]
                )
                offenders += [
                    f"{rel}: import {n}"
                    for n in names
                    if n in forbidden_modules and (rel, n) not in _ERROR_TYPE_ONLY
                ]
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id in {"exec", "eval", "setattr", "getattr"}:
                    offenders.append(f"{rel}:{node.lineno}: {func.id}()")
                if (
                    isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "os"
                    and func.attr in {"write", "writev", "pwrite", "open", "system", "popen"}
                ):
                    offenders.append(f"{rel}:{node.lineno}: os.{func.attr}()")
    assert offenders == []


# --- GUI-Backend und Kommandozeile ----------------------------------------------------


@pytest.fixture
def serial_ports(monkeypatch: pytest.MonkeyPatch) -> list[WireCheckingTransport]:
    """Ersetzt das Öffnen des seriellen Ports im GUI-Backend durch einen Fake."""
    opened: list[WireCheckingTransport] = []

    def open_serial(port: str, baud: int, trace: Path | None) -> WireCheckingTransport:
        opened.append(WireCheckingTransport(CAN_CAR_FULL, CLEARED))
        return opened[-1]

    monkeypatch.setattr(backend, "open_serial", open_serial)
    monkeypatch.setattr(DtcCatalog, "default", classmethod(lambda cls: None))
    return opened


def test_gui_diagnosis_sends_only_reads(serial_ports: list[WireCheckingTransport]) -> None:
    backend.diagnose_port("/dev/ttyUSB0", 38400)
    (transport,) = serial_ports
    assert transport.sent == [*SCAN, "0101", *FREEZE, "0902"]


def test_gui_clear_sends_04_once_after_checks(
    serial_ports: list[WireCheckingTransport], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    backend.clear_port("/dev/ttyUSB0", 38400)
    (transport,) = serial_ports
    assert transport.sent == [*SCAN, *PRECONDITIONS, *FREEZE, "04", *SCAN]


@pytest.fixture
def no_port(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("Port geöffnet")

    monkeypatch.setattr(cli, "SerialTransport", refuse)
    monkeypatch.setattr(backend, "open_serial", refuse)


@pytest.mark.usefixtures("no_port")
def test_commands_without_adapter_never_open_a_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "list_ports", lambda: [])
    session_file = save_session(full_session(), tmp_path)
    assert cli.main(["ports"]) == 0
    assert cli.main(["export", str(session_file), "--csv", str(tmp_path / "a.csv")]) == 0
    assert cli.main(["vin", "WVWZZZ1KZ6W123456"]) == 0


# --- serieller Transport ------------------------------------------------------------


@pytest.fixture
def pty_pair() -> Iterator[tuple[int, str]]:
    master, slave = os.openpty()
    tty.setraw(slave)
    os.set_blocking(master, False)
    yield master, os.ttyname(slave)
    os.close(master)
    os.close(slave)


def _pending(master: int) -> bytes:
    try:
        return os.read(master, 4096)
    except BlockingIOError:
        return b""


def test_opening_and_closing_the_port_sends_nothing(pty_pair: tuple[int, str]) -> None:
    master, device = pty_pair
    with SerialTransport(device):
        assert _pending(master) == b""
    assert _pending(master) == b""


def test_serial_transport_sends_the_command_unchanged(pty_pair: tuple[int, str]) -> None:
    master, device = pty_pair
    with SerialTransport(device) as transport:
        transport.write(b"0100\r")
        assert _pending(master) == b"0100\r"
