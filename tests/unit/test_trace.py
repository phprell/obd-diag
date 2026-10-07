import io
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from obd_diag import __version__, cli
from obd_diag.protocol.elm327 import Elm327
from obd_diag.services.diagnostics import scan
from obd_diag.transport import TransportError, TransportTimeout, trace
from obd_diag.transport.trace import (
    FileTracingTransport,
    ReplayTransport,
    TracingTransport,
    escape,
    new_trace_path,
    open_serial,
    read_trace,
    unescape,
)
from tests.fakes import CAN_CAR, FakeTransport


class _Failing(FakeTransport):
    def read_until(self, terminator: bytes, timeout: float) -> bytes:
        raise TransportTimeout("keine Antwort nach 5 s")


class _Unopenable(FakeTransport):
    def open(self) -> None:
        raise TransportError("/dev/ttyUSB0 lässt sich nicht öffnen")


def test_escape_makes_control_bytes_visible() -> None:
    assert escape(b"\xfc\r\rELM327 v1.5\r\r>") == "\\xfc\\r\\rELM327 v1.5\\r\\r>"
    assert escape(b"a\\b\n\x00") == "a\\\\b\\n\\x00"


@given(st.binary(max_size=200))
def test_escape_round_trip(data: bytes) -> None:
    text = escape(data)
    assert "\n" not in text and "\r" not in text
    assert unescape(text) == data


def test_tracing_logs_both_directions_and_errors() -> None:
    log = io.StringIO()
    with TracingTransport(FakeTransport({"ATZ": "ELM327 v1.5"}), log, "/dev/ttyUSB0") as t:
        t.write(b"ATZ\r")
        assert t.read_until(b">", 1.0) == b"ELM327 v1.5\r\r>"
    lines = log.getvalue().splitlines()
    assert lines[0].startswith("# obd-diag ") and lines[0].endswith(" /dev/ttyUSB0")
    assert lines[1].lstrip().split(" ", 2)[1:] == [">>", "ATZ\\r"]
    assert lines[2].lstrip().split(" ", 2)[1:] == ["<<", "ELM327 v1.5\\r\\r>"]

    log = io.StringIO()
    with TracingTransport(_Failing({}), log) as t, pytest.raises(TransportTimeout):
        t.read_until(b">", 1.0)
    assert " !! keine Antwort nach 5 s" in log.getvalue()


def test_file_tracing_appends_and_closes_file_on_open_error(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "trace.log"
    for _ in range(2):
        with FileTracingTransport(FakeTransport({}), path) as t:
            t.write(b"ATRV\r")
    assert path.read_text(encoding="utf-8").count("# obd-diag") == 2

    failing = FileTracingTransport(_Unopenable({}), tmp_path / "fail.log")
    with pytest.raises(TransportError):
        failing.open()
    assert failing._file.closed
    assert "!! /dev/ttyUSB0 lässt sich nicht öffnen" in (tmp_path / "fail.log").read_text()


def test_new_trace_path_never_reuses_a_name(tmp_path: Path) -> None:
    first = new_trace_path(tmp_path)
    first.touch()
    second = new_trace_path(tmp_path)
    assert first != second and second.name.startswith("trace-")


def test_recorded_scan_replays_identically(tmp_path: Path) -> None:
    """Ein Mitschnitt reicht, um den Scan ohne Adapter exakt nachzustellen."""
    path = tmp_path / "trace.log"
    with FileTracingTransport(FakeTransport(CAN_CAR), path) as t:
        recorded = scan(Elm327(t), None)
    replayed = scan(Elm327(ReplayTransport.from_file(path)), None)
    assert replayed == recorded and recorded.codes
    assert (">>", b"03\r") in read_trace(path)


def test_replay_rejects_a_different_command(tmp_path: Path) -> None:
    replay = ReplayTransport([(">>", b"ATZ\r"), ("<<", b"ELM327 v1.5\r\r>")])
    with pytest.raises(TransportError, match="aufgezeichnet war"):
        replay.write(b"ATE0\r")


def test_cli_trace_option(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "SerialTransport", lambda port, baud: FakeTransport(CAN_CAR))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    target = tmp_path / "eigener.log"
    assert cli.main(["scan", "--trace", str(target)]) == 0
    assert f"Mitschnitt: {target}" in capsys.readouterr().err
    assert (">>", b"03\r") in read_trace(target)

    # ohne Dateiname: automatisch unter $XDG_DATA_HOME/obd-diag/traces
    assert cli.main(["info", "--trace"]) == 0
    (auto,) = (tmp_path / "data" / "obd-diag" / "traces").glob("trace-*.log")
    assert (">>", b"ATRV\r") in read_trace(auto)

    # ohne --trace kein Mitschnitt
    assert cli.main(["info"]) == 0
    assert len(list((tmp_path / "data" / "obd-diag" / "traces").iterdir())) == 1


def test_escape_delete_and_high_bytes() -> None:
    assert escape(b" ~\x7f\x80\x1f") == " ~\\x7f\\x80\\x1f"


@pytest.mark.parametrize(("text", "shown"), [("ab\\qcdef", "'\\\\qcd'"), ("ab\\q", "'\\\\q'")])
def test_unescape_rejects_unknown_sequence(text: str, shown: str) -> None:
    with pytest.raises(ValueError) as info:
        unescape(text)
    assert str(info.value) == f"ungültige Escape-Sequenz an Stelle 2: {shown}"


def test_trace_lines_have_relative_timestamps(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = iter([100.0, 100.0, 101.25, 102.5, 103.0])
    monkeypatch.setattr(
        "obd_diag.transport.trace.time", SimpleNamespace(monotonic=lambda: next(clock))
    )
    log = io.StringIO()
    with TracingTransport(FakeTransport({"ATZ": "ELM327 v1.5"}), log) as t:
        t.write(b"ATZ\r")
        t.read_until(b">", 1.0)
    header, *lines = log.getvalue().splitlines()
    # ohne Bezeichnung endet der Kopf mit dem Zeitstempel
    iso = r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d+[+-]\d\d:\d\d"
    assert re.fullmatch(rf"# obd-diag {re.escape(__version__)} Mitschnitt {iso}", header), header
    assert lines == [
        "    1.250 >> ATZ\\r",
        "    2.500 << ELM327 v1.5\\r\\r>",
        "    3.000 -- geschlossen",
    ]


def test_tracing_passes_terminator_and_timeout() -> None:
    calls: list[tuple[bytes, float]] = []

    class Recording(FakeTransport):
        def read_until(self, terminator: bytes, timeout: float) -> bytes:
            calls.append((terminator, timeout))
            return b">"

    TracingTransport(Recording({}), io.StringIO()).read_until(b"\r>", 2.5)
    assert calls == [(b"\r>", 2.5)]


def test_file_tracing_creates_parents_and_labels(tmp_path: Path) -> None:
    path = tmp_path / "a" / "b" / "trace.log"
    t = FileTracingTransport(FakeTransport({}), path, "/dev/ttyUSB0 38400 Baud")
    assert t.path == path
    with t:
        t.write(b"ATRV\r")
    header = path.read_text(encoding="utf-8").splitlines()[0]
    assert header.endswith(" /dev/ttyUSB0 38400 Baud")


def test_file_tracing_writes_utf8(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Fehlermeldungen mit Umlauten landen auch unter einer ASCII-Locale im Mitschnitt."""
    real_open = Path.open

    def c_locale_open(self: Path, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if "b" not in mode:
            kwargs["encoding"] = kwargs.get("encoding") or "ascii"
        return real_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", c_locale_open)
    failing = FileTracingTransport(_Unopenable({}), tmp_path / "fail.log")
    with pytest.raises(TransportError):
        failing.open()
    assert "lässt sich nicht öffnen" in (tmp_path / "fail.log").read_bytes().decode("utf-8")


def test_open_serial(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    created: list[tuple[str, int]] = []

    class Serial(FakeTransport):
        def __init__(self, port: str, baudrate: int) -> None:
            super().__init__({})
            created.append((port, baudrate))

    monkeypatch.setattr(trace, "SerialTransport", Serial)
    plain = open_serial("/dev/ttyUSB0", 38400)
    assert isinstance(plain, Serial) and created == [("/dev/ttyUSB0", 38400)]

    path = tmp_path / "t.log"
    traced = open_serial("/dev/ttyUSB1", 9600, path)
    assert isinstance(traced, FileTracingTransport)
    assert isinstance(traced.inner, Serial) and created[-1] == ("/dev/ttyUSB1", 9600)
    with traced:
        traced.write(b"ATZ\r")
    assert path.read_text(encoding="utf-8").splitlines()[0].endswith(" /dev/ttyUSB1 9600 Baud")
    assert read_trace(path) == [(">>", b"ATZ\r")]


def test_new_trace_path_numbering(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class FixedNow:
        @staticmethod
        def now() -> "FixedNow":
            return FixedNow()

        def __format__(self, spec: str) -> str:
            return "20261007-123005"

    monkeypatch.setattr(trace, "datetime", FixedNow)
    target = tmp_path / "neu"
    names = []
    for _ in range(3):
        path = new_trace_path(target)
        path.touch()
        names.append(path.name)
    assert names == [
        "trace-20261007-123005.log",
        "trace-20261007-123005-2.log",
        "trace-20261007-123005-3.log",
    ]
    for n in range(4, 1000):
        (target / f"trace-20261007-123005-{n}.log").touch()
    with pytest.raises(FileExistsError) as info:
        new_trace_path(target)
    assert str(info.value) == f"kein freier Dateiname für trace-20261007-123005 in {target}"


def test_read_trace_skips_comments_and_other_lines(tmp_path: Path) -> None:
    path = tmp_path / "t.log"
    path.write_text(
        "# obd-diag 0.0.1 Mitschnitt\n"
        "\n"
        "    0.001 >>  AT Z\\r\n"  # Daten beginnen mit Leerzeichen
        "   12.500 << OK\n"
        "    0.002 !! keine Antwort\n"
        "    0.003 -- geschlossen\n"
        "    0.004 >>\n"  # unvollständig
        "#    0.005 >> ATRV\\r\n"
        "# >> ATRV\\r\n",  # auskommentiert, sähe sonst wie ein Befehl aus
        encoding="utf-8",
    )
    assert read_trace(path) == [(">>", b" AT Z\r"), ("<<", b"OK")]


def test_read_trace_utf8_independent_of_locale(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Fehlerzeilen mit Umlauten stören das Einlesen auch unter einer ASCII-Locale nicht."""
    path = tmp_path / "t.log"
    path.write_text(
        "    0.001 >> ATZ\\r\n    0.002 !! /dev/ttyUSB0 lässt sich nicht öffnen\n",
        encoding="utf-8",
    )
    real_read_text = Path.read_text

    def ascii_read_text(self: Path, encoding: str | None = None, errors: str | None = None) -> str:
        return real_read_text(self, encoding=encoding or "ascii", errors=errors)

    monkeypatch.setattr(Path, "read_text", ascii_read_text)
    assert read_trace(path) == [(">>", b"ATZ\r")]


def test_file_tracing_without_label(tmp_path: Path) -> None:
    path = tmp_path / "t.log"
    with FileTracingTransport(FakeTransport({}), path):
        pass
    header = path.read_text(encoding="utf-8").splitlines()[0]
    assert re.fullmatch(r"# obd-diag \S+ Mitschnitt [\d:.T+-]+", header), header


def test_replay_past_the_end(tmp_path: Path) -> None:
    replay = ReplayTransport([(">>", b"ATZ\r"), ("<<", b"OK")])
    with pytest.raises(TransportTimeout) as timeout:
        replay.read_until(b">", 1.0)  # Antwort vor dem Befehl
    assert str(timeout.value) == "Mitschnitt: keine aufgezeichnete Antwort"
    with pytest.raises(TransportError, match="unerwarteter Befehl 'ATRV\\\\\\\\r'"):
        ReplayTransport([("<<", b"OK")]).write(b"ATRV\r")
    replay.write(b"ATZ\r")
    assert replay.read_until(b">", 1.0) == b"OK"
    with pytest.raises(TransportTimeout):
        replay.read_until(b">", 1.0)
    with pytest.raises(TransportError, match="unerwarteter Befehl"):
        replay.write(b"ATZ\r")
