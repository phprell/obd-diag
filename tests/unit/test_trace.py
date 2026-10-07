import io
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from obd_diag import cli
from obd_diag.protocol.elm327 import Elm327
from obd_diag.services.diagnostics import scan
from obd_diag.transport import TransportError, TransportTimeout
from obd_diag.transport.trace import (
    FileTracingTransport,
    ReplayTransport,
    TracingTransport,
    escape,
    new_trace_path,
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
