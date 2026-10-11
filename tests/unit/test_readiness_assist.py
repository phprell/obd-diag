"""Readiness-Assistent (``services/readiness_assist.py``): PID 41 und Fahrhinweise."""

import itertools
import json
from dataclasses import replace

import pytest

from obd_diag import cli
from obd_diag.i18n import tr
from obd_diag.protocol.elm327 import Elm327, ElmError
from obd_diag.services.readiness import (
    CONTINUOUS_MONITORS,
    MonitorState,
    decode_readiness,
    specific_monitors,
)
from obd_diag.services.readiness_assist import (
    CODES_NOTE,
    CYCLE_NOTE,
    CYCLE_STATE_LABELS,
    CYCLE_STATE_TEXTS,
    DEFAULT_HINT,
    GENERAL_NOTE,
    HINTS,
    NO_CYCLE_NOTE,
    CycleState,
    DriveCycleStatus,
    advise,
    check_readiness,
    check_to_dict,
    combine_drive_cycle,
    decode_drive_cycle,
    notes,
    read_drive_cycle,
)
from obd_diag.transport.demo import DemoTransport
from tests.fakes import CAN_CAR_FULL, FakeTransport

C = CycleState.COMPLETE
I = CycleState.INCOMPLETE  # noqa: E741
D = CycleState.DISABLED


def _states(status: DriveCycleStatus) -> dict[str, CycleState]:
    return {m.key: m.state for m in status.monitors}


def _elm(responses: dict[str, str]) -> Elm327:
    return Elm327(FakeTransport(responses))


# --- Dekodieren --------------------------------------------------------------------


def test_spark_bytes() -> None:
    # B: Aussetzer und Kraftstoff freigegeben, Kraftstoff offen; Komponenten ausgesetzt.
    # C: Kat, Lambdasonde, Sondenheizung freigegeben; D: Kat offen (EVAP-Bit ohne
    # Freigabe zählt nicht).
    status = decode_drive_cycle(bytes.fromhex("00236105"), compression_ignition=False)
    assert _states(status) == {
        "misfire": C,
        "fuel_system": I,
        "components": D,
        "catalyst": I,
        "heated_catalyst": D,
        "evap": D,
        "secondary_air": D,
        "ac_refrigerant": D,
        "oxygen_sensor": C,
        "oxygen_sensor_heater": C,
        "egr": D,
    }


def test_diesel_layout_comes_from_the_caller_not_from_byte_b() -> None:
    # Bit B3 = 0, trotzdem Diesel-Belegung, weil PID 01 Diesel gemeldet hat
    status = decode_drive_cycle(bytes.fromhex("0006E921"), compression_ignition=True)
    assert _states(status) == {
        "misfire": D,
        "fuel_system": C,
        "components": C,
        "nmhc_catalyst": I,
        "nox_scr": D,
        "boost_pressure": C,
        "exhaust_gas_sensor": I,
        "pm_filter": C,
        "egr": C,
    }


def test_same_keys_and_order_as_pid_01() -> None:
    for diesel in (False, True):
        pid01 = decode_readiness(bytes.fromhex("00080000" if diesel else "00000000"))
        pid41 = decode_drive_cycle(bytes(4), diesel)
        assert [m.key for m in pid41.monitors] == [m.key for m in pid01.monitors]
        assert all(m.state is D for m in pid41.monitors)  # nichts freigegeben


def test_byte_a_and_extra_bytes_are_ignored() -> None:
    a = decode_drive_cycle(bytes.fromhex("FF07FF00"), False)
    b = decode_drive_cycle(bytes.fromhex("0007FF00AABB"), False)
    assert a == b
    assert set(_states(a).values()) == {C}


@pytest.mark.parametrize("data", ["", "00", "000700"])
def test_too_short(data: str) -> None:
    with pytest.raises(ValueError, match="PID 41"):
        decode_drive_cycle(bytes.fromhex(data), False)


def test_combine_takes_the_worst_state_in_any_order() -> None:
    engine = decode_drive_cycle(bytes.fromhex("0006E921"), True)
    other = decode_drive_cycle(bytes.fromhex("00070000"), True)  # nur Byte B
    gearbox = decode_drive_cycle(bytes.fromhex("00000000"), True)
    for order in itertools.permutations([engine, other, gearbox]):
        combined = combine_drive_cycle(list(order))
        states = _states(combined)
        assert states["misfire"] is C  # nur von „other“ freigegeben und fertig
        assert states["exhaust_gas_sensor"] is I
        assert states["nox_scr"] is D
    with pytest.raises(ValueError):
        combine_drive_cycle([])


# --- Lesen ---------------------------------------------------------------------------


def test_read_several_ecus() -> None:
    elm = _elm({"0141": "41410006E921\r414100040000\r7F0112"})
    status = read_drive_cycle(elm, compression_ignition=True)
    assert status is not None
    assert status.state("exhaust_gas_sensor") is I
    assert status.state("unknown") is None


@pytest.mark.parametrize("answer", ["NO DATA", "7F0112", "414100"])
def test_read_without_usable_answer(answer: str) -> None:
    assert read_drive_cycle(_elm({"0141": answer}), False) is None


def test_read_garbage_is_elm_error() -> None:
    with pytest.raises(ElmError):
        read_drive_cycle(_elm({"0141": "41 41 ZZ"}), False)


def test_check_reads_pid_01_then_41() -> None:
    transport = FakeTransport(CAN_CAR_FULL)
    check = check_readiness(Elm327(transport))
    assert transport.sent[-2:] == ["0101", "0141"]
    assert check.adapter == "ELM327 v1.5"
    assert check.readiness is not None and check.drive_cycle is not None
    assert [(a.key, a.cycle) for a in check.advice] == [("catalyst", I), ("oxygen_sensor", C)]
    assert check.notes == [CODES_NOTE, CYCLE_NOTE, GENERAL_NOTE]


def test_check_without_pid_01_does_not_ask_41() -> None:
    transport = FakeTransport({**CAN_CAR_FULL, "0101": "NO DATA"})
    check = check_readiness(Elm327(transport))
    assert "0141" not in transport.sent
    assert check.readiness is None and check.drive_cycle is None
    assert check.advice == [] and check.notes == []


def test_check_survives_broken_answers() -> None:
    check = check_readiness(_elm({**CAN_CAR_FULL, "0141": "41 41 ZZ"}))
    assert check.readiness is not None and check.drive_cycle is None
    assert NO_CYCLE_NOTE in check.notes
    check = check_readiness(_elm({**CAN_CAR_FULL, "0101": "41 01 ZZ"}))
    assert check.readiness is None


# --- Hinweise ------------------------------------------------------------------------


def test_every_monitor_has_a_hint() -> None:
    keys = {key for _, key, _ in CONTINUOUS_MONITORS}
    keys |= {key for diesel in (False, True) for _, key, _ in specific_monitors(diesel)}
    assert set(HINTS) == keys
    assert set(CYCLE_STATE_LABELS) == set(CycleState) == set(CYCLE_STATE_TEXTS)


def test_advice_only_for_incomplete_monitors() -> None:
    readiness = decode_readiness(bytes.fromhex("000EEB20"))  # W177: Abgassensor offen
    assert [a.key for a in advise(readiness, None)] == ["exhaust_gas_sensor"]
    assert advise(readiness, None)[0].cycle is None
    assert advise(readiness, None)[0].hint == HINTS["exhaust_gas_sensor"]
    assert notes(readiness, None) == [NO_CYCLE_NOTE, GENERAL_NOTE]
    assert notes(readiness, None, cycle_read=False) == [GENERAL_NOTE]


def test_unknown_key_gets_the_default_hint() -> None:
    readiness = decode_readiness(bytes.fromhex("00110000"))  # Aussetzer offen
    monitors = tuple(replace(m, key="new") if m.key == "misfire" else m for m in readiness.monitors)
    [advice] = advise(replace(readiness, monitors=monitors), None)
    assert advice.hint == DEFAULT_HINT


def test_nothing_open_needs_no_notes() -> None:
    readiness = decode_readiness(bytes.fromhex("81076500"))  # MIL an, aber alles fertig
    assert advise(readiness, None) == []
    assert notes(readiness, None) == []


# --- Ausgabe ---------------------------------------------------------------------------


def test_json_form() -> None:
    check = check_readiness(_elm(CAN_CAR_FULL))
    data = json.loads(json.dumps(check_to_dict(check)))
    assert data["readiness"]["all_complete"] is False
    assert data["drive_cycle"]["monitors"][3] == {
        "key": "catalyst",
        "name": "Katalysator",
        "state": "incomplete",
    }
    assert [a["drive_cycle"] for a in data["advice"]] == ["incomplete", "complete"]
    assert data["advice"][0]["hint"] == tr(HINTS["catalyst"])


def test_demo_has_the_open_exhaust_gas_sensor() -> None:
    with DemoTransport() as transport:
        check = check_readiness(Elm327(transport))
    assert check.readiness is not None and check.readiness.compression_ignition
    assert [(a.key, a.cycle) for a in check.advice] == [("exhaust_gas_sensor", I)]
    assert check.drive_cycle is not None
    assert check.drive_cycle.state("nox_scr") is D
    states = {m.key: m.state for m in check.readiness.monitors}
    assert states["misfire"] is MonitorState.NOT_SUPPORTED


@pytest.mark.parametrize(("lang", "expected"), [("de", "In dieser Fahrt"), ("en", "This drive")])
def test_cli(lang: str, expected: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--lang", lang, "readiness", "--demo"]) == 0
    out = capsys.readouterr().out
    assert expected in out
    assert tr(HINTS["exhaust_gas_sensor"])[:30] in out.replace("\n    ", " ")


def test_cli_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--lang", "de", "readiness", "--demo", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [a["key"] for a in data["advice"]] == ["exhaust_gas_sensor"]
    assert data["notes"]


def test_cli_diagnose_points_to_the_assistant(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--lang", "de", "diagnose", "--demo"]) == 0
    assert "obd-diag readiness" in capsys.readouterr().out
