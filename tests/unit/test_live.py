"""Live-Dienst: Taktung, Spannung, Drosselung, Fehler, Aufzeichnung, gesendete Befehle.

Die PID-Tabelle ersetzt ``tests.live_fakes``; nur ``PidSpec`` kommt aus
``protocol.pids``. Die Uhr ist ein Fake, es wird also nie wirklich gewartet.
"""

import contextlib
import re
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from obd_diag.protocol import pids
from obd_diag.protocol.elm327 import CLEAR_COMMAND, Elm327, ElmError, is_read_only
from obd_diag.services.live import (
    DEFAULT_KEYS,
    LOW_VOLTAGE_INTERVAL,
    MAX_FAILED_ROUNDS,
    VOLTAGE_EVERY,
    LiveRecorder,
    LiveSample,
    LiveSetup,
    SelectionError,
    new_recording_path,
    prepare_live,
    recording_dir,
    run_live,
    select_pids,
)
from obd_diag.transport import TransportError, TransportTimeout
from tests import live_fakes
from tests.fakes import CAN_CAR, FakeTransport
from tests.live_fakes import COOLANT, LIVE_VALUES, LOAD, RPM, SPEED, use_fake_pids
from tests.unit.test_command_guard import INIT, PROTOCOL, WireCheckingTransport

CAR = {**CAN_CAR, **LIVE_VALUES}


class FakeClock:
    """Uhr, die nur beim Schlafen vorrückt; merkt sich jeden Schlaf."""

    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        assert seconds > 0
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture(autouse=True)
def _fake_pids(monkeypatch: pytest.MonkeyPatch) -> None:
    use_fake_pids(monkeypatch)


def _run(
    elm: Elm327,
    specs: list[pids.PidSpec],
    clock: FakeClock,
    *,
    max_samples: int | None = None,
    stop_at: float | None = None,
    interval: float = 1.0,
    recorder: LiveRecorder | None = None,
) -> tuple[int, list[LiveSample]]:
    samples: list[LiveSample] = []
    count = run_live(
        elm,
        specs,
        on_sample=samples.append,
        should_stop=lambda: stop_at is not None and clock.now >= stop_at,
        interval=interval,
        max_samples=max_samples,
        recorder=recorder,
        clock=clock,
        sleep=clock.sleep,
    )
    return count, samples


# --- prepare_live / select_pids -----------------------------------------------------


def test_prepare_live_reads_supported_and_known_pids() -> None:
    elm = Elm327(WireCheckingTransport(CAR))
    setup = prepare_live(elm)
    assert setup.adapter == "ELM327 v1.5"
    assert setup.protocol == "ISO 15765-4 (CAN 11/500)"
    # 0x0B unterstützt, aber unbekannt; MAF bekannt, aber nicht unterstützt
    assert setup.available == [LOAD, COOLANT, RPM, SPEED]
    assert elm.transport.sent == [*INIT, *PROTOCOL, "0100"]  # type: ignore[attr-defined]


def test_prepare_live_without_supported_pids() -> None:
    setup = prepare_live(Elm327(FakeTransport({**CAR, "0100": "NO DATA"})))
    assert setup.available == []


SETUP = LiveSetup("ELM327 v1.5", "ISO 15765-4 (CAN 11/500)", [LOAD, COOLANT, RPM, SPEED])


def test_select_defaults_in_default_order_and_skips_unsupported() -> None:
    assert DEFAULT_KEYS[:4] == ("rpm", "speed", "coolant_temp", "engine_load")
    assert select_pids(SETUP, None) == [RPM, SPEED, COOLANT, LOAD]
    assert select_pids(LiveSetup("", "", [SPEED]), None) == [SPEED]


@pytest.mark.parametrize("available", [[], [pids.PidSpec(0x10, "maf", "", "", 2, float, 0, 1)]])
def test_select_without_any_default_is_refused(available: list[pids.PidSpec]) -> None:
    # Eine Abfrage ohne Werte liest nichts; das soll der Nutzer erfahren
    with pytest.raises(SelectionError, match="keinen der Standardwerte"):
        select_pids(LiveSetup("", "", available), None)


def test_select_can_skip_unsupported() -> None:
    assert select_pids(SETUP, ["maf", "speed"], skip_unsupported=True) == [SPEED]
    with pytest.raises(SelectionError, match="nicht unterstützt: maf"):
        select_pids(SETUP, ["maf"], skip_unsupported=True)
    with pytest.raises(SelectionError, match="unbekannte Werte: foo"):
        select_pids(SETUP, ["foo", "speed"], skip_unsupported=True)


def test_select_keeps_user_order_without_duplicates() -> None:
    assert select_pids(SETUP, ["speed", "rpm", "speed"]) == [SPEED, RPM]


@pytest.mark.parametrize(
    ("keys", "message"),
    [
        (["rpm", "foo"], "unbekannte Werte: foo"),
        (["maf"], "vom Fahrzeug nicht unterstützt: maf"),
        (["foo", "maf"], "unbekannte Werte: foo; vom Fahrzeug nicht unterstützt: maf"),
        ([], "keine Werte gewählt"),
    ],
)
def test_select_refuses_unknown_and_unsupported(keys: list[str], message: str) -> None:
    with pytest.raises(SelectionError, match=re.escape(message)) as info:
        select_pids(SETUP, keys)
    if keys:
        assert "verfügbar: engine_load, coolant_temp, rpm, speed" in str(info.value)


# --- run_live: Takt, Stopp, Anzahl ------------------------------------------------


def test_rounds_follow_the_interval_in_small_sleeps() -> None:
    clock = FakeClock()
    count, samples = _run(Elm327(FakeTransport(CAR)), [RPM, SPEED], clock, max_samples=3)
    assert count == 3
    assert [s.elapsed for s in samples] == pytest.approx([0.0, 1.0, 2.0])
    assert samples[0].values == {"rpm": 1726.0, "speed": 50.0}
    assert all(0 < s <= 0.1 + 1e-9 for s in clock.sleeps)
    assert sum(clock.sleeps) == pytest.approx(2.0)  # nach der letzten Runde kein Warten


def test_slow_round_is_followed_immediately() -> None:
    clock = FakeClock()

    class Slow(FakeTransport):
        def write(self, data: bytes) -> None:
            clock.now += 0.7  # jede Anfrage dauert 0,7 s
            super().write(data)

    count, samples = _run(Elm327(Slow(CAR)), [RPM, SPEED], clock, max_samples=3)
    assert count == 3
    # Runde 0: ATRV + 2 Werte = 2,1 s; danach je 1,4 s, ohne zusätzliches Warten
    assert [s.elapsed for s in samples] == pytest.approx([0.0, 2.1, 3.5])
    assert clock.sleeps == []


def test_should_stop_ends_the_wait_quickly() -> None:
    clock = FakeClock()
    count, _ = _run(Elm327(FakeTransport(CAR)), [RPM], clock, stop_at=101.55, interval=5)
    assert count == 1
    assert clock.now <= 101.65  # höchstens ein Schritt von 0,1 s nach dem Stoppsignal


def test_should_stop_before_the_first_round_sends_nothing() -> None:
    transport = FakeTransport(CAR)
    count = run_live(Elm327(transport), [RPM], on_sample=lambda s: None, should_stop=lambda: True)
    assert count == 0
    assert transport.sent == []


def test_max_samples_zero_sends_nothing() -> None:
    transport = FakeTransport(CAR)
    count, _ = _run(Elm327(transport), [RPM], FakeClock(), max_samples=0)
    assert count == 0
    assert transport.sent == []


# --- Bordspannung und Drosselung ---------------------------------------------------


def test_voltage_is_read_in_round_zero_and_every_nth_round() -> None:
    transport = FakeTransport(dict(CAR))
    clock = FakeClock()
    count, samples = _run(Elm327(transport), [RPM], clock, max_samples=VOLTAGE_EVERY + 2)
    assert count == VOLTAGE_EVERY + 2
    assert transport.sent.count("ATRV") == 2
    assert transport.sent[0] == "ATRV"
    assert transport.sent[1 + VOLTAGE_EVERY] == "ATRV"  # vor Runde VOLTAGE_EVERY
    assert all(s.voltage == 12.4 and not s.throttled for s in samples)


def test_low_voltage_throttles_until_it_recovers() -> None:
    responses = {**CAR, "ATRV": "11.5V"}
    clock = FakeClock()

    def recover(sample: LiveSample) -> None:
        samples.append(sample)
        responses["ATRV"] = "12.6V"  # ab der nächsten Messung wieder in Ordnung

    samples: list[LiveSample] = []
    count = run_live(
        Elm327(FakeTransport(responses)),
        [RPM],
        on_sample=recover,
        should_stop=lambda: False,
        interval=1.0,
        max_samples=VOLTAGE_EVERY + 2,
        clock=clock,
        sleep=clock.sleep,
    )
    assert count == VOLTAGE_EVERY + 2
    assert [s.throttled for s in samples] == [True] * VOLTAGE_EVERY + [False, False]
    assert samples[0].voltage == 11.5
    assert samples[VOLTAGE_EVERY - 1].voltage == 11.5  # zuletzt gelesener Wert
    assert samples[VOLTAGE_EVERY].voltage == 12.6
    times = [s.elapsed for s in samples]
    assert times[1] == pytest.approx(LOW_VOLTAGE_INTERVAL)
    assert times[VOLTAGE_EVERY] == pytest.approx(VOLTAGE_EVERY * LOW_VOLTAGE_INTERVAL)
    assert times[VOLTAGE_EVERY + 1] - times[VOLTAGE_EVERY] == pytest.approx(1.0)


def test_unreadable_voltage_keeps_throttling() -> None:
    responses = {**CAR, "ATRV": "11.2V"}
    samples: list[LiveSample] = []

    def unreadable(sample: LiveSample) -> None:
        samples.append(sample)
        responses["ATRV"] = "?"  # Batterie bleibt schwach, nur die Messung scheitert

    clock = FakeClock()
    run_live(
        Elm327(FakeTransport(responses)),
        [RPM],
        on_sample=unreadable,
        should_stop=lambda: False,
        max_samples=VOLTAGE_EVERY + 2,
        clock=clock,
        sleep=clock.sleep,
    )
    assert all(s.throttled for s in samples)
    assert samples[VOLTAGE_EVERY].voltage is None
    last = samples[VOLTAGE_EVERY + 1].elapsed - samples[VOLTAGE_EVERY].elapsed
    assert last == pytest.approx(LOW_VOLTAGE_INTERVAL)


def test_throttling_never_speeds_up_a_slow_interval() -> None:
    clock = FakeClock()
    _, samples = _run(
        Elm327(FakeTransport({**CAR, "ATRV": "11.0V"})), [RPM], clock, max_samples=2, interval=8
    )
    assert samples[1].elapsed == pytest.approx(8.0)


@pytest.mark.parametrize("answer", ["?", "NO DATA", "CAN ERROR", "abc", "V"])
def test_unreadable_voltage_is_none_and_not_throttled(answer: str) -> None:
    _, samples = _run(
        Elm327(FakeTransport({**CAR, "ATRV": answer})), [RPM], FakeClock(), max_samples=2
    )
    assert [(s.voltage, s.throttled) for s in samples] == [(None, False), (None, False)]


# --- Fehler einzelner Werte und Abbruch ----------------------------------------------


def test_refused_values_are_none_in_that_round() -> None:
    responses = {**CAR, "010C": "?", "010D": "NO DATA", "0105": "CAN ERROR", "0104": "7F0112"}
    _, samples = _run(
        Elm327(FakeTransport(responses)), [RPM, SPEED, COOLANT, LOAD], FakeClock(), max_samples=1
    )
    assert samples[0].values == {
        "rpm": None,
        "speed": None,
        "coolant_temp": None,
        "engine_load": None,
    }


@pytest.mark.parametrize("error", ["CAN ERROR", "UNABLE TO CONNECT", "BUS ERROR", "STOPPED"])
def test_bus_errors_on_every_value_abort_after_some_rounds(error: str) -> None:
    responses = dict(CAR)
    samples: list[LiveSample] = []

    def ignition_off(sample: LiveSample) -> None:
        samples.append(sample)
        responses.update({"010C": error, "010D": error})

    with pytest.raises(ElmError, match=f"{MAX_FAILED_ROUNDS} Runden nacheinander"):
        run_live(
            Elm327(FakeTransport(responses)),
            [RPM, SPEED],
            on_sample=ignition_off,
            should_stop=lambda: False,
            clock=(clock := FakeClock()),
            sleep=clock.sleep,
        )
    # Runde 0 gültig, danach MAX_FAILED_ROUNDS - 1 gemeldete Runden ohne Werte
    assert len(samples) == MAX_FAILED_ROUNDS
    assert samples[-1].values == {"rpm": None, "speed": None}


def test_bus_error_on_some_values_or_now_and_then_does_not_abort() -> None:
    # Ein Wert scheitert dauernd, der andere kommt: kein Abbruch
    count, _ = _run(
        Elm327(FakeTransport({**CAR, "010D": "CAN ERROR"})),
        [RPM, SPEED],
        FakeClock(),
        max_samples=2 * MAX_FAILED_ROUNDS,
    )
    assert count == 2 * MAX_FAILED_ROUNDS
    # Alle scheitern, aber nie MAX_FAILED_ROUNDS Runden am Stück
    responses = dict(CAR)
    rounds: list[LiveSample] = []

    def flaky(sample: LiveSample) -> None:
        rounds.append(sample)
        broken = len(rounds) % MAX_FAILED_ROUNDS != 0
        responses["010C"] = "CAN ERROR" if broken else CAR["010C"]

    run_live(
        Elm327(FakeTransport(responses)),
        [RPM],
        on_sample=flaky,
        should_stop=lambda: False,
        max_samples=3 * MAX_FAILED_ROUNDS,
        clock=(clock := FakeClock()),
        sleep=clock.sleep,
    )
    assert len(rounds) == 3 * MAX_FAILED_ROUNDS


def test_value_error_from_decoding_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(elm: Elm327, spec: pids.PidSpec) -> float | None:
        if spec is SPEED:
            raise ValueError("kaputte Antwort")
        return live_fakes.fake_read_value(elm, spec)

    monkeypatch.setattr(pids, "read_value", broken)
    _, samples = _run(Elm327(FakeTransport(CAR)), [RPM, SPEED], FakeClock(), max_samples=1)
    assert samples[0].values == {"rpm": 1726.0, "speed": None}


def test_transport_error_aborts(tmp_path: Path) -> None:
    class Unplugged(FakeTransport):
        def write(self, data: bytes) -> None:
            super().write(data)
            if len(self.sent) > 4:  # ab der zweiten Runde (ATRV, 010C, 010D, 010C ...)
                self._pending = b""

    samples: list[LiveSample] = []
    path = tmp_path / "live.csv"
    clock = FakeClock()
    with pytest.raises(TransportTimeout), LiveRecorder(path, [RPM, SPEED]) as recorder:
        run_live(
            Elm327(Unplugged(CAR)),
            [RPM, SPEED],
            on_sample=samples.append,
            should_stop=lambda: False,
            recorder=recorder,
            clock=clock,
            sleep=clock.sleep,
        )
    assert len(samples) == 1
    assert len(path.read_text(encoding="utf-8-sig").splitlines()) == 2  # Kopf + Runde 0


def test_forbidden_command_is_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    def writes(elm: Elm327, spec: pids.PidSpec) -> float | None:
        elm.command("1101")  # Programmierfehler: kein ElmError, darf nicht zu None werden
        return None

    monkeypatch.setattr(pids, "read_value", writes)
    transport = FakeTransport(CAR)
    with pytest.raises(Exception, match="nicht freigegeben") as info:
        _run(Elm327(transport), [RPM], FakeClock(), max_samples=1)
    assert not isinstance(info.value, ElmError | TransportError)
    assert "1101" not in transport.sent


# --- Aufzeichnung ------------------------------------------------------------------


def test_recorder_writes_csv_like_export(tmp_path: Path) -> None:
    path = tmp_path / "live.csv"
    responses = {**CAR, "010D": "NO DATA"}
    with LiveRecorder(path, [RPM, SPEED, LOAD]) as recorder:
        _run(
            Elm327(FakeTransport(responses)),
            [RPM, SPEED, LOAD],
            FakeClock(),
            max_samples=2,
            interval=0.25,
            recorder=recorder,
        )
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # BOM für Excel
    assert raw.count(b"\r\n") == 3
    assert raw.decode("utf-8-sig").splitlines() == [
        "Zeit (s);Motordrehzahl (1/min);Geschwindigkeit (km/h);Motorlast (%);Bordspannung (V)",
        "0;1726;;50,196;12,4",
        "0,25;1726;;50,196;12,4",
    ]


def test_recorder_number_format(tmp_path: Path) -> None:
    path = tmp_path / "live.csv"
    with LiveRecorder(path, [COOLANT]) as recorder:
        recorder.add(LiveSample(1234.5678, {"coolant_temp": -40.0}, None, False))
        recorder.add(LiveSample(0.0004, {"coolant_temp": -0.0001}, 13.95, True))
        recorder.add(LiveSample(2.0, {}, 12.0, False))  # fehlender Schlüssel: leer
    assert path.read_text(encoding="utf-8-sig").splitlines()[1:] == [
        "1234,568;-40;",
        "0;0;13,95",
        "2;;12",
    ]


def test_recorder_flushes_every_row(tmp_path: Path) -> None:
    path = tmp_path / "live.csv"
    recorder = LiveRecorder(path, [RPM])
    try:
        assert (
            path.read_bytes()
            == "\ufeffZeit (s);Motordrehzahl (1/min);Bordspannung (V)\r\n".encode()
        )
        recorder.add(LiveSample(0.0, {"rpm": 800.0}, 14.1, False))
        assert path.read_bytes().endswith(b"\r\n0;800;14,1\r\n")
    finally:
        recorder.close()


def test_recorder_never_overwrites(tmp_path: Path) -> None:
    path = tmp_path / "live.csv"
    path.write_text("alt", encoding="utf-8")
    with pytest.raises(FileExistsError):
        LiveRecorder(path, [RPM])
    assert path.read_text(encoding="utf-8") == "alt"


def test_recording_dir_follows_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert recording_dir() == tmp_path / "obd-diag" / "recordings"


def test_new_recording_path_is_free_and_creates_the_folder(tmp_path: Path) -> None:
    directory = tmp_path / "a" / "recordings"
    first = new_recording_path(directory)
    assert directory.is_dir()
    assert re.fullmatch(r"live-\d{8}-\d{6}\.csv", first.name)
    first.touch()
    second = new_recording_path(directory)
    assert second != first
    assert re.fullmatch(r"live-\d{8}-\d{6}(-2)?\.csv", second.name)


# --- gesendete Befehle -------------------------------------------------------------


def test_run_live_sends_exactly() -> None:
    elm = Elm327(WireCheckingTransport(CAR))
    _run(elm, [RPM, SPEED], FakeClock(), max_samples=2)
    sent = elm.transport.sent  # type: ignore[attr-defined]
    assert sent == ["ATRV", "010C", "010D", "010C", "010D"]
    assert all(is_read_only(c) for c in sent)


def test_prepare_and_run_send_only_reads_over_many_rounds() -> None:
    elm = Elm327(WireCheckingTransport(CAR))
    setup = prepare_live(elm)
    _run(elm, select_pids(setup, None), FakeClock(), max_samples=25)
    sent = elm.transport.sent  # type: ignore[attr-defined]
    assert sent[: len(INIT) + len(PROTOCOL) + 1] == [*INIT, *PROTOCOL, "0100"]
    assert sent.count("ATRV") == 3  # Runden 0, 10, 20
    assert all(is_read_only(c) for c in sent)


_GARBAGE = st.sampled_from(
    ["NO DATA", "?", "CAN ERROR", "STOPPED", "7F0112", "41", "410C", "ZZ", "", "OK", "4.V"]
)
_LIVE_COMMANDS = [*INIT, *PROTOCOL, "0100", "ATRV", *LIVE_VALUES]


def _guarded_run(transport: WireCheckingTransport) -> None:
    clock = FakeClock()
    with contextlib.suppress(ElmError, ValueError, TransportError):  # Abbruch ist in Ordnung
        elm = Elm327(transport)
        setup = prepare_live(elm)
        try:
            specs = select_pids(setup, None)
        except SelectionError:
            specs = list(setup.available)
        _run(elm, specs, clock, max_samples=VOLTAGE_EVERY + 1)


@given(st.dictionaries(st.sampled_from(_LIVE_COMMANDS), _GARBAGE))
def test_broken_answers_never_lead_to_writes(broken: dict[str, str]) -> None:
    with pytest.MonkeyPatch.context() as mp:
        use_fake_pids(mp)
        transport = WireCheckingTransport({**CAR, **broken})
        _guarded_run(transport)
    assert CLEAR_COMMAND not in transport.sent
    assert all(is_read_only(c) for c in transport.sent), transport.sent


# Sobald die echte PID-Tabelle da ist: dieselbe Garantie mit ihr statt mit den Fakes.
_REAL_PIDS = dict(pids.PIDS)
_REAL_FUNCTIONS = (pids.read_supported_pids, pids.read_value, pids.pid_by_key)


@pytest.mark.skipif(not _REAL_PIDS, reason="protocol.pids noch nicht implementiert")
@given(st.dictionaries(st.sampled_from([*_LIVE_COMMANDS, "0120", "0140"]), _GARBAGE))
def test_real_pid_table_never_leads_to_writes(broken: dict[str, str]) -> None:
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(pids, "PIDS", _REAL_PIDS)
        mp.setattr(pids, "read_supported_pids", _REAL_FUNCTIONS[0])
        mp.setattr(pids, "read_value", _REAL_FUNCTIONS[1])
        mp.setattr(pids, "pid_by_key", _REAL_FUNCTIONS[2])
        transport = WireCheckingTransport({**CAR, **broken})
        _guarded_run(transport)
    assert CLEAR_COMMAND not in transport.sent
    assert all(is_read_only(c) for c in transport.sent), transport.sent
