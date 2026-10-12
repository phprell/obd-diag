"""Warnschwellen für Live-Daten: ``services.alarms``, ``run_live``, CSV und ``obd-diag live``.

Gesendet wird für die Schwellen nichts; die Befehlsfolgen prüfen ``test_live`` und
``test_live_safety`` unverändert (Vorgaben der Kommandozeile inklusive).
"""

import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from obd_diag import cli
from obd_diag.cli import main
from obd_diag.i18n import set_language
from obd_diag.protocol.elm327 import Elm327
from obd_diag.services.alarms import (
    DEFAULT_THRESHOLDS,
    VOLTAGE_KEY,
    Alarm,
    Threshold,
    check,
    merge,
    parse_threshold,
    thresholds_from_json,
    thresholds_to_json,
)
from obd_diag.services.live import LiveRecorder, LiveSample, alarm_text, run_live
from tests.fakes import CAN_CAR
from tests.live_fakes import COOLANT, LIVE_VALUES, RPM, use_fake_pids
from tests.unit.test_cli_live import FakeTime
from tests.unit.test_command_guard import WireCheckingTransport

# --- Schwellen ---


def test_threshold_sides() -> None:
    t = Threshold(low=11.8, high=15.5)
    assert t.side(12.0) is None
    assert t.side(11.8) is None  # genau auf der Grenze: noch in Ordnung
    assert t.side(11.7) == "low"
    assert t.side(15.6) == "high"
    assert t.side(None) is None
    assert t.side(math.nan) is None
    assert Threshold(high=100).side(-1000) is None
    assert Threshold().empty and not t.empty


@pytest.mark.parametrize(("low", "high"), [(10, 10), (12, 11), (math.inf, None), (None, math.nan)])
def test_threshold_rejects_nonsense(low: float | None, high: float | None) -> None:
    with pytest.raises(ValueError):
        Threshold(low, high)


def test_defaults() -> None:
    assert DEFAULT_THRESHOLDS["coolant_temp"] == Threshold(high=115)
    assert DEFAULT_THRESHOLDS["oil_temp"] == Threshold(high=130)
    assert DEFAULT_THRESHOLDS["control_voltage"] == Threshold(11.8, 15.5)
    assert DEFAULT_THRESHOLDS[VOLTAGE_KEY] == Threshold(11.8, 15.5)


def test_check_order_and_voltage() -> None:
    alarms = check(
        {"coolant_temp": 117.0, "rpm": 800.0, "control_voltage": None},
        11.2,
        DEFAULT_THRESHOLDS,
    )
    assert alarms == (
        Alarm("coolant_temp", 117.0, "high", 115.0),
        Alarm(VOLTAGE_KEY, 11.2, "low", 11.8),
    )
    assert check({"coolant_temp": 90.0}, 14.0, DEFAULT_THRESHOLDS) == ()


def test_alarm_text_in_both_languages() -> None:
    alarm = Alarm("coolant_temp", 117.0, "high", 115.0)
    assert alarm.text("Kühlmitteltemperatur", "°C") == "Kühlmitteltemperatur 117 °C über 115 °C"
    low = Alarm(VOLTAGE_KEY, 11.25, "low", 11.8)
    assert low.text("Bordspannung", "V") == "Bordspannung 11,2 V unter 11,8 V"
    assert Alarm("x", 5.0, "high", 3.0).text("x", "") == "x 5 über 3"
    set_language("en")
    assert alarm.text("Coolant temperature", "°C") == "Coolant temperature 117 °C above 115 °C"


@pytest.mark.parametrize(
    ("text", "key", "threshold"),
    [
        ("coolant_temp=:110", "coolant_temp", Threshold(high=110)),
        ("Voltage=12,5:15", "voltage", Threshold(12.5, 15)),
        ("oil_temp=:", "oil_temp", Threshold()),
        (" rpm = 500 : 4000 ", "rpm", Threshold(500, 4000)),
    ],
)
def test_parse_threshold(text: str, key: str, threshold: Threshold) -> None:
    assert parse_threshold(text) == (key, threshold)


@pytest.mark.parametrize("text", ["coolant_temp", "coolant_temp=110", "=1:2", "rpm=a:", "x=5:1"])
def test_parse_threshold_errors(text: str) -> None:
    with pytest.raises(ValueError):
        parse_threshold(text)


def test_merge_replaces_and_drops_empty() -> None:
    merged = merge(
        DEFAULT_THRESHOLDS,
        [
            ("coolant_temp", Threshold(high=105)),
            ("oil_temp", Threshold()),
            ("rpm", Threshold(None, 4500)),
        ],
    )
    assert merged["coolant_temp"] == Threshold(high=105)
    assert "oil_temp" not in merged
    assert merged["rpm"] == Threshold(high=4500)
    assert merged[VOLTAGE_KEY] == DEFAULT_THRESHOLDS[VOLTAGE_KEY]


def test_json_round_trip_skips_invalid() -> None:
    data = thresholds_to_json(DEFAULT_THRESHOLDS)
    assert data["coolant_temp"] == [None, 115.0]
    assert thresholds_from_json(data) == DEFAULT_THRESHOLDS
    broken = {"a": [5, 1], "b": "x", "c": [None, None], "d": [1], "e": ["1", 2], "f": [1, 2]}
    assert thresholds_from_json(broken) == {"f": Threshold(1, 2)}
    assert thresholds_from_json([1, 2]) == {}


# --- run_live und Aufzeichnung ---


@pytest.fixture
def _fake_pids(monkeypatch: pytest.MonkeyPatch) -> None:
    use_fake_pids(monkeypatch)


@pytest.mark.usefixtures("_fake_pids")
def test_run_live_marks_alarms_per_round(tmp_path: Path) -> None:
    responses = {**CAN_CAR, **LIVE_VALUES, "0105": "41059B"}  # 115 °C: noch in Ordnung
    elm = Elm327(WireCheckingTransport(responses))
    elm.initialize()
    elm.protocol()
    samples: list[LiveSample] = []
    thresholds = {"coolant_temp": Threshold(high=110), "rpm": Threshold(low=900)}
    path = tmp_path / "live.csv"
    with LiveRecorder(path, [COOLANT, RPM]) as recorder:
        run_live(
            elm,
            [COOLANT, RPM],
            on_sample=samples.append,
            should_stop=lambda: False,
            max_samples=1,
            recorder=recorder,
            thresholds=thresholds,
            clock=lambda: 0.0,
            sleep=lambda s: None,
        )
    assert samples[0].alarms == (Alarm("coolant_temp", 115.0, "high", 110.0),)
    header, row = path.read_text(encoding="utf-8-sig").splitlines()
    assert header.endswith(";Bordspannung (V);Warnungen")
    assert row.endswith(";12,4;Kühlmitteltemperatur 115 °C über 110 °C")


def test_recorder_joins_several_alarms(tmp_path: Path) -> None:
    path = tmp_path / "live.csv"
    alarms = (Alarm("coolant_temp", 120.0, "high", 115.0), Alarm(VOLTAGE_KEY, 11.0, "low", 11.8))
    with LiveRecorder(path, [COOLANT]) as recorder:
        recorder.add(LiveSample(0.0, {"coolant_temp": 120.0}, 11.0, True, alarms))
    assert path.read_text(encoding="utf-8-sig").splitlines()[1] == (
        "0;120;11;Kühlmitteltemperatur 120 °C über 115 °C | Bordspannung 11 V unter 11,8 V"
    )


def test_alarm_text_for_unknown_key() -> None:
    assert alarm_text(Alarm("maf", 5.0, "high", 3.0), [COOLANT]) == "maf 5 über 3"


# --- obd-diag live ---


@pytest.fixture
def car(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> SimpleNamespace:
    use_fake_pids(monkeypatch)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    state = SimpleNamespace(responses={**CAN_CAR, **LIVE_VALUES}, time=FakeTime())

    def open_port(port: str, baud: int) -> WireCheckingTransport:
        return WireCheckingTransport(state.responses)

    monkeypatch.setattr(cli, "SerialTransport", open_port)
    monkeypatch.setattr(cli, "time", state.time)
    return state


def test_cli_default_thresholds_warn_once(
    car: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    car.responses["0105"] = "41059D"  # 117 °C
    assert main(["live", "--pids", "coolant_temp,rpm", "--duration", "3"]) == 0
    out, err = capsys.readouterr()
    rows = out.splitlines()[1:]
    assert [row.split()[1] for row in rows] == ["117!", "117!", "117!"]
    assert err.count("Warnung: Kühlmitteltemperatur 117 °C über 115 °C") == 1


def test_cli_alarm_option_and_back_in_range(
    car: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    values = iter(["410C1AF8", "410C0BB8", "410C0BB8"])  # 1726, dann 750 1/min
    original = car.responses

    class Responses(dict[str, str]):
        def get(self, key: str, default: object = None) -> object:  # type: ignore[override]
            return next(values) if key == "010C" else original.get(key, default)

        def __getitem__(self, key: str) -> str:
            return next(values) if key == "010C" else original[key]

    car.responses = Responses(original)
    args = ["live", "--pids", "rpm", "--duration", "3", "--alarm", "rpm=:1000", "--no-alarms"]
    assert main(args) == 0
    out, err = capsys.readouterr()
    assert [row.split()[1] for row in out.splitlines()[1:]] == ["1726!", "750", "750"]
    assert "Warnung: Motordrehzahl 1726 1/min über 1000 1/min" in err
    assert "Hinweis: Motordrehzahl wieder im Bereich." in err


def test_cli_no_alarms(car: SimpleNamespace, capsys: pytest.CaptureFixture[str]) -> None:
    car.responses["0105"] = "41059D"
    assert main(["live", "--pids", "coolant_temp", "--duration", "1", "--no-alarms"]) == 0
    out, err = capsys.readouterr()
    assert "!" not in out
    assert "Warnung" not in err


@pytest.mark.parametrize(
    ("alarm", "message"),
    [
        ("coolant_temp", "Form SCHLÜSSEL=UNTEN:OBEN erwartet"),
        ("nonsense=1:2", "unbekannter Wert: nonsense"),
        ("rpm=3:1", "untere Schwelle 3,0 muss kleiner sein als obere 1,0"),
    ],
)
def test_cli_alarm_errors(
    car: SimpleNamespace, capsys: pytest.CaptureFixture[str], alarm: str, message: str
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["live", "--alarm", alarm])
    assert exit_info.value.code == 2
    assert message in capsys.readouterr().err
