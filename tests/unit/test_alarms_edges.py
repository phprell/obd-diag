"""Warnschwellen (``services.alarms``): Randfälle der Syntax ``SCHLÜSSEL=UNTEN:OBEN``,
Werte genau auf der Grenze, nicht lesbare Werte, Texte und CSV in beiden Sprachen,
``run_live`` und ``obd-diag live``.

Für die Schwellen wird nichts gesendet; ``test_run_live_sends_the_same_with_thresholds``
prüft das an der Befehlsfolge.
"""

import csv
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from obd_diag import cli
from obd_diag.cli import main
from obd_diag.i18n import set_language
from obd_diag.protocol.elm327 import Elm327
from obd_diag.protocol.pids import pid_by_key
from obd_diag.services.alarms import (
    DEFAULT_THRESHOLDS,
    HIGH_VOLTAGE,
    VOLTAGE_KEY,
    Alarm,
    Threshold,
    check,
    merge,
    parse_number,
    parse_threshold,
    thresholds_from_json,
    thresholds_to_json,
)
from obd_diag.services.diagnostics import LOW_VOLTAGE
from obd_diag.services.live import LiveRecorder, LiveSample, alarm_text, run_live
from tests.fakes import CAN_CAR
from tests.live_fakes import COOLANT, LIVE_VALUES, RPM, use_fake_pids
from tests.unit.test_cli_live import FakeTime
from tests.unit.test_command_guard import WireCheckingTransport

# --- Syntax SCHLÜSSEL=UNTEN:OBEN ---


@pytest.mark.parametrize(
    ("text", "key", "threshold"),
    [
        ("coolant_temp=-40:", "coolant_temp", Threshold(low=-40)),
        ("ambient_temp=-20:-5", "ambient_temp", Threshold(-20, -5)),
        ("ambient_temp=-20,5:-0,5", "ambient_temp", Threshold(-20.5, -0.5)),
        ("voltage=11,8:15,5", "voltage", Threshold(11.8, 15.5)),
        ("voltage=11.8:15.5", "voltage", Threshold(11.8, 15.5)),
        ("VOLTAGE=:15", "voltage", Threshold(high=15)),
        ("rpm=  : 4000 ", "rpm", Threshold(high=4000)),
        ("rpm=0:", "rpm", Threshold(low=0)),  # 0 ist eine Grenze, nicht „keine“
        ("rpm=:0", "rpm", Threshold(high=0)),
        ("coolant_temp=0:0,5", "coolant_temp", Threshold(0, 0.5)),
        ("speed=:", "speed", Threshold()),
        ("speed= : ", "speed", Threshold()),
        ("rpm=+500:", "rpm", Threshold(low=500)),
    ],
)
def test_parse_threshold_accepts(text: str, key: str, threshold: Threshold) -> None:
    assert parse_threshold(text) == (key, threshold)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("rpm=", "Form SCHLÜSSEL=UNTEN:OBEN erwartet"),  # ohne Doppelpunkt
        ("rpm:1:2", "Form SCHLÜSSEL=UNTEN:OBEN erwartet"),  # ohne Gleichheitszeichen
        (" =1:2", "Form SCHLÜSSEL=UNTEN:OBEN erwartet"),  # Schlüssel nur Leerzeichen
        ("", "Form SCHLÜSSEL=UNTEN:OBEN erwartet"),
        ("rpm=1:2:3", "keine Zahl: '1:2:3'"),
        ("rpm=1,5,3:", "keine Zahl"),
        ("rpm=1;2", "Form SCHLÜSSEL=UNTEN:OBEN erwartet"),
        ("rpm=nan:", "keine Zahl: 'nan:'"),
        ("rpm=:inf", "keine Zahl"),
        ("rpm=-inf:", "keine Zahl"),
        ("rpm=0x10:", "keine Zahl"),
        ("rpm=- 5:", "keine Zahl"),
        ("rpm=5:5", "untere Schwelle 5,0 muss kleiner sein als obere 5,0"),
        ("rpm=-1:-2", "untere Schwelle -1,0 muss kleiner sein als obere -2,0"),
    ],
)
def test_parse_threshold_rejects(text: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_threshold(text)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("rpm", "expected KEY=LOW:HIGH, e.g. coolant_temp=:110: 'rpm'"),
        ("rpm=a:", "not a number: 'a:'"),
        ("rpm=5,5:1", "lower threshold 5.5 must be less than upper 1.0"),
    ],
)
def test_parse_threshold_errors_in_english(text: str, message: str) -> None:
    set_language("en")
    with pytest.raises(ValueError) as error:
        parse_threshold(text)
    assert str(error.value) == message


@pytest.mark.parametrize(
    ("text", "value"),
    [("", None), ("   ", None), ("12", 12.0), (" 12,25 ", 12.25), ("-0,5", -0.5), ("1e2", 100.0)],
)
def test_parse_number(text: str, value: float | None) -> None:
    assert parse_number(text) == value


def test_threshold_rejects_infinite_and_nan_limits() -> None:
    for low, high in ((-math.inf, 1.0), (None, math.inf), (math.nan, None)):
        with pytest.raises(ValueError, match="Schwelle muss eine Zahl sein"):
            Threshold(low, high)
    set_language("en")
    with pytest.raises(ValueError, match="Threshold must be a number"):
        Threshold(high=math.nan)


# --- Grenzen ---


@pytest.mark.parametrize(
    ("threshold", "value", "side"),
    [
        (Threshold(10, 20), 10.0, None),  # genau auf der Grenze: in Ordnung
        (Threshold(10, 20), 20.0, None),
        (Threshold(10, 20), math.nextafter(10.0, -math.inf), "low"),
        (Threshold(10, 20), math.nextafter(20.0, math.inf), "high"),
        (Threshold(low=0), 0.0, None),
        (Threshold(low=0), -0.0, None),
        (Threshold(low=0), -1e-9, "low"),
        (Threshold(high=0), 1e-9, "high"),
        (Threshold(-30, -10), -20.0, None),
        (Threshold(-30, -10), -30.5, "low"),
        (Threshold(-30, -10), -9.5, "high"),
        (Threshold(low=5), 1e300, None),  # nur untere Grenze: nach oben offen
        (Threshold(high=5), -1e300, None),
        (Threshold(high=5), math.inf, "high"),
        (Threshold(low=5), -math.inf, "low"),
        (Threshold(10, 20), math.nan, None),
        (Threshold(10, 20), None, None),
        (Threshold(), 1e9, None),
    ],
)
def test_threshold_side(threshold: Threshold, value: float | None, side: str | None) -> None:
    assert threshold.side(value) == side


@pytest.mark.parametrize(
    ("key", "value", "side"),
    [
        ("coolant_temp", 115.0, None),
        ("coolant_temp", 116.0, "high"),  # 0105 = 9C
        ("coolant_temp", -40.0, None),  # keine untere Grenze
        ("oil_temp", 130.0, None),
        ("oil_temp", 131.0, "high"),
        ("control_voltage", LOW_VOLTAGE, None),
        ("control_voltage", 11.799, "low"),
        ("control_voltage", HIGH_VOLTAGE, None),
        ("control_voltage", 15.501, "high"),
        (VOLTAGE_KEY, 11.8, None),
        (VOLTAGE_KEY, 11.7, "low"),
        (VOLTAGE_KEY, 15.6, "high"),
    ],
)
def test_default_boundaries(key: str, value: float, side: str | None) -> None:
    assert LOW_VOLTAGE == 11.8 and HIGH_VOLTAGE == 15.5
    assert DEFAULT_THRESHOLDS[key].side(value) == side


def test_check_takes_voltage_only_from_atrv() -> None:
    thresholds = {VOLTAGE_KEY: Threshold(low=11.8)}
    # ein Wert namens "voltage" in values zählt nicht, nur die Bordspannung (ATRV)
    assert check({VOLTAGE_KEY: 5.0}, 12.0, thresholds) == ()
    assert check({}, None, thresholds) == ()
    assert check({}, 11.0, thresholds) == (Alarm(VOLTAGE_KEY, 11.0, "low", 11.8),)


def test_check_ignores_missing_unreadable_and_nan_values() -> None:
    thresholds = {"rpm": Threshold(high=1000), "speed": Threshold(high=50), "maf": Threshold(1, 2)}
    assert check({"rpm": None, "speed": math.nan}, None, thresholds) == ()


def test_check_order_follows_thresholds_not_values() -> None:
    thresholds = {
        "speed": Threshold(high=50),
        VOLTAGE_KEY: Threshold(high=15),
        "rpm": Threshold(low=900),
    }
    alarms = check({"rpm": 700.0, "speed": 120.0}, 15.2, thresholds)
    assert [(a.key, a.side, a.limit) for a in alarms] == [
        ("speed", "high", 50),
        (VOLTAGE_KEY, "high", 15),
        ("rpm", "low", 900),
    ]


def test_check_without_thresholds() -> None:
    assert check({"coolant_temp": 500.0}, 3.0, {}) == ()


def test_merge_last_change_wins_and_can_reenable() -> None:
    changes = [
        ("rpm", Threshold(high=4000)),
        ("rpm", Threshold(high=3000)),
        (VOLTAGE_KEY, Threshold()),
        (VOLTAGE_KEY, Threshold(low=12)),
    ]
    merged = merge({}, changes)
    assert merged == {"rpm": Threshold(high=3000), VOLTAGE_KEY: Threshold(low=12)}
    assert merge(DEFAULT_THRESHOLDS, []) == DEFAULT_THRESHOLDS
    assert merge(DEFAULT_THRESHOLDS, []) is not DEFAULT_THRESHOLDS  # Vorgaben bleiben


def test_json_keeps_negative_and_integer_limits() -> None:
    thresholds = {"ambient_temp": Threshold(-20, -5), "rpm": Threshold(low=0)}
    data = thresholds_to_json(thresholds)
    assert data == {"ambient_temp": [-20, -5], "rpm": [0, None]}
    assert thresholds_from_json(data) == thresholds
    assert thresholds_from_json({"x": [None, math.inf]}) == {}


# --- Texte ---


@pytest.mark.parametrize(
    ("alarm", "german", "english"),
    [
        (
            Alarm(VOLTAGE_KEY, 11.25, "low", 11.8),
            "Bordspannung 11,2 V unter 11,8 V",
            "Battery voltage 11.2 V below 11.8 V",
        ),
        (
            Alarm(VOLTAGE_KEY, 16.0, "high", 15.5),
            "Bordspannung 16 V über 15,5 V",
            "Battery voltage 16 V above 15.5 V",
        ),
        (
            Alarm("coolant_temp", 117.0, "high", 115.0),
            "Kühlmitteltemperatur 117 °C über 115 °C",
            "Coolant temperature 117 °C above 115 °C",
        ),
        (
            Alarm("ambient_temp", -25.0, "low", -20.0),
            "Umgebungstemperatur -25 °C unter -20 °C",
            "Ambient air temperature -25 °C below -20 °C",
        ),
    ],
)
def test_alarm_text_with_real_pids(alarm: Alarm, german: str, english: str) -> None:
    pids = [pid_by_key("coolant_temp"), pid_by_key("ambient_temp")]
    assert alarm_text(alarm, pids) == german
    set_language("en")
    assert alarm_text(alarm, pids) == english


def test_alarm_text_without_unit_has_no_double_spaces() -> None:
    assert Alarm("x", 2.5, "low", 3.0).text("x", "") == "x 2,5 unter 3"
    set_language("en")
    assert Alarm("x", 2.5, "low", 3.0).text("x", "") == "x 2.5 below 3"


# --- CSV ---


def _rows(path: Path, delimiter: str) -> list[list[str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.reader(f, delimiter=delimiter))


def test_recorder_english_column_and_row(tmp_path: Path) -> None:
    set_language("en")
    path = tmp_path / "live.csv"
    alarms = (Alarm("coolant_temp", 120.5, "high", 115.0), Alarm(VOLTAGE_KEY, 11.0, "low", 11.8))
    with LiveRecorder(path, [COOLANT, RPM]) as recorder:
        recorder.add(LiveSample(1.5, {"coolant_temp": 120.5, "rpm": None}, 11.0, True, alarms))
        recorder.add(LiveSample(2.5, {"coolant_temp": 90.0, "rpm": 800.0}, None, False, ()))
    header, first, second = _rows(path, ",")
    assert header[-2:] == ["Battery voltage (V)", "Warnings"]
    # Name aus der PidSpec, übersetzt
    assert first == [
        "1.5",
        "120.5",
        "",
        "11",
        "Coolant temperature 120.5 °C above 115 °C | Battery voltage 11 V below 11.8 V",
    ]
    assert second == ["2.5", "90", "800", "", ""]  # ohne Warnung: leere Zelle


def test_recorder_german_column_and_empty_cell(tmp_path: Path) -> None:
    path = tmp_path / "live.csv"
    with LiveRecorder(path, [COOLANT]) as recorder:
        recorder.add(LiveSample(0.0, {"coolant_temp": None}, None, False, ()))
    header, row = _rows(path, ";")
    assert header == ["Zeit (s)", "Kühlmitteltemperatur (°C)", "Bordspannung (V)", "Warnungen"]
    assert row == ["0", "", "", ""]


# --- run_live ---


@pytest.fixture
def _fake_pids(monkeypatch: pytest.MonkeyPatch) -> None:
    use_fake_pids(monkeypatch)


def _run(
    responses: dict[str, str], thresholds: dict[str, Threshold] | None, rounds: int = 1
) -> tuple[list[LiveSample], list[str]]:
    transport = WireCheckingTransport(responses)
    elm = Elm327(transport)
    elm.initialize()
    elm.protocol()
    samples: list[LiveSample] = []
    now = [0.0]  # simulierte Uhr: Warten rückt sie vor

    def sleep(seconds: float) -> None:
        now[0] += seconds

    run_live(
        elm,
        [COOLANT, RPM],
        on_sample=samples.append,
        should_stop=lambda: False,
        max_samples=rounds,
        thresholds=thresholds,
        clock=lambda: now[0],
        sleep=sleep,
    )
    return samples, list(transport.sent)


@pytest.mark.usefixtures("_fake_pids")
@pytest.mark.parametrize("thresholds", [None, {}])
def test_run_live_without_thresholds_has_no_alarms(thresholds: dict[str, Threshold] | None) -> None:
    samples, _ = _run({**CAN_CAR, **LIVE_VALUES, "0105": "4105FF", "ATRV": "16.0V"}, thresholds)
    assert samples[0].alarms == ()


@pytest.mark.usefixtures("_fake_pids")
def test_run_live_sends_the_same_with_thresholds() -> None:
    responses = {**CAN_CAR, **LIVE_VALUES, "0105": "4105FF"}
    _, without = _run(responses, None, rounds=3)
    samples, with_alarms = _run(responses, dict(DEFAULT_THRESHOLDS), rounds=3)
    assert with_alarms == without
    assert [s.alarms for s in samples] == [(Alarm("coolant_temp", 215.0, "high", 115.0),)] * 3


@pytest.mark.usefixtures("_fake_pids")
def test_run_live_voltage_alarm_between_atrv_reads() -> None:
    # ATRV nur in Runde 0 (sonst jede zehnte); die Warnung gilt mit dem zuletzt
    # gelesenen Wert auch in den Runden dazwischen
    samples, sent = _run(
        {**CAN_CAR, **LIVE_VALUES, "ATRV": "15.9V"}, dict(DEFAULT_THRESHOLDS), rounds=3
    )
    assert sent.count("ATRV") == 1  # nur Runde 0
    assert [s.alarms for s in samples] == [(Alarm(VOLTAGE_KEY, 15.9, "high", 15.5),)] * 3


@pytest.mark.usefixtures("_fake_pids")
def test_run_live_unreadable_value_gives_no_alarm() -> None:
    responses = {**CAN_CAR, **LIVE_VALUES, "0105": "NO DATA", "010C": "7F 01 12", "ATRV": "?"}
    samples, _ = _run(
        responses,
        {"coolant_temp": Threshold(1, 2), "rpm": Threshold(1, 2), VOLTAGE_KEY: Threshold(1, 2)},
    )
    assert samples[0].values == {"coolant_temp": None, "rpm": None}
    assert samples[0].voltage is None
    assert samples[0].alarms == ()


@pytest.mark.usefixtures("_fake_pids")
def test_run_live_exact_limit_is_no_alarm() -> None:
    # 0105 = 9B: 155 - 40 = 115 °C, 010C = 0BB8: 3000/4 = 750 1/min
    responses = {**CAN_CAR, **LIVE_VALUES, "0105": "41059B", "010C": "410C0BB8"}
    samples, _ = _run(responses, {"coolant_temp": Threshold(high=115), "rpm": Threshold(low=750)})
    assert samples[0].alarms == ()
    samples, _ = _run(
        responses, {"coolant_temp": Threshold(high=114.9), "rpm": Threshold(low=750.5)}
    )
    assert samples[0].alarms == (
        Alarm("coolant_temp", 115.0, "high", 114.9),
        Alarm("rpm", 750.0, "low", 750.5),
    )


# --- obd-diag live ---


@pytest.fixture
def car(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> SimpleNamespace:
    use_fake_pids(monkeypatch)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    state = SimpleNamespace(responses={**CAN_CAR, **LIVE_VALUES}, time=FakeTime(), sent=[])

    def open_port(port: str, baud: int) -> WireCheckingTransport:
        transport = WireCheckingTransport(state.responses)
        state.sent = transport.sent
        return transport

    monkeypatch.setattr(cli, "SerialTransport", open_port)
    monkeypatch.setattr(cli, "time", state.time)
    return state


def _cells(out: str) -> list[list[str]]:
    return [row.split() for row in out.splitlines()[1:]]


def test_cli_default_voltage_alarm(
    car: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    car.responses["ATRV"] = "15.9V"
    assert main(["live", "--pids", "rpm", "--duration", "2"]) == 0
    out, err = capsys.readouterr()
    assert [row[-1] for row in _cells(out)] == ["15.9!", "15.9!"]
    assert err.count("Warnung: Bordspannung 15,9 V über 15,5 V") == 1


def test_cli_alarm_can_switch_off_a_default(
    car: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    car.responses["ATRV"] = "15.9V"
    assert main(["live", "--pids", "rpm", "--duration", "1", "--alarm", "voltage=:"]) == 0
    out, err = capsys.readouterr()
    assert "!" not in out
    assert "Warnung" not in err


def test_cli_repeated_alarm_last_wins(
    car: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    # 1726 1/min: erst über 1000 (Warnung), dann bis 2000 erlaubt (keine)
    args = [
        "live",
        "--pids",
        "rpm",
        "--duration",
        "1",
        "--alarm",
        "rpm=:1000",
        "--alarm",
        "RPM=:2000",
    ]
    assert main(args) == 0
    out, err = capsys.readouterr()
    assert _cells(out)[0][1] == "1726"
    assert "Warnung" not in err


def test_cli_negative_lower_limit(car: SimpleNamespace, capsys: pytest.CaptureFixture[str]) -> None:
    # Negative untere Grenze auf der Kommandozeile (argparse hält sie nicht für eine Option)
    args = ["live", "--pids", "coolant_temp", "--duration", "1", "--alarm", "coolant_temp=-40:80"]
    assert main(args) == 0
    out, err = capsys.readouterr()
    assert _cells(out)[0][1] == "86!"
    assert "Warnung: Kühlmitteltemperatur 86 °C über 80 °C" in err


def test_cli_back_in_range_for_voltage(
    car: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    # ATRV wird nur jede zehnte Runde gelesen: Runde 0 16 V, Runde 10 wieder 14 V
    readings = iter(["16.0V", "14.0V"])  # Runde 0, Runde 10
    original = car.responses

    class Responses(dict[str, str]):
        def get(self, key: str, default: object = None) -> object:  # type: ignore[override]
            return next(readings) if key == "ATRV" else original.get(key, default)

        def __getitem__(self, key: str) -> str:
            return next(readings) if key == "ATRV" else original[key]

    car.responses = Responses(original)
    assert main(["live", "--pids", "rpm", "--duration", "11", "--interval", "1"]) == 0
    out, err = capsys.readouterr()
    voltages = [row[-1] for row in _cells(out)]
    assert voltages[0] == "16.0!" and voltages[9] == "16.0!" and voltages[10] == "14.0"
    assert car.sent.count("ATRV") == 2
    assert err.count("Warnung: Bordspannung 16 V über 15,5 V") == 1
    assert "Hinweis: Bordspannung wieder im Bereich." in err


def test_cli_english_warning(car: SimpleNamespace, capsys: pytest.CaptureFixture[str]) -> None:
    car.responses["0105"] = "41059D"  # 117 °C
    assert main(["live", "--lang", "en", "--pids", "coolant_temp", "--duration", "1"]) == 0
    err = capsys.readouterr().err
    assert "Warning: Coolant temperature 117 °C above 115 °C" in err


@pytest.mark.parametrize(
    ("alarm", "message"),
    [
        ("rpm=3:1", "lower threshold 3.0 must be less than upper 1.0"),
        ("rpm", "expected KEY=LOW:HIGH"),
        (
            "nonsense=1:2",
            "unknown value: nonsense (--list shows the keys, supply voltage: voltage)",
        ),
        ("rpm=x:", "not a number: 'x:'"),
    ],
)
def test_cli_alarm_errors_in_english(
    car: SimpleNamespace, capsys: pytest.CaptureFixture[str], alarm: str, message: str
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["live", "--lang", "en", "--alarm", alarm])
    assert exit_info.value.code == 2
    assert message in capsys.readouterr().err
    assert car.sent == []  # Fehler vor dem Öffnen des Ports


def test_cli_alarm_for_known_but_unselected_value_stays_silent(
    car: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    # Schwelle für einen Wert, der nicht abgefragt wird: keine Warnung, nichts gesendet
    args = ["live", "--pids", "rpm", "--duration", "1", "--alarm", "speed=:1", "--no-alarms"]
    assert main(args) == 0
    out, err = capsys.readouterr()
    assert "!" not in out and "Warnung" not in err
    assert "010D" not in car.sent


# --- Wert und Schwelle lesbar verschieden -----------------------------------------


@pytest.mark.parametrize(
    "alarm",
    [
        # PID 42 löst 1 mV auf: 11,76 V liegt unter 11,8 V, gerundet aber nicht
        Alarm("control_voltage", 11.76, "low", 11.8),
        Alarm(VOLTAGE_KEY, 15.54, "high", 15.5),
    ],
)
def test_alarm_text_value_never_reads_like_the_limit(alarm: Alarm) -> None:
    # Früher auf eine Nachkommastelle gerundet: „11,8 V unter 11,8 V“.
    text = alarm.text("Wert", "V")
    value_text, limit_text = text.removeprefix("Wert ").split(" V ")[0], text.rsplit(" ", 2)[1]
    assert value_text != limit_text, text


def test_alarm_text_shows_needed_digits_only() -> None:
    assert Alarm("control_voltage", 11.76, "low", 11.8).text("Wert", "V") == (
        "Wert 11,76 V unter 11,8 V"
    )
    assert Alarm("control_voltage", 11.7996, "low", 11.8).text("Wert", "V") == (
        "Wert 11,800 V unter 11,8 V"
    )
    assert Alarm("control_voltage", 11.5, "low", 11.8).text("Wert", "V") == (
        "Wert 11,5 V unter 11,8 V"
    )
