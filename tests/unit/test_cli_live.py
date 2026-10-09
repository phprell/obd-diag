"""``obd-diag live``: Tabelle, --list, --pids, --record, --duration, Strg+C, Fehler.

Serieller Port und PID-Tabelle sind Fakes (``tests.fakes``, ``tests.live_fakes``), die
Uhr der Kommandozeile ebenfalls: ``--duration`` wartet nie wirklich.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from obd_diag import cli
from obd_diag.cli import main
from obd_diag.protocol import pids
from obd_diag.protocol.elm327 import is_read_only
from tests import live_fakes
from tests.fakes import CAN_CAR
from tests.live_fakes import LIVE_VALUES, use_fake_pids
from tests.unit.test_command_guard import INIT, PROTOCOL, WireCheckingTransport

SAFETY = "Hinweis: Während der Fahrt nur durch Beifahrer bedienen."


class FakeTime:
    """Ersatz für das Modul ``time`` in ``cli``; ``interrupt_after`` simuliert Strg+C."""

    def __init__(self, interrupt_after: int | None = None) -> None:
        self.now = 1000.0
        self.sleeps = 0
        self.interrupt_after = interrupt_after

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps += 1
        if self.interrupt_after is not None and self.sleeps > self.interrupt_after:
            raise KeyboardInterrupt
        self.now += seconds


Car = SimpleNamespace


@pytest.fixture
def car(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Car:
    """Laufender Motor am Fake-Port; Antworten, Uhr und Transporte lassen sich prüfen."""
    use_fake_pids(monkeypatch)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    state = SimpleNamespace(
        responses={**CAN_CAR, **LIVE_VALUES}, transports=[], time=FakeTime(), data=tmp_path
    )

    def open_port(port: str, baud: int) -> WireCheckingTransport:
        state.transports.append(WireCheckingTransport(state.responses))
        return state.transports[-1]  # type: ignore[no-any-return]

    monkeypatch.setattr(cli, "SerialTransport", open_port)
    monkeypatch.setattr(cli, "time", state.time)
    return state


def _sent(car: Car) -> list[str]:
    return [c for t in car.transports for c in t.sent]


def test_live_table_for_duration(car: Car, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["live", "--duration", "3"]) == 0
    out, err = capsys.readouterr()
    lines = out.splitlines()
    assert len(lines) == 4  # Kopf + 3 Runden (0, 1, 2 s)
    assert lines[0].split("  ") == [
        "Zeit (s)",
        "Motordrehzahl (1/min)",
        "Geschwindigkeit (km/h)",
        "Kühlmitteltemperatur (°C)",
        "Motorlast (%)",
        "Spannung (V)",
    ]
    assert lines[1].split() == ["0.0", "1726", "50", "86", "50.2", "12.4"]
    assert lines[3].split()[0] == "2.0"
    # feste Spaltenbreiten: jede Zeile so lang wie der Kopf, Werte rechtsbündig
    assert {len(line) for line in lines} == {len(lines[0])}
    assert lines[1].endswith(" 12.4")
    assert err.startswith(SAFETY + "\n")
    assert "Adapter: ELM327 v1.5, Protokoll: ISO 15765-4 (CAN 11/500)" in err
    assert err.endswith("Beendet nach 3 Runden.\n")
    assert "Aufzeichnung" not in err


def test_live_sends_only_reads_exactly(car: Car, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["live", "--pids", "rpm, SPEED", "--interval", "0.5", "--duration", "1"]) == 0
    sent = _sent(car)
    assert sent == [*INIT, *PROTOCOL, "0100", "ATRV", "010C", "010D", "010C", "010D"]
    assert all(is_read_only(c) for c in sent)
    assert capsys.readouterr().err.endswith("Beendet nach 2 Runden.\n")


def test_live_list(car: Car, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["live", "--list", "--pids", "foo"]) == 0  # --pids wird bei --list ignoriert
    out, err = capsys.readouterr()
    assert out.splitlines() == [
        "Schlüssel     Name                  Einheit",
        "engine_load   Motorlast             %",
        "coolant_temp  Kühlmitteltemperatur  °C",
        "rpm           Motordrehzahl         1/min",
        "speed         Geschwindigkeit       km/h",
    ]
    assert SAFETY not in err
    assert _sent(car) == [*INIT, *PROTOCOL, "0100"]


def test_live_list_without_known_values(car: Car, capsys: pytest.CaptureFixture[str]) -> None:
    car.responses["0100"] = "NO DATA"
    assert main(["live", "--list"]) == 0
    assert capsys.readouterr().out == "Das Fahrzeug meldet keine bekannten Live-Werte.\n"


def test_missing_values_show_a_hyphen(car: Car, capsys: pytest.CaptureFixture[str]) -> None:
    car.responses.update({"010D": "NO DATA", "ATRV": "?"})
    assert main(["live", "--pids", "rpm,speed", "--duration", "1"]) == 0
    assert capsys.readouterr().out.splitlines()[1].split() == ["0.0", "1726", "-", "-"]


def test_low_voltage_note(car: Car, capsys: pytest.CaptureFixture[str]) -> None:
    car.responses["ATRV"] = "11.5V"
    assert main(["live", "--pids", "rpm", "--duration", "6"]) == 0
    out, err = capsys.readouterr()
    assert "Hinweis: Bordspannung 11.5 V unter 11.8 V, Abfrage nur alle 5 s." in err
    assert [line.split()[0] for line in out.splitlines()[1:]] == ["0.0", "5.0"]


def test_record_to_automatic_path(car: Car, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["live", "--pids", "rpm,speed", "--duration", "2", "--record"]) == 0
    files = list((car.data / "obd-diag" / "recordings").glob("live-*.csv"))
    assert len(files) == 1
    assert files[0].read_text(encoding="utf-8-sig").splitlines() == [
        "Zeit (s);Motordrehzahl (1/min);Geschwindigkeit (km/h);Bordspannung (V)",
        "0;1726;50;12,4",
        "1;1726;50;12,4",
    ]
    err = capsys.readouterr().err
    assert err.endswith(f"Beendet nach 2 Runden.\nAufzeichnung: {files[0]}\n")


def test_record_never_overwrites(
    car: Car, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "fahrt.csv"
    path.write_text("alt", encoding="utf-8")
    assert main(["live", "--duration", "2", "--record", str(path)]) == 1
    assert path.read_text(encoding="utf-8") == "alt"
    assert capsys.readouterr().err.endswith(f"Fehler: {path}: File exists\n")
    assert not [c for c in _sent(car) if c in ("ATRV", "010C")]  # keine Runde gelaufen


def test_ctrl_c_ends_cleanly(
    car: Car, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    clock = FakeTime(interrupt_after=15)  # Strg+C während des Wartens nach Runde 1
    monkeypatch.setattr(cli, "time", clock)
    path = tmp_path / "fahrt.csv"
    assert main(["live", "--pids", "rpm", "--record", str(path)]) == 0
    out, err = capsys.readouterr()
    assert len(out.splitlines()) == 3  # Kopf + 2 Runden
    assert err.endswith(f"\nBeendet nach 2 Runden.\nAufzeichnung: {path}\n")
    assert len(path.read_text(encoding="utf-8-sig").splitlines()) == 3


def test_ctrl_c_during_setup(
    car: Car, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def interrupted(elm: object) -> set[int]:
        raise KeyboardInterrupt

    monkeypatch.setattr(pids, "read_supported_pids", interrupted)
    assert main(["live"]) == 0
    assert capsys.readouterr().err.endswith("Beendet nach 0 Runden.\n")


@pytest.mark.parametrize(
    ("pids", "message"),
    [
        ("foo", "Fehler: unbekannte Werte: foo (verfügbar: engine_load, coolant_temp, rpm, speed)"),
        ("maf,rpm", "Fehler: vom Fahrzeug nicht unterstützt: maf (verfügbar: "),
        (" , ", "Fehler: keine Werte gewählt"),
    ],
)
def test_bad_selection_is_an_error(
    car: Car, pids: str, message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["live", "--pids", pids]) == 1
    assert message in capsys.readouterr().err
    assert "ATRV" not in _sent(car)


def test_no_default_values_supported(
    car: Car, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(live_fakes, "SUPPORTED", {0x10})  # nur MAF
    assert main(["live"]) == 1
    assert "Fehler: Das Fahrzeug unterstützt keinen der Standardwerte" in capsys.readouterr().err


def test_adapter_error(car: Car, capsys: pytest.CaptureFixture[str]) -> None:
    car.responses["0100"] = "SEARCHING...\rUNABLE TO CONNECT"
    assert main(["live"]) == 1
    err = capsys.readouterr().err
    assert "Fehler: 0100: UNABLE TO CONNECT\nHinweis: Kein Steuergerät antwortet. Zündung" in err


@pytest.mark.parametrize(
    "argv",
    [
        ["--interval", "0.05"],
        ["--interval", "nan"],
        ["--interval", "schnell"],
        ["--duration", "0"],
        ["--duration", "-1"],
    ],
)
def test_invalid_options(car: Car, argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as info:
        main(["live", *argv])
    assert info.value.code == 2
    assert car.transports == []


def test_broken_vehicle_answer_is_named(
    car: Car, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Ein ValueError aus der Einrichtung (z. B. kaputte Frames) liegt nicht an der Auswahl
    def broken(elm: object) -> object:
        raise ValueError("Frame 2 fehlt")

    monkeypatch.setattr(cli, "prepare_live", broken)
    assert main(["live", "--pids", "rpm"]) == 1
    assert capsys.readouterr().err.endswith(
        "Fehler: Unerwartete Antwort vom Fahrzeug: Frame 2 fehlt\n"
    )
