"""Batterie- und Anlasstest: Grenzwerte, unlesbare Antworten, Zeitverhalten, CLI, Englisch.

Ergänzt ``test_battery.py`` (Ablauf am simulierten Auto) und ``test_battery_demo_cli.py``
(Demo-Fahrzeug). PID 42 (Steuergerätespannung) ist nach SAE J1979 ``(256 A + B) / 1000`` V,
PID 0C (Drehzahl) ``(256 A + B) / 4`` 1/min, PID 05 (Kühlmittel) ``A - 40`` °C.
"""

import json
from collections.abc import Callable
from itertools import pairwise
from typing import Any

import pytest

from obd_diag import cli, i18n
from obd_diag.protocol.elm327 import Elm327, NoConnectionError, is_read_only
from obd_diag.services import battery
from obd_diag.services.battery import (
    CRANK_LIMITS,
    BatteryProgress,
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
    overall_text,
    rest_verdict,
    result_to_dict,
    run_battery_test,
)
from obd_diag.transport import TransportError
from tests.battery_fakes import REST, RUNNING, SimCar, SimClock, State, healthy
from tests.fakes import CAN_CAR, FakeTransport

INIT = ["ATZ", "ATE0", "ATL0", "ATS0", "ATH0", "ATSP0"]
PROTOCOL = ["0100", "ATDPN", "ATDP"]
PREPARE = [*INIT, *PROTOCOL, "010C", "0105"]


def _run(car: SimCar, **kwargs: Any) -> BatteryTestResult:
    return run_battery_test(
        Elm327(car),
        on_progress=car.on_progress,
        clock=car.clock,
        sleep=car.clock.sleep,
        **kwargs,
    )


def _car(profile: Callable[[float], State] = healthy, **kwargs: Any) -> SimCar:
    return SimCar(SimClock(), profile, **kwargs)


def _after(start: float, state: State) -> Callable[[float], State]:
    """Ruhe bis ``start`` Sekunden nach der Aufforderung, danach ``state``."""
    return lambda t: REST if t < start else state


def _result(
    *,
    rest_adapter: float | None = 12.0,
    rest_ecu: float | None = 12.6,
    offset: float = 0.6,
    coolant: float | None = 25.0,
    crank: float | None = 10.5,
    started: bool = True,
    charge_adapter: float | None = None,
    charge_ecu: float | None = 14.2,
    reset: bool = False,
) -> BatteryTestResult:
    return BatteryTestResult(
        "ELM327 v1.5",
        rest_adapter,
        rest_ecu,
        offset,
        coolant,
        crank is not None,
        crank,
        crank,
        None if crank is None else 0.7,
        started,
        charge_adapter,
        charge_ecu,
        reset,
        (),
    )


@pytest.fixture
def english() -> None:
    i18n.set_language("en")  # ``_german`` (autouse) stellt danach wieder Deutsch ein


# --- Einzelne Messungen (J1979-Bytes) ------------------------------------------------


def _elm(**responses: str) -> Elm327:
    return Elm327(FakeTransport({f"01{pid}": answer for pid, answer in responses.items()}))


@pytest.mark.parametrize(
    ("answer", "voltage"),
    [
        ("41423138", 12.6),  # 0x3138 = 12600 mV
        ("41421388", 5.0),  # untere Grenze, noch geglaubt
        ("41421387", None),  # 4,999 V
        ("41427530", 30.0),  # obere Grenze, noch geglaubt
        ("41427531", None),  # 30,001 V
        ("41420000", None),
        ("4142FFFF", None),  # 65,535 V
        ("414231", None),  # nur ein Datenbyte
        ("7F0142", None),  # unvollständige Ablehnung
        ("7F0112", None),  # PID nicht unterstützt
        ("NO DATA", None),
        ("CAN ERROR", None),  # Adapterfehler: Wert fehlt, kein Abbruch
        ("4142313800", 12.6),  # Füllbyte wird ignoriert
        ("41423138\r41423070", 12.6),  # erstes Steuergerät zählt
    ],
)
def test_ecu_voltage_answers(answer: str, voltage: float | None) -> None:
    assert battery._read_ecu_voltage(_elm(**{"42": answer})) == voltage


@pytest.mark.parametrize(
    ("answer", "rpm"),
    [
        ("410C0000", 0.0),
        ("410C0001", 0.25),
        ("410C0BB8", 750.0),  # 3000 / 4
        ("410C0000\r410C0BB8", 750.0),  # höchste aller Steuergeräte
        ("410C0000\r7F010C12", 0.0),  # Ablehnung eines Steuergeräts zählt nicht
        ("410C00", None),  # zu kurz
        ("7F010C12", None),
        ("NO DATA", None),
        ("STOPPED", None),
    ],
)
def test_rpm_answers(answer: str, rpm: float | None) -> None:
    assert battery._read_rpm(_elm(**{"0C": answer})) == rpm


@pytest.mark.parametrize(
    ("answer", "temp"),
    [("410500", -40), ("410528", 0), ("41053C", 20), ("4105FF", 215), ("NO DATA", None)],
)
def test_coolant_answers(answer: str, temp: float | None) -> None:
    assert battery._read_coolant(_elm(**{"05": answer})) == temp


def test_coolant_adapter_error_is_unknown() -> None:
    assert battery._read_coolant(_elm(**{"05": "BUS BUSY"})) is None


@pytest.mark.parametrize(
    ("reply", "voltage", "reset"),
    [
        ("12.6V", 12.6, False),
        ("12.6v", 12.6, False),
        ("12.6", 12.6, False),
        ("?", None, False),  # nicht unterstützt: kein Neustart
        ("ERR94", None, False),
        ("LV RESET", None, True),
        ("ELM327 v2.1", None, True),  # Kennung statt Spannung: Neustart
        ("elm327 v1.5", None, True),
        ("UNSINN", None, False),
    ],
)
def test_atrv_replies(reply: str, voltage: float | None, reset: bool) -> None:
    elm = Elm327(FakeTransport({"ATRV": reply}))
    assert battery._read_atrv(elm) == (voltage, reset)


# --- Bewertung genau an den Schwellen ------------------------------------------------

# Grenze je Temperaturstufe, ab ``since`` °C; „gut“ ab Grenze + 0,4 V (als Dezimalwert).
# 4 °C (Grenze 9,3 V) steht im Abschnitt am Dateiende.
_BANDS = [
    (since, limit, good)
    for (since, limit), good in zip(
        CRANK_LIMITS, (10.0, 9.9, 9.8, None, 9.5, 9.3, 9.1, 8.9), strict=True
    )
    if good is not None
]


@pytest.mark.parametrize(("since", "limit", "good"), _BANDS)
def test_crank_verdict_at_each_band(since: float, limit: float, good: float) -> None:
    def rating(voltage: float) -> Rating:
        return crank_verdict(_result(coolant=since, crank=voltage)).rating

    assert rating(good) is Rating.GOOD
    assert rating(round(good - 0.001, 3)) is Rating.WEAK
    assert rating(limit) is Rating.WEAK
    assert rating(round(limit - 0.001, 3)) is Rating.BAD


@pytest.mark.parametrize(("since", "limit"), CRANK_LIMITS)
def test_crank_limit_changes_exactly_at_band_start(since: float, limit: float) -> None:
    assert crank_limit(since) == limit
    below = crank_limit(since - 0.001)
    assert below == limit if since == CRANK_LIMITS[-1][0] else below < limit


def test_crank_limit_without_temperature_is_strictest() -> None:
    assert crank_limit(None) == max(limit for _since, limit in CRANK_LIMITS)
    text = crank_verdict(_result(coolant=None, crank=9.6)).text
    assert text == "9,6 V (Grenze 9,6 V): knapp, Batterie altert"


@pytest.mark.parametrize(
    ("rest_ecu", "rest_adapter", "used"),
    [(12.2, 11.0, 12.2), (None, 12.2, 12.2), (11.79, 12.5, 11.79)],
)
def test_rest_verdict_prefers_ecu(rest_ecu: float | None, rest_adapter: float, used: float) -> None:
    verdict = rest_verdict(_result(rest_ecu=rest_ecu, rest_adapter=rest_adapter))
    assert verdict.rating is (Rating.GOOD if used >= 12.2 else Rating.BAD)
    assert verdict.text.startswith(f"{i18n.decimal(used)} V")


def test_charge_voltage_prefers_ecu_over_adapter() -> None:
    result = _result(charge_adapter=12.5, charge_ecu=14.0)
    assert result.charge_voltage == 14.0
    assert charge_verdict(result).rating is Rating.GOOD
    adapter_only = _result(charge_adapter=12.5, charge_ecu=None)
    assert charge_verdict(adapter_only).text == "12,5 V: Lichtmaschine lädt nicht"


def test_charge_not_readable_after_start() -> None:
    verdict = charge_verdict(_result(started=True, charge_adapter=None, charge_ecu=None))
    assert verdict == battery.Verdict(Rating.UNKNOWN, "nicht lesbar")


def test_charge_not_measured_without_start() -> None:
    verdict = charge_verdict(_result(started=False, charge_adapter=None, charge_ecu=None))
    assert verdict.text == "nicht gemessen: Motor ist nicht angesprungen"


@pytest.mark.parametrize(
    ("rest", "crank", "charge", "overall"),
    [
        (12.0, None, None, Rating.WEAK),  # Unbekanntes zählt nicht
        (12.6, 9.0, 14.0, Rating.BAD),
        (12.6, 9.7, 13.0, Rating.WEAK),
        (12.6, 10.5, 15.01, Rating.BAD),
        (None, None, None, Rating.UNKNOWN),
    ],
)
def test_overall_rating_combinations(
    rest: float | None, crank: float | None, charge: float | None, overall: Rating
) -> None:
    result = _result(
        rest_ecu=rest, rest_adapter=rest, crank=crank, started=charge is not None, charge_ecu=charge
    )
    assert overall_rating(result) is overall


# --- Hinweise ------------------------------------------------------------------------


def test_notes_negative_offset() -> None:
    assert notes(_result(offset=-0.2))[0] == (
        "Adapter-Messung um -0,2 V korrigiert (Abweichung zum Steuergerät in Ruhe)."
    )


def test_notes_without_offset_but_with_ecu_voltage() -> None:
    # Adapter und Steuergerät stimmen überein: weder Korrektur noch PID-42-Hinweis
    assert notes(_result(offset=0.0)) == [i18n.tr(battery.BATTERY_NOTE)]


def test_notes_reset_and_no_start() -> None:
    result = _result(crank=9.9, started=False, charge_ecu=None, reset=True)
    texts = notes(result)
    assert "Der Motor ist nicht angesprungen (oder die Drehzahl war nicht lesbar)." in texts
    assert "Der Adapter startete beim Anlassen neu und wurde neu eingerichtet." in texts
    assert texts[-1] == i18n.tr(battery.BATTERY_NOTE)


# --- Ablauf am simulierten Auto ------------------------------------------------------


def test_dip_exactly_crank_drop_is_not_cranking() -> None:
    # Ruhe 12,0 V (Adapter): Schwelle 11,0 V, „darunter“ heißt echt kleiner
    result = _run(_car(_after(1.0, State("11.0V", 0, 12.0))), start_timeout=3.0)
    assert not result.crank_detected
    assert crank_verdict(result).text == "kein Anlassen erkannt"


def test_dip_just_below_crank_drop_is_cranking() -> None:
    def profile(t: float) -> State:
        if t < 1.0:
            return REST
        return State("10.99V", 150, 11.8) if t < 1.5 else RUNNING

    result = _run(_car(profile))
    assert result.crank_detected and result.started
    assert result.crank_minimum == pytest.approx(10.99 + 0.8)


def test_dip_timing_and_duration() -> None:
    car = _car()
    result = _run(car)
    assert car.prompted_at is not None
    threshold = 12.8 - battery.CRANK_DROP  # korrigierte Kurve
    first = next(t for t, v in result.curve if v < threshold)
    last = max(t for t, v in result.curve if v < threshold)
    # ``healthy``: Einbruch 2,0 s bis 2,8 s nach der Aufforderung
    assert first - car.prompted_at == pytest.approx(2.0, abs=0.1)
    assert last - car.prompted_at == pytest.approx(2.8, abs=0.1)
    assert result.crank_seconds == pytest.approx(last - first, abs=0.001)


def test_dip_shorter_than_inrush_uses_lowest_value() -> None:
    def short(t: float) -> State:
        if t < 1.0:
            return REST
        return State("9.0V", 150, None) if t < 1.05 else RUNNING

    result = _run(_car(short))
    assert result.crank_detected
    assert result.crank_seconds is not None and result.crank_seconds < battery.INRUSH_SKIP
    assert result.crank_minimum == result.crank_lowest == pytest.approx(9.8)


def test_samples_during_dip_are_at_most_every_sample_interval() -> None:
    car = _car()
    _run(car)
    assert car.prompted_at is not None
    times = [t for t, c in car.timeline if c == "ATRV" and 2.1 <= t - car.prompted_at < 2.7]
    gaps = [b - a for a, b in pairwise(times)]
    assert gaps and min(gaps) >= battery.SAMPLE_INTERVAL - 1e-9
    assert max(gaps) <= battery.SAMPLE_INTERVAL + 1e-9


def test_rpm_is_read_every_half_second_while_waiting() -> None:
    car = _car(lambda t: REST)
    _run(car, start_timeout=3.0)
    assert car.prompted_at is not None
    rpm = [t for t, c in car.timeline if c == "010C" and t >= car.prompted_at]
    gaps = [b - a for a, b in pairwise(rpm)]
    assert len(rpm) == pytest.approx(3.0 / battery.RPM_INTERVAL, abs=1)
    assert all(g >= battery.RPM_INTERVAL - 1e-9 for g in gaps)


@pytest.mark.parametrize(("rpm", "started"), [(500.0, True), (499.75, False)])
def test_running_rpm_boundary(rpm: float, started: bool) -> None:
    result = _run(_car(_after(1.0, State("12.0V", rpm, 12.8))), start_timeout=3.0)
    assert result.started is started
    assert not result.crank_detected


def test_start_detected_without_dip_has_phases_without_cranking() -> None:
    car = _car(_after(1.0, RUNNING))
    result = _run(car)
    assert result.started
    assert car.phases == [Phase.PREPARE, Phase.REST, Phase.START, Phase.RUNNING]


def test_dip_with_unreadable_rpm_never_counts_as_started() -> None:
    def no_start(t: float) -> State:
        return State("9.3V", 150, 10.0) if 1.0 <= t < 2.0 else RUNNING

    result = _run(_car(no_start, rpm_unreadable=True), start_timeout=4.0)
    assert result.crank_detected and not result.started
    assert result.charge_voltage is None
    assert crank_verdict(result).rating is Rating.GOOD


def test_charge_ignores_settle_time_and_uses_corrected_adapter() -> None:
    def profile(t: float) -> State:
        if t < 1.0:
            return REST
        if t < 4.0:
            return State("15.5V", 800, None)  # Einschwingen: zu hoch, aber nicht bewertet
        return State("13.6V", 800, None)

    car = _car(profile)
    result = _run(car)
    assert result.started
    assert result.charge_ecu is None
    assert result.charge_adapter == pytest.approx(14.4)  # 13,6 V + 0,8 V Diodenverlust
    assert charge_verdict(result).rating is Rating.GOOD


def test_charge_phase_reads_ecu_voltage_every_interval() -> None:
    car = _car(_after(1.0, RUNNING))
    _run(car)
    assert car.prompted_at is not None
    started_at = next(t for t, c in car.timeline if c == "010C" and t >= car.prompted_at + 1.0)
    charge = [t for t, c in car.timeline if c == "0142" and t > started_at]
    # erst nach SETTLE_SECONDS, dann CHARGE_SECONDS lang alle CHARGE_INTERVAL
    assert min(charge) - started_at >= battery.SETTLE_SECONDS - 0.1
    expected = battery.CHARGE_SECONDS / battery.CHARGE_INTERVAL
    assert len(charge) == pytest.approx(expected, abs=1)


def test_charge_unreadable_is_not_readable() -> None:
    result = _run(_car(_after(1.0, State("?", 800, None))))
    assert result.started
    assert result.charge_voltage is None
    assert charge_verdict(result).text == "nicht lesbar"


def test_atrv_unsupported_during_wait_is_not_a_reset() -> None:
    car = _car(_after(1.0, State("?", 0, 12.8)))
    result = _run(car, start_timeout=2.0)
    assert not result.adapter_reset
    assert car.sent.count("ATZ") == 1


def test_adapter_reset_sends_init_and_protocol_again() -> None:
    def brownout(t: float) -> State:
        return State("LV RESET", 0, None) if 2.0 <= t < 2.03 else healthy(t)

    car = _car(brownout)
    _run(car)
    assert car.sent.count("ATZ") == 2
    second = len(car.sent) - car.sent[::-1].index("ATZ") - 1
    assert car.sent[second - 1] == "ATRV"
    assert car.sent[second : second + 9] == [*INIT, *PROTOCOL]


def test_rest_curve_is_corrected_by_offset() -> None:
    car = _car(lambda t: REST)
    result = _run(car, start_timeout=1.0)
    assert result.offset == pytest.approx(0.8)
    assert {v for _t, v in result.curve} == {12.8}
    times = [t for t, _v in result.curve]
    assert times == sorted(times)


@pytest.mark.parametrize(
    ("atrv", "ecu", "offset"),
    [
        ("11.0V", 12.5, 1.5),  # genau MAX_OFFSET: noch Diodenverlust
        ("11.0V", 12.501, 0.0),  # knapp darüber: nicht angewandt
        ("12.6V", 12.4, -0.2),  # Steuergerät tiefer: auch korrigiert
        ("12.4V", 12.4, 0.0),
    ],
)
def test_offset_boundaries(atrv: str, ecu: float, offset: float) -> None:
    result = _run(_car(rest=State(atrv, 0, ecu)), start_timeout=0.5)
    assert result.offset == pytest.approx(offset)
    assert result.rest_voltage == ecu


def test_rest_ecu_from_single_answer() -> None:
    class OnlyFirstEcu(SimCar):
        def write(self, data: bytes) -> None:
            super().write(data)
            if data == b"0142\r":
                self.rest = State(self.rest.atrv, 0, None)  # zweite Messung: NO DATA

    car = OnlyFirstEcu(SimClock(), lambda t: REST)
    result = _run(car, start_timeout=0.5)
    assert result.rest_ecu == 12.8
    assert result.offset == pytest.approx(0.8)


@pytest.mark.parametrize(("voltage", "refused"), [("13.3V", True), ("13.29V", False)])
def test_engine_running_voltage_boundary_without_rpm(voltage: str, refused: bool) -> None:
    car = _car(lambda t: REST, rest=State(voltage, 0, None), rpm_unreadable=True)
    if refused:
        with pytest.raises(BatteryTestRefused, match=r"\(13,3 V\)"):
            _run(car)
        assert car.prompted_at is None
    else:
        assert _run(car, start_timeout=0.5).rest_adapter == 13.29


def test_high_rest_voltage_with_readable_rpm_zero_is_not_refused() -> None:
    # Oberflächenladung nach einer Fahrt: Drehzahl 0 ist der bessere Beleg
    result = _run(_car(lambda t: REST, rest=State("13.8V", 0, 14.0)), start_timeout=0.5)
    assert result.rest_voltage == 14.0
    assert rest_verdict(result).rating is Rating.GOOD


@pytest.mark.parametrize(("answer", "rpm"), [("410C0BB8", "750"), ("410C0000\r410C0C80", "800")])
def test_refused_with_highest_rpm(answer: str, rpm: str) -> None:
    transport = FakeTransport({**CAN_CAR, "010C": answer})
    with pytest.raises(BatteryTestRefused, match=rf"\({rpm} 1/min\)"):
        run_battery_test(Elm327(transport), clock=SimClock(), sleep=lambda s: None)
    assert transport.sent == [*INIT, *PROTOCOL, "010C"]


def test_unreadable_coolant_uses_strictest_limit() -> None:
    class NoCoolant(SimCar):
        def write(self, data: bytes) -> None:
            super().write(data)
            if data == b"0105\r":
                self._pending = b"NO DATA\r\r>"

    result = _run(NoCoolant(SimClock(), coolant=-20))
    assert result.coolant_temp is None
    assert result_to_dict(result)["crank_limit"] == 9.6


def test_cold_engine_lowers_crank_limit() -> None:
    result = _run(_car(coolant=-20))
    assert result.coolant_temp == -20
    assert result_to_dict(result)["crank_limit"] == 8.5


# --- Abbruch und Fehler --------------------------------------------------------------


def test_cancel_before_rest_sends_no_voltage_request() -> None:
    car = _car()
    with pytest.raises(BatteryTestCancelled, match="Batterietest abgebrochen"):
        _run(car, should_stop=lambda: True)
    assert car.sent == PREPARE


def test_cancel_in_rest_phase_stops_before_first_atrv() -> None:
    car = _car()
    with pytest.raises(BatteryTestCancelled):
        _run(car, should_stop=lambda: Phase.REST in car.phases)
    assert car.sent == [*PREPARE, "0142"]


def test_cancel_while_charging() -> None:
    car = _car()
    with pytest.raises(BatteryTestCancelled):
        _run(car, should_stop=lambda: car.phases[-1] is Phase.RUNNING)
    assert all(is_read_only(c) for c in car.sent)


def test_transport_error_at_initialize_passes_through() -> None:
    class Unplugged(SimCar):
        def write(self, data: bytes) -> None:
            raise TransportError("Verbindung unterbrochen")

    car = Unplugged(SimClock())
    with pytest.raises(TransportError):
        _run(car)
    assert car.phases == [Phase.PREPARE]


def test_no_connection_passes_through() -> None:
    car = _car()
    car.static["0100"] = "UNABLE TO CONNECT"
    with pytest.raises(NoConnectionError):
        _run(car)


def test_progress_remaining_never_negative_and_elapsed_grows() -> None:
    car = _car()
    _run(car)
    assert all(p.remaining is None or p.remaining >= 0 for p in car.progress)
    elapsed = [p.elapsed for p in car.progress]
    assert elapsed == sorted(elapsed)
    assert car.progress[0] == BatteryProgress(Phase.PREPARE, 0.0, None, None, None)
    rest = next(p for p in car.progress if p.phase is Phase.REST)
    assert rest.remaining == pytest.approx(battery.REST_SECONDS)


def test_custom_start_timeout_in_progress() -> None:
    car = _car(lambda t: REST)
    _run(car, start_timeout=7.0)
    start = next(p for p in car.progress if p.phase is Phase.START)
    assert start.remaining == pytest.approx(7.0)


def test_result_to_dict_without_crank_is_json() -> None:
    result = _result(coolant=-20.0, crank=None, started=False, charge_ecu=None)
    data = json.loads(json.dumps(result_to_dict(result)))
    assert data["crank_limit"] == 8.5
    assert data["crank_minimum"] is None and data["charge_voltage"] is None
    assert data["ratings"] == {
        "rest": "good",
        "crank": "unknown",
        "charge": "unknown",
        "overall": "good",
    }


# --- Englisch ------------------------------------------------------------------------


@pytest.mark.usefixtures("english")
@pytest.mark.parametrize(
    ("result", "texts"),
    [
        (
            _result(rest_ecu=12.6, crank=10.5, charge_ecu=14.2),
            (
                "12.6 V: well charged",
                "10.5 V (limit 9.6 V): good",
                "14.2 V: alternator is charging",
                "Battery and charging OK",
            ),
        ),
        (
            _result(rest_ecu=12.0, crank=9.7, charge_ecu=13.0),
            (
                "12.0 V: only partly charged, charge the battery",
                "9.7 V (limit 9.6 V): marginal, battery is ageing",
                "13.0 V: low; often intended on vehicles with battery management (start-stop), "
                "otherwise check the alternator",
                "Noticeable: see details",
            ),
        ),
        (
            _result(rest_ecu=11.5, crank=9.0, charge_ecu=15.5),
            (
                "11.5 V: discharged, charge the battery and repeat the test",
                "9.0 V (limit 9.6 V): too low, battery weak or discharged "
                "(starter and cables are also possible causes)",
                "15.5 V: too high, have the voltage regulator checked",
                "Poor: have the battery or charging checked",
            ),
        ),
        (
            _result(rest_ecu=None, rest_adapter=None, crank=None, started=False, charge_ecu=None),
            (
                "not measured",
                "no cranking detected",
                "not measured: the engine did not start",
                "No rating possible",
            ),
        ),
    ],
)
def test_english_verdicts(result: BatteryTestResult, texts: tuple[str, str, str, str]) -> None:
    got = (
        rest_verdict(result).text,
        crank_verdict(result).text,
        charge_verdict(result).text,
        overall_text(result),
    )
    assert got == texts


@pytest.mark.usefixtures("english")
def test_english_refusals_and_notes() -> None:
    with pytest.raises(BatteryTestRefused, match=r"^The engine is running \(750 rpm\)"):
        elm = Elm327(FakeTransport({**CAN_CAR, "010C": "410C0BB8"}))
        run_battery_test(elm, clock=SimClock(), sleep=lambda s: None)
    with pytest.raises(BatteryTestRefused, match="does not report the battery voltage"):
        _run(_car(rest=State("?", 0, 12.8)))
    with pytest.raises(BatteryTestCancelled, match=r"^Battery test cancelled$"):
        _run(_car(), should_stop=lambda: True)
    assert notes(_result(offset=0.75))[0] == (
        "Adapter reading corrected by 0.8 V (difference to the control unit at rest)."
    )
    reset = crank_verdict(_result(crank=None, reset=True)).text
    assert reset.startswith("not measurable: the adapter restarted")


# --- Kommandozeile -------------------------------------------------------------------


def _cli_car(monkeypatch: pytest.MonkeyPatch, car: SimCar, **fixed: Any) -> None:
    """``obd-diag battery --port …`` am simulierten Auto mit simulierter Uhr."""
    monkeypatch.setattr(cli, "SerialTransport", lambda port, baud: car)

    def run(elm: Elm327, *, on_progress: Any, start_timeout: float) -> BatteryTestResult:
        def both(progress: BatteryProgress) -> None:
            car.on_progress(progress)
            on_progress(progress)

        return run_battery_test(
            elm,
            on_progress=both,
            start_timeout=start_timeout,
            clock=car.clock,
            sleep=car.clock.sleep,
            **fixed,
        )

    monkeypatch.setattr(cli, "run_battery_test", run)


def test_cli_text_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _cli_car(monkeypatch, _car())
    assert cli.main(["--lang", "de", "battery", "--port", "/dev/ttyUSB0"]) == 0
    out, err = capsys.readouterr()
    assert err.splitlines() == [
        i18n.tr(battery.BATTERY_INSTRUCTIONS),
        "Verbinde und prüfe, ob der Motor steht …",
        "Messe die Ruhespannung, Motor noch nicht starten …",
        "Jetzt den Motor starten. (Zeit: 30 s)",
        "Anlassen erkannt …",
        "Motor läuft, messe die Ladespannung. Motor laufen lassen …",
    ]
    lines = out.splitlines()
    assert lines[1:5] == [
        "Ruhespannung: 12,8 V: gut geladen",
        "Anlassen:     10,1 V (Grenze 9,5 V): gut",
        "Ladespannung: 14,4 V: Lichtmaschine lädt",
        "Ergebnis:     Batterie und Ladung in Ordnung",
    ]
    assert lines[6].startswith("Hinweis: Adapter-Messung um 0,8 V korrigiert")


def test_cli_english_text_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _cli_car(monkeypatch, _car())
    assert (
        cli.main(["--lang", "en", "battery", "--port", "/dev/ttyUSB0", "--start-timeout", "7"]) == 0
    )
    out, err = capsys.readouterr()
    assert "Start the engine now. (time: 7 s)" in err
    lines = out.splitlines()
    assert lines[1:5] == [
        "Resting voltage:  12.8 V: well charged",
        "Cranking:         10.1 V (limit 9.5 V): good",
        "Charging voltage: 14.4 V: alternator is charging",
        "Result:           Battery and charging OK",
    ]
    assert "Note: Adapter reading corrected by 0.8 V" in out


def test_cli_json_with_weak_results(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def weak(t: float) -> State:
        if t < 1.0:
            return REST
        return State("8.5V", 150, 9.3) if t < 2.5 else State("12.4V", 800, 12.6)

    _cli_car(monkeypatch, _car(weak))
    assert cli.main(["--lang", "de", "battery", "--port", "/dev/ttyUSB0", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ratings"] == {"rest": "good", "crank": "bad", "charge": "bad", "overall": "bad"}
    assert data["crank_minimum"] == pytest.approx(9.3)
    assert data["charge_voltage"] == 12.6
    assert data["offset"] == pytest.approx(0.8)
    assert data["started"] is True


def test_cli_refused_high_voltage(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _cli_car(monkeypatch, _car(rest=State("13.9V", 0, 14.6), rpm_unreadable=True))
    assert cli.main(["--lang", "de", "battery", "--port", "/dev/ttyUSB0", "--json"]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert "Fehler: Die Bordspannung (14,6 V) ist so hoch wie bei laufendem Motor." in err


def test_cli_cancelled(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    car = _car()
    _cli_car(monkeypatch, car, should_stop=lambda: Phase.START in car.phases)
    assert cli.main(["--lang", "en", "battery", "--port", "/dev/ttyUSB0"]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err.endswith("\n\nError: Battery test cancelled\n")


def test_cli_keyboard_interrupt(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class CtrlC(SimCar):
        def write(self, data: bytes) -> None:
            if data == b"ATRV\r" and self.prompted_at is not None:
                raise KeyboardInterrupt
            super().write(data)

    _cli_car(monkeypatch, CtrlC(SimClock()))
    assert cli.main(["--lang", "de", "battery", "--port", "/dev/ttyUSB0"]) == 1
    err = capsys.readouterr().err
    assert err.endswith("\n\nFehler: Batterietest abgebrochen\n")


def test_cli_transport_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class Unplugged(SimCar):
        def write(self, data: bytes) -> None:
            if data == b"ATRV\r":
                raise TransportError("Verbindung unterbrochen")
            super().write(data)

    _cli_car(monkeypatch, Unplugged(SimClock()))
    assert cli.main(["--lang", "de", "battery", "--port", "/dev/ttyUSB0"]) == 1
    assert "Fehler: Verbindung unterbrochen" in capsys.readouterr().err


def test_cli_no_connection_hint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    car = _car()
    car.static["0100"] = "UNABLE TO CONNECT"
    _cli_car(monkeypatch, car)
    assert cli.main(["--lang", "de", "battery", "--port", "/dev/ttyUSB0"]) == 1
    err = capsys.readouterr().err
    assert "Fehler: 0100: UNABLE TO CONNECT" in err
    assert "Hinweis: " in err


@pytest.mark.parametrize("value", ["0", "-5", "abc", "nan"])
def test_cli_start_timeout_must_be_positive(value: str, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--lang", "de", "battery", "--port", "/dev/ttyUSB0", "--start-timeout", value])
    assert exit_info.value.code == 2
    assert "--start-timeout" in capsys.readouterr().err


def test_cli_sends_only_read_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    car = _car()
    _cli_car(monkeypatch, car)
    assert cli.main(["--lang", "de", "battery", "--port", "/dev/ttyUSB0", "--json"]) == 0
    assert set(car.sent) == {*INIT, *PROTOCOL, "010C", "0105", "0142", "ATRV"}
    assert all(is_read_only(c) for c in car.sent)


# --- Grenze + 0,4 V in Gleitkomma -------------------------------------------------
# Früher ergab 9,3 + 0,4 = 9,700000000000001: genau 9,7 V bei 4 bis 9 °C galt als „knapp“.


def test_crank_good_exactly_at_margin_for_4_degrees() -> None:
    assert crank_verdict(_result(coolant=4.0, crank=9.7)).rating is Rating.GOOD
