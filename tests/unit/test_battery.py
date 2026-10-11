"""Batterie- und Anlasstest (``services.battery``) an einem simulierten Auto."""

import pytest

from obd_diag.protocol.elm327 import Elm327, is_read_only
from obd_diag.services import battery
from obd_diag.services.battery import (
    BatteryTestCancelled,
    BatteryTestRefused,
    BatteryTestResult,
    Phase,
    Rating,
    charge_verdict,
    crank_limit,
    crank_verdict,
    notes,
    overall_rating,
    rest_verdict,
    result_to_dict,
    run_battery_test,
)
from obd_diag.transport import TransportError
from tests.battery_fakes import REST, RUNNING, SimCar, SimClock, State, healthy

INIT = ["ATZ", "ATE0", "ATL0", "ATS0", "ATH0", "ATSP0"]
PROTOCOL = ["0100", "ATDPN", "ATDP"]


def _run(car: SimCar, **kwargs: object) -> BatteryTestResult:
    return run_battery_test(
        Elm327(car),
        on_progress=car.on_progress,
        clock=car.clock,
        sleep=car.clock.sleep,
        **kwargs,  # type: ignore[arg-type]
    )


def _car(profile=healthy, **kwargs: object) -> SimCar:  # type: ignore[no-untyped-def]
    return SimCar(SimClock(), profile, **kwargs)  # type: ignore[arg-type]


def test_healthy_battery() -> None:
    car = _car()
    result = _run(car)
    assert result.adapter == "ELM327 v1.5"
    assert result.rest_adapter == 12.0
    assert result.rest_ecu == 12.8
    assert result.offset == pytest.approx(0.8)
    assert result.coolant_temp == 20
    assert result.crank_detected
    # Einschaltspitze 8,0 V zählt nur als absoluter Tiefstwert
    assert result.crank_lowest == pytest.approx(8.8)
    assert result.crank_minimum == pytest.approx(10.1)
    assert result.crank_seconds == pytest.approx(0.8, abs=0.05)
    assert result.started
    assert result.charge_ecu == 14.4
    assert result.charge_adapter == pytest.approx(14.4)
    assert not result.adapter_reset
    assert [rest_verdict(result).rating, crank_verdict(result).rating] == [Rating.GOOD] * 2
    assert charge_verdict(result).rating is Rating.GOOD
    assert overall_rating(result) is Rating.GOOD
    assert car.phases == [Phase.PREPARE, Phase.REST, Phase.START, Phase.CRANKING, Phase.RUNNING]


def test_sends_only_read_commands_in_order() -> None:
    car = _car()
    _run(car)
    assert car.sent[:11] == [*INIT, *PROTOCOL, "010C", "0105"]
    assert car.sent[11] == "0142"  # Steuergerätespannung zu Beginn der Ruhe
    assert set(car.sent) == {*INIT, *PROTOCOL, "010C", "0105", "0142", "ATRV"}
    assert all(is_read_only(c) for c in car.sent)


def test_rest_phase_samples_atrv_often() -> None:
    car = _car()
    _run(car)
    rest = [t for t, c in car.timeline if c == "ATRV" and t < car.prompted_at]  # type: ignore[operator]
    # 3 s bei 15 ms Antwortzeit und höchstens 50 Messungen je Sekunde
    assert 140 <= len(rest) <= 151


def test_no_rpm_request_during_the_dip() -> None:
    car = _car()
    _run(car)
    assert car.prompted_at is not None
    crank = [c for t, c in car.timeline if 2.06 <= t - car.prompted_at < 2.75]
    assert crank and set(crank) == {"ATRV"}


def test_weak_battery_is_rated_bad() -> None:
    def weak(t: float) -> State:
        if t < 1.0:
            return REST
        return State("8.5V", 150, 9.3) if t < 2.5 else RUNNING

    result = _run(_car(weak))
    assert result.crank_minimum == pytest.approx(9.3)
    assert crank_verdict(result).rating is Rating.BAD
    assert overall_rating(result) is Rating.BAD


def test_marginal_cranking_voltage() -> None:
    def marginal(t: float) -> State:
        if t < 1.0:
            return REST
        return State("8.8V", 150, 9.6) if t < 2.0 else RUNNING

    result = _run(_car(marginal))
    assert result.crank_minimum == pytest.approx(9.6)  # Grenze bei 20 °C: 9,5 V
    assert crank_verdict(result).rating is Rating.WEAK


@pytest.mark.parametrize(
    ("temp", "limit"),
    [
        (None, 9.6),
        (90, 9.6),
        (21, 9.6),
        (20.9, 9.5),
        (16, 9.5),
        (10, 9.4),
        (4, 9.3),
        (0, 9.1),
        (-1, 9.1),
        (-5, 8.9),
        (-12, 8.7),
        (-18, 8.5),
        (-40, 8.5),
    ],
)
def test_crank_limit_by_temperature(temp: float | None, limit: float) -> None:
    assert crank_limit(temp) == limit


def test_engine_running_is_refused_before_measuring() -> None:
    car = _car(rest=RUNNING)
    with pytest.raises(BatteryTestRefused, match="läuft"):
        _run(car)
    assert car.sent == [*INIT, *PROTOCOL, "010C"]


def test_high_voltage_without_rpm_is_refused() -> None:
    car = _car(rest=State("13.9V", 0, 14.6), rpm_unreadable=True)
    with pytest.raises(BatteryTestRefused, match="laufendem Motor"):
        _run(car)
    assert car.prompted_at is None


def test_unreadable_adapter_voltage_is_refused() -> None:
    car = _car(rest=State("?", 0, 12.8))
    with pytest.raises(BatteryTestRefused, match="ATRV"):
        _run(car)


def test_without_ecu_voltage_no_correction() -> None:
    def no_ecu(t: float) -> State:
        s = healthy(t)
        return State(s.atrv, s.rpm, None)

    car = _car(no_ecu, rest=State("12.4V", 0, None))
    result = _run(car)
    assert result.rest_ecu is None
    assert result.offset == 0
    assert result.rest_voltage == 12.4
    assert result.crank_minimum == pytest.approx(9.3)
    assert result.charge_voltage == pytest.approx(13.6)
    assert any("PID 42" in n for n in notes(result))


def test_implausible_offset_is_not_applied() -> None:
    car = _car(rest=State("10.0V", 0, 12.6))  # 2,6 V Abweichung: kein Diodenverlust
    result = _run(car, start_timeout=1.0)
    assert result.offset == 0


def test_engine_does_not_start() -> None:
    def no_start(t: float) -> State:
        return State("9.3V", 150, 10.0) if 1.0 <= t < 3.0 else REST

    result = _run(_car(no_start), start_timeout=10.0)
    assert result.crank_detected
    assert not result.started
    assert result.charge_voltage is None
    assert charge_verdict(result).rating is Rating.UNKNOWN
    assert crank_verdict(result).rating is Rating.GOOD
    assert any("nicht angesprungen" in n for n in notes(result))


def test_nothing_happens_until_timeout() -> None:
    car = _car(lambda t: REST)
    result = _run(car, start_timeout=5.0)
    assert not result.crank_detected and not result.started
    assert crank_verdict(result).text == "kein Anlassen erkannt"
    assert overall_rating(result) is Rating.GOOD  # nur die Ruhespannung ist bewertet
    assert car.prompted_at is not None
    assert car.clock() - car.prompted_at == pytest.approx(5.0, abs=0.1)


def test_start_without_visible_dip() -> None:
    result = _run(_car(lambda t: RUNNING if t >= 1.0 else REST))
    assert result.started and not result.crank_detected
    assert crank_verdict(result).text == "kein Spannungseinbruch erkannt"


def test_adapter_reset_while_cranking_reinitializes() -> None:
    def brownout(t: float) -> State:
        if 2.0 <= t < 2.03:
            return State("LV RESET", 0, None)
        return healthy(t)

    car = _car(brownout)
    result = _run(car)
    assert result.adapter_reset
    assert car.sent.count("ATZ") == 2
    assert result.started
    assert any("neu" in n for n in notes(result))


def test_adapter_banner_instead_of_voltage_counts_as_reset() -> None:
    def brownout(t: float) -> State:
        if 2.0 <= t < 2.03:
            return State("ELM327 v1.5", 0, None)
        return healthy(t)

    result = _run(_car(brownout))
    assert result.adapter_reset


def test_reset_without_any_dip_sample() -> None:
    result = BatteryTestResult(
        "ELM327", 12.0, None, 0.0, None, False, None, None, None, True, 14.0, None, True, ()
    )
    assert crank_verdict(result).rating is Rating.WEAK


def test_cancel_stops_the_test() -> None:
    car = _car()
    with pytest.raises(BatteryTestCancelled):
        run_battery_test(
            Elm327(car),
            on_progress=car.on_progress,
            should_stop=lambda: car.prompted_at is not None,
            clock=car.clock,
            sleep=car.clock.sleep,
        )


def test_transport_error_passes_through() -> None:
    class Unplugged(SimCar):
        def write(self, data: bytes) -> None:
            if data == b"ATRV\r" and self.prompted_at is not None:
                raise TransportError("weg")
            super().write(data)

    with pytest.raises(TransportError):
        _run(Unplugged(SimClock()))


def test_progress_is_throttled_but_phase_changes_are_immediate() -> None:
    car = _car()
    _run(car)
    rest = [p for p in car.progress if p.phase is Phase.REST]
    assert len(rest) <= 3 / battery.PROGRESS_INTERVAL + 2
    start = next(p for p in car.progress if p.phase is Phase.START)
    assert start.remaining == pytest.approx(battery.START_TIMEOUT)
    assert all(p.voltage is None or p.voltage > 8 for p in car.progress)


def _result(
    rest: float | None = 12.6, crank: float | None = 10.5, charge: float | None = 14.2
) -> BatteryTestResult:
    return BatteryTestResult(
        "ELM327",
        rest,
        None,
        0.0,
        25.0,
        crank is not None,
        crank,
        crank,
        0.7,
        charge is not None,
        charge,
        None,
        False,
        (),
    )


@pytest.mark.parametrize(
    ("voltage", "rating"),
    [
        (12.7, Rating.GOOD),
        (12.2, Rating.GOOD),
        (12.19, Rating.WEAK),
        (11.8, Rating.WEAK),
        (11.79, Rating.BAD),
        (None, Rating.UNKNOWN),
    ],
)
def test_rest_ratings(voltage: float | None, rating: Rating) -> None:
    assert rest_verdict(_result(rest=voltage)).rating is rating


@pytest.mark.parametrize(
    ("voltage", "rating"),
    [
        (15.01, Rating.BAD),
        (15.0, Rating.GOOD),
        (13.5, Rating.GOOD),
        (13.49, Rating.WEAK),
        (12.8, Rating.WEAK),
        (12.79, Rating.BAD),
    ],
)
def test_charge_ratings(voltage: float, rating: Rating) -> None:
    assert charge_verdict(_result(charge=voltage)).rating is rating


@pytest.mark.parametrize(
    ("voltage", "rating"),
    [(10.0, Rating.GOOD), (9.99, Rating.WEAK), (9.6, Rating.WEAK), (9.59, Rating.BAD)],
)
def test_crank_ratings_at_25_degrees(voltage: float, rating: Rating) -> None:
    assert crank_verdict(_result(crank=voltage)).rating is rating


def test_overall_is_the_worst_known_rating() -> None:
    assert overall_rating(_result()) is Rating.GOOD
    assert overall_rating(_result(rest=12.0)) is Rating.WEAK
    assert overall_rating(_result(rest=12.0, charge=16.0)) is Rating.BAD
    assert overall_rating(_result(rest=None, crank=None, charge=None)) is Rating.UNKNOWN


def test_result_to_dict() -> None:
    data = result_to_dict(_run(_car()))
    assert data["ratings"] == {
        "rest": "good",
        "crank": "good",
        "charge": "good",
        "overall": "good",
    }
    assert data["rest_voltage"] == 12.8
    assert data["crank_limit"] == 9.5
    assert data["curve"] and all(len(point) == 2 for point in data["curve"])
