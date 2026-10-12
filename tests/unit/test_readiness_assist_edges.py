"""Readiness-Assistent: Grenzfälle von PID 41 nach SAE J1979, Lesen, Prüfen und Ausgabe.

Ergänzt ``test_readiness_assist.py``. PID 41 ist wie PID 01 aufgebaut (Byte A reserviert,
B0-B2 freigegeben, B4-B6 nicht abgeschlossen, C/D motorspezifisch); die Antworten von
``0141`` hier sind ausgedacht, nicht vom echten Auto.
"""

import itertools
import json
import re
from datetime import timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from obd_diag import cli
from obd_diag.i18n import set_language, tr
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
from obd_diag.transport import TransportError
from tests.fakes import CAN_CAR_FULL, FakeTransport

C = CycleState.COMPLETE
I = CycleState.INCOMPLETE  # noqa: E741
D = CycleState.DISABLED

# Gleiches Bild wie PID 01: nicht freigegeben ~ nicht unterstützt
_SAME = {D: MonitorState.NOT_SUPPORTED, C: MonitorState.COMPLETE, I: MonitorState.INCOMPLETE}

# Befehlsfolge von ``check_readiness``: initialize, protocol, 0101, 0141
SEQUENCE = ["ATZ", "ATE0", "ATL0", "ATS0", "ATH0", "ATSP0", "0100", "ATDPN", "ATDP", "0101", "0141"]

byte = st.integers(0, 255)


def _states(status: DriveCycleStatus) -> dict[str, CycleState]:
    return {m.key: m.state for m in status.monitors}


def _elm(responses: dict[str, str]) -> Elm327:
    return Elm327(FakeTransport(responses))


class BrokenTransport(FakeTransport):
    """Fake, dessen Verbindung beim Senden von ``broken_at`` abreißt."""

    def __init__(self, responses: dict[str, str], broken_at: str) -> None:
        super().__init__(responses)
        self.broken_at = broken_at

    def write(self, data: bytes) -> None:
        if data.decode("ascii").strip() == self.broken_at:
            self.sent.append(self.broken_at)
            raise TransportError("Verbindung zu /dev/ttyUSB0 unterbrochen")
        super().write(data)


# --- Dekodieren nach J1979 --------------------------------------------------------


@given(byte, byte, byte, byte)
def test_same_picture_as_pid_01_for_the_same_bytes(a: int, b: int, c: int, d: int) -> None:
    # PID 41 B-D ist aufgebaut wie PID 01 B-D; Motorart hier wie in Bit B3 von PID 01
    pid01 = decode_readiness(bytes([a, b, c, d]))
    pid41 = decode_drive_cycle(bytes([a, b, c, d]), pid01.compression_ignition)
    assert [m.key for m in pid41.monitors] == [m.key for m in pid01.monitors]
    assert [m.name for m in pid41.monitors] == [m.name for m in pid01.monitors]
    assert [_SAME[m.state] for m in pid41.monitors] == [m.state for m in pid01.monitors]


@given(byte, byte, byte, byte, byte, st.binary(max_size=4), st.booleans())
def test_byte_a_and_reserved_bits_of_b_do_not_matter(
    a: int, a2: int, b: int, c: int, d: int, extra: bytes, diesel: bool
) -> None:
    # A reserviert, B3 (Motorart kommt vom Aufrufer) und B7 reserviert, Bytes nach D egal
    plain = decode_drive_cycle(bytes([a, b & 0x77, c, d]), diesel)
    noisy = decode_drive_cycle(bytes([a2, b | 0x88, c, d]) + extra, diesel)
    assert plain == noisy


@given(byte, byte, byte, st.booleans())
def test_not_enabled_is_always_disabled(b: int, c: int, d: int, diesel: bool) -> None:
    # Nicht freigegebene Monitore melden „ausgesetzt“, egal was im Bit „offen“ steht
    status = decode_drive_cycle(bytes([0, b & 0xF8, 0, d]), diesel)
    assert set(_states(status).values()) == {D}
    status = decode_drive_cycle(bytes([0, b & 0x07, c, 0]), diesel)
    assert I not in _states(status).values()


@pytest.mark.parametrize("diesel", [False, True])
@pytest.mark.parametrize("incomplete", [False, True])
def test_each_continuous_bit_affects_exactly_its_monitor(diesel: bool, incomplete: bool) -> None:
    for bit, key, _ in CONTINUOUS_MONITORS:
        b = 1 << bit | (1 << bit + 4 if incomplete else 0)
        states = _states(decode_drive_cycle(bytes([0, b, 0, 0]), diesel))
        assert states.pop(key) is (I if incomplete else C)
        assert set(states.values()) == {D}


@pytest.mark.parametrize("diesel", [False, True])
@pytest.mark.parametrize("incomplete", [False, True])
def test_each_specific_bit_affects_exactly_its_monitor(diesel: bool, incomplete: bool) -> None:
    for bit, key, _ in specific_monitors(diesel):
        c, d = 1 << bit, (1 << bit if incomplete else 0)
        states = _states(decode_drive_cycle(bytes([0, 0, c, d]), diesel))
        assert states.pop(key) is (I if incomplete else C)
        assert set(states.values()) == {D}


@pytest.mark.parametrize("bit", [2, 4])
def test_reserved_diesel_bits_are_ignored(bit: int) -> None:
    # Diesel: C2/D2 und C4/D4 sind in J1979 reserviert
    mask = 1 << bit
    assert decode_drive_cycle(bytes([0, 0, mask, mask]), True) == decode_drive_cycle(bytes(4), True)


@pytest.mark.parametrize(("diesel", "count"), [(False, 11), (True, 9)])
def test_all_bits_set_means_everything_open(diesel: bool, count: int) -> None:
    status = decode_drive_cycle(bytes.fromhex("FFFFFFFF"), diesel)
    assert len(status.monitors) == count
    assert set(_states(status).values()) == {I}


def test_layout_switches_byte_c_meaning() -> None:
    # C5/D5: Otto Lambdasonde, Diesel Abgassensor
    data = bytes.fromhex("00002020")
    assert decode_drive_cycle(data, False).state("oxygen_sensor") is I
    assert decode_drive_cycle(data, False).state("exhaust_gas_sensor") is None
    assert decode_drive_cycle(data, True).state("exhaust_gas_sensor") is I
    assert decode_drive_cycle(data, True).state("oxygen_sensor") is None


def test_too_short_message_names_the_count_in_english() -> None:
    set_language("en")
    with pytest.raises(ValueError, match=r"^PID 41 needs four data bytes, not 3$"):
        decode_drive_cycle(bytes(3), False)


# --- Zusammenfassen ----------------------------------------------------------------


@given(st.lists(st.binary(min_size=4, max_size=4), min_size=1, max_size=4), st.booleans())
def test_combine_is_order_independent_and_takes_the_worst(raw: list[bytes], diesel: bool) -> None:
    statuses = [decode_drive_cycle(r, diesel) for r in raw]
    combined = combine_drive_cycle(statuses)
    rank = {D: 0, C: 1, I: 2}
    for i, m in enumerate(combined.monitors):
        assert rank[m.state] == max(rank[s.monitors[i].state] for s in statuses)
    for order in itertools.permutations(statuses):
        assert combine_drive_cycle(list(order)) == combined
    assert combine_drive_cycle([*statuses, combined]) == combined


def test_combine_single_answer_is_unchanged() -> None:
    status = decode_drive_cycle(bytes.fromhex("0006E921"), True)
    assert combine_drive_cycle([status]) == status


# --- Lesen ---------------------------------------------------------------------------


@pytest.mark.parametrize("answer", ["4141 0007 6101", "41 41 00 07 61 01", "4141000761 01"])
def test_read_with_and_without_spaces(answer: str) -> None:
    status = read_drive_cycle(_elm({"0141": answer}), False)
    assert status is not None
    assert status.state("catalyst") is I
    assert status.state("evap") is D
    assert status.state("oxygen_sensor") is C


@pytest.mark.parametrize("answer", ["7F0131", "7F0122", "7F0111", "7F 01 12"])
def test_every_rejection_counts_as_not_answered(answer: str) -> None:
    assert read_drive_cycle(_elm({"0141": answer}), False) is None


def test_answer_to_another_pid_is_not_taken_for_pid_41() -> None:
    # eine verirrte Antwort auf PID 01 (gleiche Länge) darf nicht als PID 41 gelten
    assert read_drive_cycle(_elm({"0141": "41010007E500"}), False) is None
    assert read_drive_cycle(_elm({"0141": "4241000761"}), False) is None


def test_short_answer_of_one_ecu_does_not_hide_the_others() -> None:
    status = read_drive_cycle(_elm({"0141": "414100\r4141 0007 6101\r7F0131"}), False)
    assert status == decode_drive_cycle(bytes.fromhex("00076101"), False)


def test_extra_bytes_of_one_ecu_are_ignored() -> None:
    status = read_drive_cycle(_elm({"0141": "4141000761 01AA55"}), False)
    assert status == decode_drive_cycle(bytes.fromhex("00076101"), False)


def test_ecu_order_in_the_answer_does_not_matter() -> None:
    lines = ["41410006E921", "414100070000", "414100042001"]
    results = {
        read_drive_cycle(_elm({"0141": "\r".join(order)}), True)
        for order in itertools.permutations(lines)
    }
    assert len(results) == 1
    [status] = results
    assert status is not None
    # Abgassensor: Motor offen, drittes Steuergerät ohne D5 -> offen bleibt offen
    assert status.state("exhaust_gas_sensor") is I
    assert status.state("misfire") is C  # nur ein Steuergerät gibt B0 frei
    assert status.state("nmhc_catalyst") is I  # drittes Steuergerät: C0 frei, D0 offen


@pytest.mark.parametrize(("answer", "error"), [("?", "0141: ?"), ("41 41 0G", "0141")])
def test_read_unusable_answers_are_elm_errors(answer: str, error: str) -> None:
    with pytest.raises(ElmError, match=re.escape(error)):
        read_drive_cycle(_elm({"0141": answer}), False)


def test_read_sends_only_0141() -> None:
    transport = FakeTransport({"0141": "41410006E921"})
    read_drive_cycle(Elm327(transport), True)
    assert transport.sent == ["0141"]


# --- Prüfen --------------------------------------------------------------------------


def test_check_sends_exactly_the_read_only_sequence() -> None:
    transport = FakeTransport(CAN_CAR_FULL)
    check = check_readiness(Elm327(transport))
    assert transport.sent == SEQUENCE
    assert check.protocol == "ISO 15765-4 (CAN 11/500)"
    assert check.created.utcoffset() is not None  # mit Zeitzone
    assert abs(check.created - check.created.astimezone()) == timedelta(0)


def test_check_without_pid_41_answer() -> None:
    check = check_readiness(_elm({**CAN_CAR_FULL, "0141": "NO DATA"}))
    assert check.drive_cycle is None
    assert [(a.key, a.cycle) for a in check.advice] == [("catalyst", None), ("oxygen_sensor", None)]
    assert check.notes == [CODES_NOTE, NO_CYCLE_NOTE, GENERAL_NOTE]


@pytest.mark.parametrize("answer", ["?", "UNABLE TO CONNECT", "CAN ERROR", "41 41 ZZ"])
def test_check_keeps_pid_01_when_pid_41_fails(answer: str) -> None:
    check = check_readiness(_elm({**CAN_CAR_FULL, "0141": answer}))
    assert check.readiness is not None
    assert check.drive_cycle is None
    assert NO_CYCLE_NOTE in check.notes


@pytest.mark.parametrize("broken_at", ["0101", "0141"])
def test_connection_errors_reach_the_caller(broken_at: str) -> None:
    transport = BrokenTransport(CAN_CAR_FULL, broken_at)
    with pytest.raises(TransportError, match="unterbrochen"):
        check_readiness(Elm327(transport))
    assert transport.sent[-1] == broken_at
    assert transport.sent == SEQUENCE[: SEQUENCE.index(broken_at) + 1]


def test_check_decodes_pid_41_with_the_combined_engine_type() -> None:
    # Wie am W177: ein Steuergerät meldet B3 = 0 mit C = 00, die Mehrheit Diesel.
    # PID 41 muss mit Diesel-Belegung gelesen werden (C5/D5 = Abgassensor).
    responses = {
        **CAN_CAR_FULL,
        "0101": "4101000EEB20\r4101010C0000\r410100040000\r4101000C0200",
        "0141": "41410006E921",
    }
    check = check_readiness(_elm(responses))
    assert check.readiness is not None and check.readiness.compression_ignition
    assert check.drive_cycle is not None
    assert check.drive_cycle.state("exhaust_gas_sensor") is I
    assert check.drive_cycle.state("oxygen_sensor") is None
    assert [(a.key, a.cycle) for a in check.advice] == [("exhaust_gas_sensor", I)]


def test_check_spark_engine_ignores_diesel_bit_in_pid_41() -> None:
    # B3 in PID 41 gesetzt, PID 01 sagt Otto: Belegung bleibt Otto
    check = check_readiness(_elm({**CAN_CAR_FULL, "0141": "4141000F6101"}))
    assert check.drive_cycle is not None
    assert check.drive_cycle.state("catalyst") is I
    assert check.drive_cycle.state("nmhc_catalyst") is None


def test_check_all_complete_has_no_advice_even_with_open_drive_cycle() -> None:
    # PID 01: alles abgeschlossen; PID 41: Katalysator in dieser Fahrt offen
    check = check_readiness(_elm({**CAN_CAR_FULL, "0101": "41010007 6500"}))
    assert check.readiness is not None and check.readiness.all_complete
    assert check.drive_cycle is not None and check.drive_cycle.state("catalyst") is I
    assert check.advice == [] and check.notes == []


def test_advice_shows_disabled_monitors() -> None:
    # PID 01: Tankentlüftung offen; PID 41: in dieser Fahrt ausgesetzt (C2 = 0)
    check = check_readiness(
        _elm({**CAN_CAR_FULL, "0101": "41010007 6504", "0141": "41410007 6100"})
    )
    assert [(a.key, a.cycle) for a in check.advice] == [("evap", D)]
    assert check.advice[0].hint == HINTS["evap"]


def test_advice_keeps_the_order_of_pid_01() -> None:
    readiness = decode_readiness(bytes.fromhex("0077FFFF"))
    keys = [a.key for a in advise(readiness, None)]
    assert keys == [m.key for m in readiness.monitors]
    assert keys[:3] == ["misfire", "fuel_system", "components"]


@pytest.mark.parametrize(
    ("byte_a", "codes_note"), [("00", False), ("80", True), ("01", True), ("7F", True)]
)
def test_codes_note_for_mil_or_stored_codes(byte_a: str, codes_note: bool) -> None:
    readiness = decode_readiness(bytes.fromhex(byte_a + "076521"))
    cycle = decode_drive_cycle(bytes.fromhex("00076101"), False)
    assert (CODES_NOTE in notes(readiness, cycle)) is codes_note
    assert notes(readiness, cycle)[-2:] == [CYCLE_NOTE, GENERAL_NOTE]


# --- JSON und Kommandozeile ------------------------------------------------------------


def test_json_without_pid_01() -> None:
    check = check_readiness(_elm({**CAN_CAR_FULL, "0101": "NO DATA"}))
    data = json.loads(json.dumps(check_to_dict(check)))
    assert data["readiness"] is None and data["drive_cycle"] is None
    assert data["advice"] == [] and data["notes"] == []
    assert data["adapter"] == "ELM327 v1.5"


def test_json_without_pid_41() -> None:
    check = check_readiness(_elm({**CAN_CAR_FULL, "0141": "NO DATA"}))
    data = check_to_dict(check)
    assert data["drive_cycle"] is None
    assert [a["drive_cycle"] for a in data["advice"]] == [None, None]
    assert data["notes"][1] == tr(NO_CYCLE_NOTE)


def test_json_in_english_keeps_keys_and_states() -> None:
    set_language("en")
    data = check_to_dict(check_readiness(_elm(CAN_CAR_FULL)))
    assert data["advice"][0]["key"] == "catalyst"
    assert data["advice"][0]["name"] == "Catalyst"
    assert data["advice"][0]["drive_cycle"] == "incomplete"
    assert data["advice"][0]["hint"].startswith("With the engine")
    assert data["notes"][0].startswith("Trouble codes are stored")
    # Monitornamen in den Rohdaten bleiben deutsch (msgid), wie in Sitzungen
    assert data["drive_cycle"]["monitors"][3]["name"] == "Katalysator"


class Car:
    """Serieller Port als Fake: Antworten anpassbar, geöffnete Transporte in ``opened``."""

    def __init__(self) -> None:
        self.responses = dict(CAN_CAR_FULL)
        self.opened: list[FakeTransport] = []

    def open(self, port: str, baud: int) -> FakeTransport:
        self.opened.append(FakeTransport(self.responses))
        return self.opened[-1]


@pytest.fixture
def car(monkeypatch: pytest.MonkeyPatch) -> Car:
    fake = Car()
    monkeypatch.setattr(cli, "SerialTransport", fake.open)
    return fake


def _row(out: str, label: str) -> list[str]:
    [line] = [x for x in out.splitlines() if x.strip().startswith(label + ":")]
    return re.split(r"\s{2,}", line.split(":", 1)[1].strip())


def test_cli_table_with_both_columns(car: Car, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--lang", "de", "readiness", "--port", "/dev/ttyUSB0"]) == 0
    out = capsys.readouterr().out
    assert car.opened[0].sent == SEQUENCE
    assert "Readiness-Assistent (Otto-Motor):" in out
    assert re.search(r"Seit dem Löschen\s+In dieser Fahrt", out)
    assert _row(out, "Katalysator") == ["nicht abgeschlossen", "noch offen"]
    assert _row(out, "Lambdasonde") == ["nicht abgeschlossen", "abgeschlossen"]
    assert _row(out, "Tankentlüftung") == ["abgeschlossen", "ausgesetzt"]
    assert _row(out, "Sekundärluftsystem") == ["nicht unterstützt"]
    assert "  Katalysator (in dieser Fahrt: noch offen)" in out
    assert "Kontrollleuchte (MIL): an" in out
    assert "Gemeldete Fehlercodes: 3" in out


def test_cli_without_pid_41_has_one_column(car: Car, capsys: pytest.CaptureFixture[str]) -> None:
    car.responses["0141"] = "NO DATA"
    assert cli.main(["--lang", "de", "readiness", "--port", "/dev/ttyUSB0"]) == 0
    out = capsys.readouterr().out
    assert "Seit dem Löschen" in out and "In dieser Fahrt" not in out
    assert _row(out, "Katalysator") == ["nicht abgeschlossen"]
    assert "\n  Katalysator\n" in out  # Überschrift ohne Stand dieser Fahrt
    assert "PID 41" in out.replace("\n", " ")


def test_cli_without_pid_01(car: Car, capsys: pytest.CaptureFixture[str]) -> None:
    car.responses["0101"] = "NO DATA"
    assert cli.main(["--lang", "de", "readiness", "--port", "/dev/ttyUSB0"]) == 0
    out = capsys.readouterr().out
    assert "Readiness: nicht verfügbar (PID 01 nicht beantwortet)." in out
    assert "Offene Tests" not in out and "Hinweis" not in out
    assert "0141" not in car.opened[0].sent


def test_cli_all_complete_has_no_open_tests(car: Car, capsys: pytest.CaptureFixture[str]) -> None:
    car.responses["0101"] = "41010007 6500"
    assert cli.main(["--lang", "de", "readiness", "--port", "/dev/ttyUSB0"]) == 0
    out = capsys.readouterr().out
    assert "Alle Tests abgeschlossen: ja" in out
    assert "Offene Tests" not in out
    # nur der AU-Hinweis bleibt
    assert out.count("Hinweis:") == 1


def test_cli_diesel_header(car: Car, capsys: pytest.CaptureFixture[str]) -> None:
    car.responses["0101"] = "4101000EEB20"
    car.responses["0141"] = "41410006E921"
    assert cli.main(["--lang", "de", "readiness", "--port", "/dev/ttyUSB0"]) == 0
    out = capsys.readouterr().out
    assert "Readiness-Assistent (Diesel-Motor):" in out
    assert _row(out, "Abgassensor") == ["nicht abgeschlossen", "noch offen"]
    assert _row(out, "NOx-Nachbehandlung (SCR)") == ["abgeschlossen", "ausgesetzt"]


def test_cli_in_english(car: Car, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--lang", "en", "readiness", "--port", "/dev/ttyUSB0"]) == 0
    out = capsys.readouterr().out
    assert "Readiness assistant (Petrol engine):" in out
    assert re.search(r"Since codes cleared\s+This drive", out)
    assert _row(out, "Catalyst")[-1] == "incomplete"
    assert "Incomplete tests and which drive completes them:" in out
    assert "  Catalyst (this drive: incomplete)" in out
    assert "Katalysator" not in out and "Hinweis" not in out


def test_cli_json_in_english(car: Car, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--lang", "en", "readiness", "--port", "/dev/ttyUSB0", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [(a["key"], a["name"], a["drive_cycle"]) for a in data["advice"]] == [
        ("catalyst", "Catalyst", "incomplete"),
        ("oxygen_sensor", "Oxygen sensor", "complete"),
    ]
    assert data["readiness"]["dtc_count"] == 3 and data["readiness"]["mil_on"] is True


def test_cli_connection_error_exits_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        cli, "SerialTransport", lambda port, baud: BrokenTransport(CAN_CAR_FULL, "0141")
    )
    assert cli.main(["--lang", "de", "readiness", "--port", "/dev/ttyUSB0"]) == 1
    captured = capsys.readouterr()
    assert captured.err.startswith("Fehler: Verbindung zu /dev/ttyUSB0 unterbrochen")
    assert "Readiness-Assistent" not in captured.out


def test_cli_no_connection_shows_the_hint(car: Car, capsys: pytest.CaptureFixture[str]) -> None:
    car.responses["0100"] = "SEARCHING...\rUNABLE TO CONNECT"
    assert cli.main(["--lang", "de", "readiness", "--port", "/dev/ttyUSB0"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("Fehler: 0100: UNABLE TO CONNECT\nHinweis: Kein Steuergerät antwortet.")
    assert "0101" not in car.opened[0].sent
