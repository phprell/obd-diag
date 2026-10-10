import itertools

import pytest

from obd_diag.protocol.elm327 import Elm327, ElmError
from obd_diag.services.readiness import (
    ALL_COMPLETE_LABEL,
    AU_NOTE,
    Monitor,
    MonitorState,
    ReadinessStatus,
    combine_readiness,
    decode_readiness,
    read_readiness,
)
from tests.fakes import FakeTransport
from tests.samples import READINESS

C = MonitorState.COMPLETE
I = MonitorState.INCOMPLETE  # noqa: E741
N = MonitorState.NOT_SUPPORTED

SPARK_KEYS = [
    "misfire",
    "fuel_system",
    "components",
    "catalyst",
    "heated_catalyst",
    "evap",
    "secondary_air",
    "ac_refrigerant",
    "oxygen_sensor",
    "oxygen_sensor_heater",
    "egr",
]
DIESEL_KEYS = [
    "misfire",
    "fuel_system",
    "components",
    "nmhc_catalyst",
    "nox_scr",
    "boost_pressure",
    "exhaust_gas_sensor",
    "pm_filter",
    "egr",
]


def _states(status: ReadinessStatus) -> dict[str, MonitorState]:
    return {m.key: m.state for m in status.monitors}


def test_sample_bytes_give_sample_status() -> None:
    # A: MIL an, 2 Codes; B: kontinuierliche unterstützt und fertig; C: Kat, EVAP,
    # Lambdasonde, Sondenheizung; D: Kat und Lambdasonde offen
    status = decode_readiness(bytes.fromhex("82076521"))
    without_ac = tuple(m for m in status.monitors if m.key != "ac_refrigerant")
    assert ReadinessStatus(True, 2, False, without_ac) == READINESS
    assert _states(status)["ac_refrigerant"] is N
    assert not status.ready


def test_all_keys_in_order() -> None:
    assert [m.key for m in decode_readiness(bytes(4)).monitors] == SPARK_KEYS
    assert [m.key for m in decode_readiness(b"\x00\x08\x00\x00").monitors] == DIESEL_KEYS


@pytest.mark.parametrize(
    ("a", "mil", "count"),
    [(0x00, False, 0), (0x81, True, 1), (0x7F, False, 127), (0xFF, True, 127)],
)
def test_byte_a(a: int, mil: bool, count: int) -> None:
    status = decode_readiness(bytes((a, 0, 0, 0)))
    assert (status.mil_on, status.dtc_count) == (mil, count)


def test_nothing_supported_is_ready() -> None:
    status = decode_readiness(bytes(4))
    assert set(_states(status).values()) == {N}
    assert status.ready
    assert not status.compression_ignition


@pytest.mark.parametrize("diesel", [False, True])
@pytest.mark.parametrize(("bit", "key"), [(0, "misfire"), (1, "fuel_system"), (2, "components")])
def test_continuous_monitors_bit_by_bit(diesel: bool, bit: int, key: str) -> None:
    flag = 0x08 if diesel else 0
    supported = decode_readiness(bytes((0, flag | 1 << bit, 0, 0)))
    assert {k: s for k, s in _states(supported).items() if s is not N} == {key: C}
    incomplete = decode_readiness(bytes((0, flag | 1 << bit | 1 << (bit + 4), 0, 0)))
    assert {k: s for k, s in _states(incomplete).items() if s is not N} == {key: I}
    assert not incomplete.ready
    # "nicht abgeschlossen" ohne "unterstützt" zählt nicht
    assert set(_states(decode_readiness(bytes((0, flag | 1 << (bit + 4), 0, 0)))).values()) == {N}


@pytest.mark.parametrize(
    ("bit", "key"),
    list(enumerate(SPARK_KEYS[3:])),
)
def test_spark_monitors_bit_by_bit(bit: int, key: str) -> None:
    supported = decode_readiness(bytes((0, 0, 1 << bit, 0)))
    assert {k: s for k, s in _states(supported).items() if s is not N} == {key: C}
    incomplete = decode_readiness(bytes((0, 0, 1 << bit, 1 << bit)))
    assert {k: s for k, s in _states(incomplete).items() if s is not N} == {key: I}
    assert set(_states(decode_readiness(bytes((0, 0, 0, 1 << bit)))).values()) == {N}


@pytest.mark.parametrize(
    ("bit", "key"),
    [
        (0, "nmhc_catalyst"),
        (1, "nox_scr"),
        (2, None),  # reserviert
        (3, "boost_pressure"),
        (4, None),  # reserviert
        (5, "exhaust_gas_sensor"),
        (6, "pm_filter"),
        (7, "egr"),
    ],
)
def test_diesel_monitors_bit_by_bit(bit: int, key: str | None) -> None:
    supported = decode_readiness(bytes((0, 0x08, 1 << bit, 0)))
    assert supported.compression_ignition
    expected = {} if key is None else {key: C}
    assert {k: s for k, s in _states(supported).items() if s is not N} == expected
    incomplete = decode_readiness(bytes((0, 0x08, 1 << bit, 1 << bit)))
    expected = {} if key is None else {key: I}
    assert {k: s for k, s in _states(incomplete).items() if s is not N} == expected


def test_reserved_bit_b7_and_extra_bytes_ignored() -> None:
    assert decode_readiness(b"\x00\x80\x00\x00\xff") == decode_readiness(bytes(4))


def test_names_are_german() -> None:
    names = {m.key: m.name for m in decode_readiness(b"\x00\x08\x00\x00").monitors}
    assert names["pm_filter"] == "Partikelfilter"
    assert names["egr"] == "Abgasrückführung"


@pytest.mark.parametrize("data", [b"", b"\x00", b"\x00\x00\x00"])
def test_too_short(data: bytes) -> None:
    with pytest.raises(ValueError, match="vier Datenbytes"):
        decode_readiness(data)


# --- Lesen und mehrere Steuergeräte ---


def _read(response: str) -> ReadinessStatus | None:
    return read_readiness(Elm327(FakeTransport({"0101": response})))


def test_read_single_ecu() -> None:
    assert _read("4101 8207 6521") == decode_readiness(bytes.fromhex("82076521"))


def test_read_no_data() -> None:
    assert _read("NO DATA") is None


def test_read_negative_or_short_answer() -> None:
    assert _read("7F0112") is None
    assert _read("410100") is None


def test_read_garbage_raises_elm_error() -> None:
    with pytest.raises(ElmError, match="0101"):
        _read("OK")


def test_read_combines_engine_and_transmission() -> None:
    # Motor: 1 Code, Kat offen; Getriebe: MIL aus, 1 Code, nur Komponenten, offen
    status = _read("41 01 81 07 65 01\r41 01 01 44 00 00")
    assert status is not None
    assert status.mil_on
    assert status.dtc_count == 2
    states = _states(status)
    assert states["catalyst"] is I
    assert states["components"] is I
    assert states["misfire"] is C
    assert states["heated_catalyst"] is N


def test_combine_ignores_other_ignition_type() -> None:
    engine = decode_readiness(bytes((0, 0x00, 0x01, 0x00)))
    other = decode_readiness(bytes((0, 0x08, 0x01, 0x01)))  # Bits anders belegt
    combined = combine_readiness([engine, other])
    assert not combined.compression_ignition
    assert _states(combined)["catalyst"] is C


@pytest.mark.parametrize("order", list(itertools.permutations(range(4))))
def test_combine_does_not_depend_on_order(order: tuple[int, ...]) -> None:
    # Mercedes A 180 d (W177), zweiter Test am Auto: ohne Header kommen die vier
    # Antworten auf 0101 jedes Mal anders sortiert. Stand 00 04 00 00 (Bit B3 = 0, aber
    # keine motorspezifischen Monitore) vorn, wurde daraus ein Ottomotor ohne Monitore.
    lines = ["00040000", "000EEB20", "010C0000", "000C0200"]
    statuses = [decode_readiness(bytes.fromhex(lines[i])) for i in order]
    combined = combine_readiness(statuses)
    assert combined.compression_ignition
    assert combined.dtc_count == 1
    assert not combined.mil_on
    states = _states(combined)
    assert [k for k, s in states.items() if s is I] == ["exhaust_gas_sensor"]
    assert states["misfire"] is N
    assert states["components"] is C
    assert states["nox_scr"] is C


def test_combine_continuous_monitors_from_all_ecus() -> None:
    # Aussetzer, Kraftstoffsystem, Komponenten (Byte B) bedeuten bei beiden Motorarten
    # dasselbe und zählen deshalb auch bei Steuergeräten mit anderer Kennung.
    diesel = decode_readiness(bytes((0, 0x0C, 0x01, 0x00)))
    other = decode_readiness(bytes((0, 0x44, 0x00, 0x00)))  # Komponenten offen
    combined = combine_readiness([other, diesel])
    assert combined.compression_ignition
    assert _states(combined)["components"] is I


def test_combine_needs_input() -> None:
    with pytest.raises(ValueError):
        combine_readiness([])


def test_combine_single_is_identity() -> None:
    assert combine_readiness([READINESS]) == READINESS
    assert READINESS.monitors[0] == Monitor("misfire", "Verbrennungsaussetzer", C)


def test_all_complete_and_compatible_alias() -> None:
    done = decode_readiness(bytes((0, 0x07, 0x65, 0x00)))
    open_ = decode_readiness(bytes((0, 0x07, 0x65, 0x21)))
    assert done.all_complete and done.ready
    assert not open_.all_complete and not open_.ready


def test_no_au_claim_in_labels() -> None:
    assert "AU" not in ALL_COMPLETE_LABEL
    assert "keine AU-Bewertung" in AU_NOTE
