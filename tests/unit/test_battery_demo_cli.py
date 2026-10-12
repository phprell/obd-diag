"""Batterietest am Demo-Fahrzeug und über ``obd-diag battery``."""

import functools
import json

import pytest

from obd_diag import cli
from obd_diag.protocol.elm327 import Elm327
from obd_diag.services import battery
from obd_diag.services.battery import (
    BatteryTestRefused,
    Rating,
    charge_verdict,
    crank_verdict,
    overall_rating,
    rest_verdict,
    run_battery_test,
)
from obd_diag.transport.demo import CRANK_AT, DemoTransport
from tests.battery_fakes import SimClock


def test_demo_engine_start_is_rated_good() -> None:
    clock = SimClock()
    with DemoTransport(engine_start=True, clock=clock) as transport:
        result = run_battery_test(Elm327(transport), clock=clock, sleep=clock.sleep)
    assert result.adapter.endswith("(Demo)")
    assert result.rest_adapter == 11.8
    assert result.rest_ecu == 12.55
    assert result.offset == pytest.approx(0.75)
    assert result.coolant_temp == 18
    assert result.crank_minimum == pytest.approx(10.25)
    assert result.crank_lowest == pytest.approx(9.65)  # Einschaltspitze 8,9 V
    assert result.started
    assert result.charge_ecu == pytest.approx(14.8, abs=0.05)
    ratings = [v(result).rating for v in (rest_verdict, crank_verdict, charge_verdict)]
    assert ratings == [Rating.GOOD] * 3
    assert overall_rating(result) is Rating.GOOD
    # Anlassen CRANK_AT s nach der ersten Drehzahlabfrage
    first = next(t for t, v in result.curve if v < 11)
    assert first == pytest.approx(CRANK_AT, abs=0.2)


def test_demo_without_engine_start_runs_and_is_refused() -> None:
    clock = SimClock()
    with DemoTransport(clock=clock) as transport, pytest.raises(BatteryTestRefused):
        run_battery_test(Elm327(transport), clock=clock, sleep=clock.sleep)


def _fast(monkeypatch: pytest.MonkeyPatch, *, engine_start: bool = True) -> None:
    """Simulierte Uhr statt Wartezeit; ohne ``engine_start`` läuft der Motor des Demo-Autos."""
    clock = SimClock()
    wanted = engine_start
    monkeypatch.setattr(
        cli,
        "run_battery_test",
        functools.partial(battery.run_battery_test, clock=clock, sleep=clock.sleep),
    )
    monkeypatch.setattr(
        cli,
        "DemoTransport",
        lambda engine_start=False: DemoTransport(engine_start=engine_start and wanted, clock=clock),
    )


def test_cli_battery_demo(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fast(monkeypatch)
    assert cli.main(["--lang", "de", "battery", "--demo"]) == 0
    out, err = capsys.readouterr()
    assert "Jetzt den Motor starten. (Zeit: 30 s)" in err
    assert "Anlassen erkannt" in err
    assert "Ruhespannung: 12,6 V: gut geladen" in out
    assert "Anlassen:     10,2 V (Grenze 9,5 V): gut" in out
    assert "Ergebnis:     Batterie und Ladung in Ordnung" in out
    assert "korrigiert" in out


def test_cli_battery_json_english(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fast(monkeypatch)
    assert cli.main(["battery", "--demo", "--json", "--lang", "en"]) == 0
    out, err = capsys.readouterr()
    data = json.loads(out)
    assert data["ratings"]["overall"] == "good"
    assert "Start the engine now." in err


def test_cli_battery_refused(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fast(monkeypatch, engine_start=False)
    assert cli.main(["--lang", "de", "battery", "--demo"]) == 1
    assert "Der Motor läuft" in capsys.readouterr().err


def test_cli_battery_start_timeout(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fast(monkeypatch)
    assert cli.main(["--lang", "de", "battery", "--demo", "--start-timeout", "2"]) == 0
    out = capsys.readouterr().out
    assert "kein Anlassen erkannt" in out
    assert "nicht gemessen: Motor ist nicht angesprungen" in out
