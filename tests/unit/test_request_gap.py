"""Mindestpause zwischen zwei Anfragen ans Fahrzeug (``MIN_REQUEST_GAP``).

Es ist immer nur eine Anfrage unterwegs (gesendet wird erst nach dem Prompt); die Pause
ist die zweite Grenze dahinter. Die Uhr ist hier simuliert: ``sleep`` stellt sie vor,
jede Antwort braucht ``reply_time`` Sekunden.
"""

from collections.abc import Mapping
from types import TracebackType
from typing import Self

import pytest
from hypothesis import given
from hypothesis import strategies as st

from obd_diag.protocol import elm327
from obd_diag.protocol.elm327 import Elm327, ElmError
from obd_diag.services.session import run_diagnosis
from obd_diag.transport import TransportTimeout
from tests.fakes import CAN_CAR_FULL, FakeTransport

GAP = 0.05


class Clock:
    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        assert seconds > 0
        self.sleeps.append(seconds)
        self.now += seconds


class TimedTransport:
    """``FakeTransport`` mit Zeitstempeln: merkt sich, wann gesendet und wann der Prompt
    gelesen wurde; jede Antwort dauert ``reply_time``."""

    def __init__(
        self,
        clock: Clock,
        responses: Mapping[str, str],
        reply_time: float = 0.0,
        silent: frozenset[str] = frozenset(),
    ) -> None:
        self.inner = FakeTransport(responses)
        self.clock = clock
        self.reply_time = reply_time
        self.silent = silent  # Befehle, auf die keine Antwort kommt (Timeout)
        self.events: list[tuple[str, str, float]] = []  # ("write"/"reply", Befehl, Zeit)
        self._current = ""

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def write(self, data: bytes) -> None:
        self._current = data.decode("ascii").strip()
        self.events.append(("write", self._current, self.clock.now))
        self.inner.write(data)

    def read_until(self, terminator: bytes, timeout: float) -> bytes:
        self.clock.now += self.reply_time
        self.events.append(("reply", self._current, self.clock.now))
        if self._current in self.silent:
            raise TransportTimeout("keine Antwort")
        return self.inner.read_until(terminator, timeout)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        pass


def _elm(transport: TimedTransport, clock: Clock, gap: float = GAP) -> Elm327:
    return Elm327(transport, min_request_gap=gap, clock=clock, sleep=clock.sleep)


def _assert_spaced(events: list[tuple[str, str, float]], gap: float) -> None:
    """Jede Anfrage ans Fahrzeug folgt frühestens ``gap`` nach der vorigen Antwort, und
    gesendet wird nie, bevor die vorige Antwort gelesen ist."""
    last_reply: float | None = None
    waiting = False
    for kind, cmd, at in events:
        if kind == "reply":
            last_reply, waiting = at, False
            continue
        assert not waiting, f"{cmd} gesendet, bevor die vorige Antwort da war"
        waiting = True
        if not cmd.startswith("AT") and last_reply is not None:
            assert at - last_reply >= gap - 1e-9, f"{cmd} nach {at - last_reply:.3f} s"


def test_obd_requests_are_spaced() -> None:
    clock = Clock()
    transport = TimedTransport(clock, {"010C": "410C0000", "010D": "410D00"})
    elm = _elm(transport, clock)
    for _ in range(5):
        elm.command("010C")
        elm.command("010D")
    _assert_spaced(transport.events, GAP)
    assert clock.sleeps == [pytest.approx(GAP)] * 9  # vor der ersten Anfrage keine Pause


def test_adapter_commands_are_not_delayed() -> None:
    clock = Clock()
    transport = TimedTransport(clock, {})
    elm = _elm(transport, clock)
    elm.initialize()
    elm.command("ATRV")
    assert clock.sleeps == []


def test_slow_reply_needs_no_extra_pause() -> None:
    clock = Clock()
    transport = TimedTransport(clock, {"010C": "410C0000"}, reply_time=0.2)
    elm = _elm(transport, clock)
    elm.command("010C")
    clock.now += GAP  # die Pause ist schon vergangen
    elm.command("010C")
    assert clock.sleeps == []


def test_pause_counts_from_the_reply_not_from_sending() -> None:
    clock = Clock()
    transport = TimedTransport(clock, {"010C": "410C0000"}, reply_time=0.03)
    elm = _elm(transport, clock)
    elm.command("010C")
    elm.command("010C")
    assert clock.sleeps == [pytest.approx(GAP)]


def test_pause_also_after_errors_and_timeouts() -> None:
    clock = Clock()
    transport = TimedTransport(clock, {"0100": "CAN ERROR"}, silent=frozenset({"0902"}))
    elm = _elm(transport, clock)
    with pytest.raises(ElmError):
        elm.command("0100")
    with pytest.raises(TransportTimeout):
        elm.command("0902")
    elm.command("03")
    _assert_spaced(transport.events, GAP)
    assert len(clock.sleeps) == 2


def test_full_diagnosis_respects_the_pause() -> None:
    clock = Clock()
    transport = TimedTransport(clock, CAN_CAR_FULL)
    run_diagnosis(_elm(transport, clock), None)
    requests = [cmd for kind, cmd, _ in transport.events if kind == "write"]
    assert len([c for c in requests if not c.startswith("AT")]) > 5
    _assert_spaced(transport.events, GAP)


@given(
    commands=st.lists(
        st.sampled_from(["ATRV", "ATDPN", "0100", "010C", "03", "07", "0A", "0902", "020C00"]),
        max_size=30,
    ),
    reply_time=st.floats(0, 0.2),
    idle=st.lists(st.floats(0, 0.1), max_size=30),
)
def test_any_command_sequence_is_spaced(
    commands: list[str], reply_time: float, idle: list[float]
) -> None:
    clock = Clock()
    transport = TimedTransport(clock, {}, reply_time=reply_time)
    elm = _elm(transport, clock)
    for i, cmd in enumerate(commands):
        elm.command(cmd)
        clock.now += idle[i] if i < len(idle) else 0.0
    _assert_spaced(transport.events, GAP)


@pytest.mark.request_gap
def test_shipped_gap() -> None:
    """Ausgeliefert: höchstens 20 Anfragen je Sekunde ans Fahrzeug."""
    assert elm327.MIN_REQUEST_GAP == GAP
    assert Elm327(FakeTransport({})).min_request_gap == GAP


@pytest.mark.request_gap
def test_shipped_gap_with_real_clock() -> None:
    import time

    transport = FakeTransport({"010C": "410C0000"})
    elm = Elm327(transport)
    start = time.monotonic()
    for _ in range(4):
        elm.command("010C")
    assert time.monotonic() - start >= 3 * GAP
