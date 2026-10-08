"""Live-Daten mit der echten PID-Tabelle: Kommandozeile, GUI-Backend, Fuzzing, Taktung.

Ergänzt ``test_live.py`` (dort mit Fake-Tabelle): Hier laufen ``protocol.pids`` und
``services.live`` zusammen, so wie am Auto. Geprüft wird vor allem, dass nur lesende
Befehle gesendet werden, auch bei beliebigen Antworten.
"""

from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from obd_diag import cli
from obd_diag.data.dtc_catalog import DtcCatalog
from obd_diag.protocol.elm327 import CLEAR_COMMAND, Elm327, ElmError, is_read_only
from obd_diag.protocol.pids import (
    PIDS,
    PidSpec,
    parse_supported,
    read_supported_pids,
    read_value,
)
from obd_diag.services import live
from obd_diag.services.live import LiveSample, prepare_live, run_live
from obd_diag.ui import backend
from tests.fakes import CAN_CAR_FULL, FakeTransport
from tests.unit.test_command_guard import INIT, PROTOCOL, WireCheckingTransport

# CAN_CAR_FULL unterstützt laut 0100 u. a. 04, 05, 0C, 0D, 0F (BE 3F A8 13) und setzt
# das Bit für 0120; dessen Antwort "OK" zählt nicht. Laufender Motor:
RUNNING = {
    **CAN_CAR_FULL,
    "0104": "410480",  # 50,2 %
    "0105": "41057E",  # 86 °C
    "010C": "410C1AF8",  # 1726 1/min
    "010D": "410D32",  # 50 km/h
    "010F": "410F3C",  # 20 °C
}
SETUP = [*INIT, *PROTOCOL, "0100", "0120"]
# DEFAULT_KEYS, soweit unterstützt (control_voltage = 0142 nicht), in deren Reihenfolge
DEFAULT_ROUND = ["010C", "010D", "0105", "0104", "010F"]


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


# --- Kommandozeile und GUI-Backend mit echter Tabelle ------------------------------


def test_cli_live_sends_exactly(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    transports: list[WireCheckingTransport] = []

    def open_port(port: str, baud: int) -> WireCheckingTransport:
        transports.append(WireCheckingTransport(RUNNING))
        return transports[-1]

    clock = FakeClock()

    class FakeTime:
        monotonic = staticmethod(clock)
        sleep = staticmethod(clock.sleep)

    monkeypatch.setattr(cli, "SerialTransport", open_port)
    monkeypatch.setattr(cli, "time", FakeTime)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert cli.main(["live", "--duration", "2", "--record"]) == 0
    (transport,) = transports
    assert transport.sent == [*SETUP, "ATRV", *DEFAULT_ROUND, *DEFAULT_ROUND]
    (csv_file,) = (tmp_path / "obd-diag" / "recordings").iterdir()
    lines = csv_file.read_text(encoding="utf-8-sig").splitlines()
    assert lines[0] == (
        "Zeit (s);Motordrehzahl (1/min);Geschwindigkeit (km/h);Kühlmitteltemperatur (°C);"
        "Berechnete Motorlast (%);Ansauglufttemperatur (°C);Bordspannung (V)"
    )
    assert lines[1] == "0;1726;50;86;50,196;20;12,4"
    assert len(lines) == 3


def test_cli_live_list_sends_only_setup(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    transports: list[WireCheckingTransport] = []

    def open_port(port: str, baud: int) -> WireCheckingTransport:
        transports.append(WireCheckingTransport(RUNNING))
        return transports[-1]

    monkeypatch.setattr(cli, "SerialTransport", open_port)
    assert cli.main(["live", "--list"]) == 0
    assert [c for t in transports for c in t.sent] == SETUP
    out = capsys.readouterr().out
    for key in ("rpm", "speed", "coolant_temp", "engine_load", "intake_temp", "maf"):
        assert key in out


@pytest.mark.parametrize("record", [False, True])
def test_gui_backend_live_sends_only_reads(
    record: bool, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    opened: list[WireCheckingTransport] = []

    def open_serial(port: str, baud: int, trace: Path | None) -> WireCheckingTransport:
        opened.append(WireCheckingTransport(RUNNING))
        return opened[-1]

    monkeypatch.setattr(backend, "open_serial", open_serial)
    monkeypatch.setattr(DtcCatalog, "default", classmethod(lambda cls: None))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    samples: list[LiveSample] = []
    result = backend.live_port(
        "/dev/ttyUSB0",
        38400,
        ["rpm", "speed"],
        0.1,
        record,
        on_setup=lambda setup: None,
        on_start=lambda pids, path: None,
        on_sample=samples.append,
        should_stop=lambda: len(samples) >= 3,
    )
    (transport,) = opened
    rounds = ["010C", "010D"] * 3
    assert transport.sent == [*SETUP, "ATRV", *rounds]
    assert result.samples == 3
    assert [s.values for s in samples] == [{"rpm": 1726.0, "speed": 50.0}] * 3
    if record:
        assert result.recording is not None
        assert len(result.recording.read_text(encoding="utf-8-sig").splitlines()) == 4
    else:
        assert result.recording is None
        assert not (tmp_path / "obd-diag").exists()


def test_gui_backend_stop_during_setup_reads_nothing_and_records_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    opened: list[WireCheckingTransport] = []

    def open_serial(port: str, baud: int, trace: Path | None) -> WireCheckingTransport:
        opened.append(WireCheckingTransport(RUNNING))
        return opened[-1]

    monkeypatch.setattr(backend, "open_serial", open_serial)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    stopped: list[bool] = []
    started: list[object] = []
    result = backend.live_port(
        "/dev/ttyUSB0",
        38400,
        ["rpm"],
        0.1,
        True,
        on_setup=lambda setup: stopped.append(True),  # Stopp kommt während der Initialisierung
        on_start=lambda pids, path: started.append(path),
        on_sample=lambda sample: None,
        should_stop=lambda: bool(stopped),
    )
    assert result == backend.LiveResult(0, None)
    assert started == []
    assert opened[0].sent == SETUP
    assert not (tmp_path / "obd-diag").exists()


# --- Fuzzing: beliebige Antworten, nur Lesendes, keine fremden Ausnahmen -------------

_HEX = st.text(alphabet="0123456789ABCDEF ", max_size=24)
_ANSWER = st.one_of(
    _HEX,
    st.sampled_from(["NO DATA", "?", "CAN ERROR", "STOPPED", "OK", "7F0112", "41", ""]),
    st.builds(lambda a, b: f"{a}\r{b}", _HEX, _HEX),  # zwei Steuergeräte
)
_PID_COMMANDS = [
    *sorted({f"01{s.pid:02X}" for s in PIDS.values()}),
    *("0100", "0120", "0140", "0160", "0180"),
]


@settings(max_examples=300, deadline=None)
@given(st.dictionaries(st.sampled_from(_PID_COMMANDS), _ANSWER))
def test_read_value_on_arbitrary_answers(answers: dict[str, str]) -> None:
    """Jeder Wert liegt im Bereich der Norm oder ist None; sonst höchstens ElmError."""
    transport = FakeTransport(answers)
    elm = Elm327(transport)
    for spec in PIDS.values():
        try:
            value = read_value(elm, spec)
        except ElmError:
            continue
        assert value is None or spec.minimum <= value <= spec.maximum, (spec.key, value)
    try:
        supported = read_supported_pids(elm)
    except ElmError:
        supported = set()
    assert all(0x01 <= pid <= 0xE0 for pid in supported)
    assert all(c.startswith("01") for c in transport.sent), transport.sent


@settings(max_examples=150, deadline=None)
@given(
    st.dictionaries(st.sampled_from([*SETUP, *_PID_COMMANDS, "ATRV"]), _ANSWER),
    st.lists(st.sampled_from(sorted(spec.key for spec in PIDS.values())), max_size=6),
)
def test_live_never_writes_on_arbitrary_answers(broken: dict[str, str], keys: list[str]) -> None:
    transport = WireCheckingTransport({**RUNNING, **broken})
    elm = Elm327(transport)
    clock = FakeClock()
    try:
        setup = prepare_live(elm)
        chosen = live.select_pids(setup, keys or None)
        run_live(
            elm,
            chosen,
            on_sample=lambda s: None,
            should_stop=lambda: False,
            max_samples=12,
            clock=clock,
            sleep=clock.sleep,
        )
    except (ElmError, ValueError):
        pass  # Abbruch ist in Ordnung, Schreiben nicht
    assert CLEAR_COMMAND not in transport.sent
    assert all(is_read_only(c) for c in transport.sent), transport.sent


# --- Taktung: nie schneller als erlaubt -----------------------------------------------


@settings(max_examples=200, deadline=None)
@given(
    interval=st.sampled_from([0.1, 0.5, 1.0, 2.0, 7.0]),
    voltages=st.lists(st.sampled_from(["12.6V", "11.9V", "11.7V", "9.8V", "?"]), min_size=1),
    round_cost=st.sampled_from([0.0, 0.05, 0.3, 3.0]),
)
def test_rounds_never_come_faster_than_allowed(
    interval: float, voltages: list[str], round_cost: float
) -> None:
    """Abstand der Rundenbeginne ≥ Intervall, bei niedriger Spannung ≥ 5 s."""
    clock = FakeClock()
    readings = iter(voltages * 50)

    class Slow(FakeTransport):
        def write(self, data: bytes) -> None:
            if data == b"ATRV\r":
                self.responses = {**self.responses, "ATRV": next(readings)}
            clock.now += round_cost / 2
            super().write(data)

    transport = Slow(RUNNING)
    samples: list[LiveSample] = []
    run_live(
        Elm327(transport),
        [PIDS["rpm"]],
        on_sample=samples.append,
        should_stop=lambda: False,
        interval=interval,
        max_samples=25,
        clock=clock,
        sleep=clock.sleep,
    )
    times = [s.elapsed for s in samples]
    for before, after, sample in zip(times, times[1:], samples, strict=False):
        needed = max(interval, live.LOW_VOLTAGE_INTERVAL) if sample.throttled else interval
        assert after - before >= needed - 1e-9, (before, after, sample)
    assert len(samples) == 25


# --- Grenzfälle, die die Mutationstests aufgezeigt haben ------------------------------


def test_parse_supported_accepts_the_last_block_and_ignores_extra_bytes() -> None:
    # 01E0 ist der letzte Block (PIDs E1 bis 100); Füllbytes hinter den vier
    # Maskenbytes zählen nicht.
    assert parse_supported(0xE0, bytes([0x80, 0, 0, 0x01])) == {0xE1, 0x100}
    assert parse_supported(0x00, bytes([0x80, 0, 0, 0, 0xFF])) == {0x01}
    with pytest.raises(ValueError):
        parse_supported(0x100, bytes(4))


def test_decode_gets_exactly_its_bytes_even_with_padding() -> None:
    seen: list[bytes] = []

    def decode(data: bytes) -> float:
        seen.append(data)
        return 0.0

    spec = PidSpec(0x0C, "rpm", "Motordrehzahl", "1/min", 2, decode, 0, 16383.75)
    assert read_value(Elm327(FakeTransport({"010C": "410C1AF8AAAA"})), spec) == 0.0
    assert seen == [bytes([0x1A, 0xF8])]


@pytest.mark.parametrize(("answer", "throttled"), [("11.8V", False), ("11.7V", True)])
def test_throttling_starts_below_the_limit(answer: str, throttled: bool) -> None:
    samples: list[LiveSample] = []
    clock = FakeClock()
    run_live(
        Elm327(FakeTransport({**RUNNING, "ATRV": answer})),
        [PIDS["rpm"]],
        on_sample=samples.append,
        should_stop=lambda: False,
        max_samples=1,
        clock=clock,
        sleep=clock.sleep,
    )
    assert [s.throttled for s in samples] == [throttled]


def test_recorder_keeps_its_path(tmp_path: Path) -> None:
    path = tmp_path / "live.csv"
    with live.LiveRecorder(path, [PIDS["rpm"]]) as recorder:
        assert recorder.path == path


def test_values_of_one_pid_are_read_with_one_query_per_round() -> None:
    transport = WireCheckingTransport({**RUNNING, "0114": "41145A80", "0167": "4167037B50"})
    samples: list[LiveSample] = []
    clock = FakeClock()
    specs = [PIDS[k] for k in ("o2_s1_voltage", "rpm", "o2_s1_trim", "coolant_temp_2")]
    run_live(
        Elm327(transport),
        specs,
        on_sample=samples.append,
        should_stop=lambda: False,
        max_samples=2,
        clock=clock,
        sleep=clock.sleep,
    )
    assert transport.sent == ["ATRV", "0114", "010C", "0167", "0114", "010C", "0167"]
    assert samples[0].values == {
        "o2_s1_voltage": 0.45,
        "rpm": 1726.0,
        "o2_s1_trim": 0.0,
        "coolant_temp_2": 40,
    }
    assert list(samples[0].values) == [s.key for s in specs]  # Reihenfolge der Auswahl
