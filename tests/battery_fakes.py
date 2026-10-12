"""Simuliertes Auto für den Batterie- und Anlasstest: Spannungen und Drehzahl hängen von
einer simulierten Uhr ab.

Bis zur Aufforderung „Motor starten“ (``Phase.START``, über ``SimCar.on_progress``) steht
der Motor; danach liefert ``profile`` den Zustand je Sekunde seit der Aufforderung. Jeder
Befehl kostet Zeit wie am FORScan-Adapter (Adapter-Befehl 15 ms, Anfrage ans Fahrzeug
60 ms); die Mindestpause schaltet ``tests/conftest.py`` ab.
"""

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass

from obd_diag.services.battery import BatteryProgress, Phase
from tests.fakes import CAN_CAR, FakeTransport

AT_COST = 0.015
OBD_COST = 0.06


@dataclass(frozen=True)
class State:
    """Zustand des Autos: Spannung laut Adapter (Text der ATRV-Antwort), Drehzahl,
    Steuergerätespannung (None: PID 42 nicht beantwortet)."""

    atrv: str
    rpm: float
    ecu: float | None


class SimClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        assert seconds >= 0
        self.now += seconds


REST = State("12.0V", 0, 12.8)
RUNNING = State("13.6V", 800, 14.4)


def healthy(t: float) -> State:
    """Anlassen 2 s nach der Aufforderung, 0,8 s lang: kurz 8,0 V, dann 9,3 V (Adapter)."""
    if t < 2.0:
        return REST
    if t < 2.05:
        return State("8.0V", 0, None)
    if t < 2.8:
        return State("9.3V", 200, 10.1)
    return RUNNING


def _word(value: float, scale: float) -> str:
    raw = round(value * scale)
    return f"{raw >> 8:02X}{raw & 0xFF:02X}"


class _Responses(Mapping[str, str]):
    def __init__(self, car: "SimCar") -> None:
        self._car = car

    def __getitem__(self, cmd: str) -> str:
        state = self._car.state()
        if cmd == "ATRV":
            return state.atrv
        if cmd == "010C":
            if self._car.rpm_unreadable:
                return "NO DATA"
            return "\r".join(f"410C{_word(state.rpm, 4)}" for _ in range(2))
        if cmd == "0142":
            return "NO DATA" if state.ecu is None else f"4142{_word(state.ecu, 1000)}"
        if cmd == "0105":
            return f"4105{round(self._car.coolant + 40):02X}"
        return self._car.static[cmd]

    def __iter__(self) -> Iterator[str]:
        return iter(self._car.static)

    def __len__(self) -> int:
        return len(self._car.static)


class SimCar(FakeTransport):
    def __init__(
        self,
        clock: SimClock,
        profile: Callable[[float], State] = healthy,
        *,
        rest: State = REST,
        coolant: float = 20.0,
        rpm_unreadable: bool = False,
    ) -> None:
        self.clock = clock
        self.profile = profile
        self.rest = rest
        self.coolant = coolant
        self.rpm_unreadable = rpm_unreadable
        self.static = dict(CAN_CAR)
        self.prompted_at: float | None = None
        self.phases: list[Phase] = []
        self.progress: list[BatteryProgress] = []
        self.timeline: list[tuple[float, str]] = []  # (Zeit, Befehl)
        super().__init__(_Responses(self))

    def state(self) -> State:
        if self.prompted_at is None:
            return self.rest
        return self.profile(self.clock() - self.prompted_at)

    def on_progress(self, progress: BatteryProgress) -> None:
        self.progress.append(progress)
        if not self.phases or self.phases[-1] is not progress.phase:
            self.phases.append(progress.phase)
        if progress.phase is Phase.START and self.prompted_at is None:
            self.prompted_at = self.clock()

    def write(self, data: bytes) -> None:
        cmd = data.decode("ascii").strip()
        self.timeline.append((self.clock(), cmd))
        super().write(data)
        self.clock.sleep(AT_COST if cmd.startswith("AT") else OBD_COST)
