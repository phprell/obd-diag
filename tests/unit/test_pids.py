"""Mode-01-PIDs: Formeln nach SAE J1979 (Wikipedia-Tabelle „OBD-II PIDs“), Bitmasken
der unterstützten PIDs (ELM327-Datenblatt) und das Lesen über den Adapter."""

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from obd_diag.protocol import pids
from obd_diag.protocol.elm327 import Elm327, ElmError
from obd_diag.protocol.pids import (
    PIDS,
    PidSpec,
    parse_supported,
    pid_by_key,
    read_supported_pids,
    read_value,
    read_values,
)
from tests.fakes import FakeTransport

# Schlüssel, auf die sich Live-Dienst, CLI und GUI verlassen: Schlüssel -> (PID, Einheit)
REQUIRED = {
    "engine_load": (0x04, "%"),
    "coolant_temp": (0x05, "°C"),
    "stft_bank1": (0x06, "%"),
    "ltft_bank1": (0x07, "%"),
    "stft_bank2": (0x08, "%"),
    "ltft_bank2": (0x09, "%"),
    "fuel_pressure": (0x0A, "kPa"),
    "intake_pressure": (0x0B, "kPa"),
    "rpm": (0x0C, "1/min"),
    "speed": (0x0D, "km/h"),
    "timing_advance": (0x0E, "°"),
    "intake_temp": (0x0F, "°C"),
    "maf": (0x10, "g/s"),
    "throttle": (0x11, "%"),
    "run_time": (0x1F, "s"),
    "distance_with_mil": (0x21, "km"),
    "fuel_rail_pressure": (0x23, "kPa"),
    "commanded_egr": (0x2C, "%"),
    "egr_error": (0x2D, "%"),
    "fuel_level": (0x2F, "%"),
    "distance_since_clear": (0x31, "km"),
    "baro_pressure": (0x33, "kPa"),
    "catalyst_temp_b1s1": (0x3C, "°C"),
    "control_voltage": (0x42, "V"),
    "absolute_load": (0x43, "%"),
    "commanded_lambda": (0x44, "Lambda"),
    "relative_throttle": (0x45, "%"),
    "ambient_temp": (0x46, "°C"),
    "accelerator_pedal_d": (0x49, "%"),
    "oil_temp": (0x5C, "°C"),
    "fuel_rate": (0x5E, "L/h"),
    "odometer": (0xA6, "km"),
}


@pytest.mark.parametrize(("key", "expected"), REQUIRED.items())
def test_required_keys(key: str, expected: tuple[int, str]) -> None:
    spec = pid_by_key(key)
    assert (spec.pid, spec.unit) == expected
    assert PIDS[key] is spec


def test_table_is_consistent() -> None:
    assert len(pids._TABLE) == len(PIDS)  # kein Schlüssel doppelt
    assert [s.pid for s in PIDS.values()] == sorted(s.pid for s in PIDS.values())
    for key, spec in PIDS.items():
        assert spec.key == key
        assert 1 <= spec.size <= 5
        assert spec.minimum < spec.maximum
        assert spec.name and spec.name[0].isupper()
        assert spec.pid % 0x20 != 0  # Bitmasken-PIDs sind keine Live-Werte


def test_unknown_key() -> None:
    with pytest.raises(KeyError, match=r"unbekannter Live-Wert 'drehzahl'.*rpm"):
        pid_by_key("drehzahl")


# --- Formeln ---------------------------------------------------------------------

# (Schlüssel, Datenbytes hex, erwarteter Wert); je Formel unterer Rand, oberer Rand und
# ein typischer Wert, nachgerechnet mit der Formel aus J1979 / Wikipedia.
EXAMPLES = [
    ("engine_load", "00", 0.0),
    ("engine_load", "FF", 100.0),
    ("engine_load", "80", 128 * 100 / 255),  # 50,2 %
    ("coolant_temp", "00", -40),
    ("coolant_temp", "FF", 215),
    ("coolant_temp", "7B", 83),
    ("stft_bank1", "00", -100.0),
    ("stft_bank1", "80", 0.0),
    ("stft_bank1", "FF", 99.21875),
    ("ltft_bank1", "84", 3.125),
    ("stft_bank2", "7C", -3.125),
    ("ltft_bank2", "80", 0.0),
    ("fuel_pressure", "00", 0),
    ("fuel_pressure", "FF", 765),
    ("fuel_pressure", "64", 300),
    ("intake_pressure", "21", 33),
    ("intake_pressure", "FF", 255),
    ("rpm", "0000", 0.0),
    ("rpm", "FFFF", 16383.75),
    ("rpm", "1AF8", 1726.0),  # ELM327-Datenblatt: 41 0C 1A F8
    ("rpm", "0BB8", 750.0),
    ("speed", "00", 0),
    ("speed", "FF", 255),
    ("speed", "32", 50),
    ("timing_advance", "00", -64.0),
    ("timing_advance", "FF", 63.5),
    ("timing_advance", "94", 10.0),
    ("intake_temp", "41", 25),
    ("maf", "0000", 0.0),
    ("maf", "FFFF", 655.35),
    ("maf", "0190", 4.0),
    ("throttle", "FF", 100.0),
    ("throttle", "33", 20.0),
    ("run_time", "FFFF", 65535),
    ("run_time", "012C", 300),
    ("distance_with_mil", "0100", 256),
    ("fuel_rail_pressure_relative", "FFFF", 5177.265),
    ("fuel_rail_pressure_relative", "03E8", 79.0),
    ("fuel_rail_pressure", "0000", 0),
    ("fuel_rail_pressure", "FFFF", 655350),
    ("fuel_rail_pressure", "2710", 100000),  # 1000 bar Raildruck (Diesel)
    ("commanded_egr", "FF", 100.0),
    ("egr_error", "00", -100.0),
    ("egr_error", "FF", 99.21875),
    ("commanded_evap_purge", "00", 0.0),
    ("fuel_level", "FF", 100.0),
    ("fuel_level", "66", 40.0),
    ("warmups_since_clear", "0A", 10),
    ("distance_since_clear", "FFFF", 65535),
    ("distance_since_clear", "04D2", 1234),
    ("evap_pressure", "0000", 0.0),
    ("evap_pressure", "8000", -8192.0),  # Zweierkomplement
    ("evap_pressure", "7FFF", 8191.75),
    ("evap_pressure", "FFFC", -1.0),
    ("baro_pressure", "65", 101),
    ("catalyst_temp_b1s1", "0000", -40.0),
    ("catalyst_temp_b1s1", "FFFF", 6513.5),
    ("catalyst_temp_b1s1", "1D4C", 710.0),
    ("catalyst_temp_b2s1", "0190", 0.0),
    ("catalyst_temp_b1s2", "0190", 0.0),
    ("catalyst_temp_b2s2", "0190", 0.0),
    ("control_voltage", "0000", 0.0),
    ("control_voltage", "FFFF", 65.535),
    ("control_voltage", "3070", 12.4),
    ("absolute_load", "0000", 0.0),
    ("absolute_load", "FFFF", 25700.0),
    ("absolute_load", "00FF", 100.0),
    ("commanded_lambda", "0000", 0.0),
    ("commanded_lambda", "8000", 1.0),
    ("commanded_lambda", "FFFF", 65535 * 2 / 65536),  # knapp unter 2
    ("relative_throttle", "FF", 100.0),
    ("ambient_temp", "00", -40),
    ("ambient_temp", "3C", 20),
    ("throttle_b", "FF", 100.0),
    ("accelerator_pedal_d", "00", 0.0),
    ("accelerator_pedal_d", "FF", 100.0),
    ("accelerator_pedal_e", "33", 20.0),
    ("commanded_throttle", "FF", 100.0),
    ("time_with_mil", "003C", 60),
    ("time_since_clear", "FFFF", 65535),
    ("ethanol_percent", "FF", 100.0),
    ("relative_accelerator_pedal", "00", 0.0),
    ("hybrid_battery_level", "FF", 100.0),
    ("oil_temp", "00", -40),
    ("oil_temp", "FF", 215),
    ("oil_temp", "82", 90),
    ("injection_timing", "0000", -210.0),
    ("injection_timing", "FFFF", 301.9921875),
    ("injection_timing", "6900", 0.0),
    ("fuel_rate", "0000", 0.0),
    ("fuel_rate", "FFFF", 3276.75),
    ("fuel_rate", "0064", 5.0),
    ("demand_torque", "00", -125),
    ("demand_torque", "FF", 130),
    ("actual_torque", "7D", 0),
    ("reference_torque", "0190", 400),
    ("odometer", "00000000", 0.0),
    ("odometer", "FFFFFFFF", 429496729.5),
    ("odometer", "001E8481", 200000.1),  # 2.000.001 / 10
    # 55-58: A = erste, B = zweite Bank (55/56: Bank 1 und 3, 57/58: Bank 2 und 4)
    ("stft_secondary_bank1", "80", 0.0),
    ("stft_secondary_bank1", "00", -100.0),
    ("stft_secondary_bank1", "FF", 99.21875),
    ("stft_secondary_bank3", "0080", 0.0),
    ("stft_secondary_bank3", "FF00", -100.0),
    ("stft_secondary_bank3", "00FF", 99.21875),
    ("ltft_secondary_bank1", "84", 3.125),
    ("ltft_secondary_bank3", "007C", -3.125),
    ("stft_secondary_bank2", "84", 3.125),
    ("stft_secondary_bank4", "007C", -3.125),
    ("ltft_secondary_bank2", "80", 0.0),
    ("ltft_secondary_bank4", "0080", 0.0),
    *(
        (key, data, value)
        for key, size in (
            ("ltft_secondary_bank1", 1),
            ("ltft_secondary_bank3", 2),
            ("stft_secondary_bank2", 1),
            ("stft_secondary_bank4", 2),
            ("ltft_secondary_bank2", 1),
            ("ltft_secondary_bank4", 2),
        )
        for data, value in ((("00" * size), -100.0), ("00" * (size - 1) + "FF", 99.21875))
    ),
    # 64: A Leerlauf, B-E Stützpunkte 1-4, je A - 125 %
    ("torque_idle", "00", -125),
    ("torque_idle", "FF", 130),
    ("torque_idle", "7D", 0),
    ("torque_point1", "7D00", -125),
    ("torque_point1", "00FF", 130),
    ("torque_point2", "0000A5", 40),
    ("torque_point2", "000000", -125),
    ("torque_point2", "0000FF", 130),
    ("torque_point3", "000000FF", 130),
    ("torque_point3", "00000000", -125),
    ("torque_point4", "0000000091", 20),
    ("torque_point4", "FFFFFFFF00", -125),
    ("torque_point4", "00000000FF", 130),
    # 66: Statusbyte A (Bit 0: Sensor A, Bit 1: Sensor B), dann je (256X+Y)/32 g/s
    ("maf_a", "010020", 1.0),
    ("maf_a", "010000", 0.0),
    ("maf_a", "01FFFF", 2047.96875),
    ("maf_a", "00FFFF", None),  # Sensor A laut Status nicht vorhanden
    ("maf_a", "02FFFF", None),
    ("maf_b", "0200000020", 1.0),
    ("maf_b", "0300000000", 0.0),
    ("maf_b", "020000FFFF", 2047.96875),
    ("maf_b", "01FFFFFFFF", None),
    # 67: Statusbyte A (Bit 0: Sensor 1, Bit 1: Sensor 2), dann je A - 40 °C
    ("coolant_temp_1", "017B", 83),
    ("coolant_temp_1", "0100", -40),
    ("coolant_temp_1", "03FF", 215),
    ("coolant_temp_1", "027B", None),
    ("coolant_temp_2", "02007B", 83),
    ("coolant_temp_2", "020000", -40),
    ("coolant_temp_2", "0300FF", 215),
    ("coolant_temp_2", "01007B", None),
]

# Lambdasonden 1-8 haben je Art dieselbe Kodierung (nachgerechnet nach J1979)
for _n in range(1, 9):
    EXAMPLES += [
        # 14-1B: A/200 V; B: 100/128 B - 100 %, B = FF: nicht im Trimm
        (f"o2_s{_n}_voltage", "00", 0.0),
        (f"o2_s{_n}_voltage", "FF", 1.275),
        (f"o2_s{_n}_voltage", "5A", 0.45),
        (f"o2_s{_n}_trim", "5A80", 0.0),
        (f"o2_s{_n}_trim", "5A00", -100.0),
        (f"o2_s{_n}_trim", "5AFE", 98.4375),
        (f"o2_s{_n}_trim", "5AFF", None),
        # 24-2B: Lambda 2/65536 (256A+B), Spannung 8/65536 (256C+D) V
        (f"o2_s{_n}_lambda", "8000", 1.0),
        (f"o2_s{_n}_lambda", "0000", 0.0),
        (f"o2_s{_n}_lambda", "FFFF", 65535 * 2 / 65536),
        (f"o2_s{_n}_wide_voltage", "80008000", 4.0),
        (f"o2_s{_n}_wide_voltage", "80000000", 0.0),
        (f"o2_s{_n}_wide_voltage", "8000FFFF", 65535 * 8 / 65536),
        # 34-3B: Lambda wie oben, Strom (256C+D)/256 - 128 mA
        (f"o2_s{_n}_lambda_current", "7400", 0.90625),
        (f"o2_s{_n}_lambda_current", "0000", 0.0),
        (f"o2_s{_n}_lambda_current", "FFFF", 65535 * 2 / 65536),
        (f"o2_s{_n}_current", "80008000", 0.0),
        (f"o2_s{_n}_current", "80007F00", -1.0),
        (f"o2_s{_n}_current", "80000000", -128.0),
        (f"o2_s{_n}_current", "8000FFFF", 127.99609375),
    ]

# Werte mit Status- bzw. Gültigkeitsangabe: dürfen None ergeben
MAY_BE_INVALID = {
    *(f"o2_s{n}_trim" for n in range(1, 9)),
    "maf_a",
    "maf_b",
    "coolant_temp_1",
    "coolant_temp_2",
}


@pytest.mark.parametrize(("key", "data", "expected"), EXAMPLES)
def test_formula(key: str, data: str, expected: float | None) -> None:
    spec = pid_by_key(key)
    raw = bytes.fromhex(data)
    assert len(raw) == spec.size
    if expected is None:
        assert spec.decode(raw) is None
    else:
        assert spec.decode(raw) == pytest.approx(expected)


def test_every_pid_has_examples() -> None:
    assert {key for key, _, _ in EXAMPLES} == {spec.key for spec in PIDS.values()}


SIGNED = {"evap_pressure"}  # Zweierkomplement: Extremwerte nicht bei 00…/FF…


def _single(spec: PidSpec) -> bool:
    return [s.pid for s in PIDS.values()].count(spec.pid) == 1


@pytest.mark.parametrize("spec", [s for s in PIDS.values() if _single(s)], ids=lambda s: s.key)
def test_bounds_are_reached(spec: PidSpec) -> None:
    if spec.key in SIGNED:
        low, high = b"\x80" + bytes(spec.size - 1), b"\x7f" + b"\xff" * (spec.size - 1)
    else:
        low, high = bytes(spec.size), b"\xff" * spec.size
    assert spec.decode(low) == pytest.approx(spec.minimum)
    assert spec.decode(high) == pytest.approx(spec.maximum)


@pytest.mark.parametrize("spec", [s for s in PIDS.values() if not _single(s)], ids=lambda s: s.key)
def test_bounds_of_shared_pids_appear_in_examples(spec: PidSpec) -> None:
    # Teilt sich eine PID mehrere Werte, liegen die Ränder nicht bei 00…/FF…; die
    # Beispiele oben müssen sie dann ausdrücklich enthalten.
    values = [v for k, _, v in EXAMPLES if k == spec.key and v is not None]
    assert min(values) == pytest.approx(spec.minimum)
    assert max(values) == pytest.approx(spec.maximum)


@given(st.sampled_from(list(PIDS.values())), st.data())
def test_every_decoding_stays_in_range(spec: PidSpec, data: st.DataObject) -> None:
    raw = data.draw(st.binary(min_size=spec.size, max_size=spec.size))
    value = spec.decode(raw)
    if value is None:  # nur Werte mit Status- bzw. Gültigkeitsangabe
        assert spec.key in MAY_BE_INVALID
        return
    assert math.isfinite(value)
    assert spec.minimum <= value <= spec.maximum


# --- Bitmasken -------------------------------------------------------------------


def test_parse_supported_datasheet_example() -> None:
    # ELM327-Datenblatt: 41 00 BE 1F A8 13
    assert parse_supported(0x00, bytes.fromhex("BE1FA813")) == {
        0x01,
        0x03,
        0x04,
        0x05,
        0x06,
        0x07,
        0x0C,
        0x0D,
        0x0E,
        0x0F,
        0x10,
        0x11,
        0x13,
        0x15,
        0x1C,
        0x1F,
        0x20,
    }


def test_parse_supported_offsets_by_base() -> None:
    assert parse_supported(0x20, bytes.fromhex("80000001")) == {0x21, 0x40}
    assert parse_supported(0xA0, bytes.fromhex("04000000")) == {0xA6}
    assert parse_supported(0x40, bytes(4)) == set()
    assert parse_supported(0x00, b"\xff" * 4) == set(range(0x01, 0x21))


@pytest.mark.parametrize(("base", "data"), [(0x10, bytes(4)), (0x100, bytes(4)), (0, bytes(3))])
def test_parse_supported_rejects(base: int, data: bytes) -> None:
    with pytest.raises(ValueError):
        parse_supported(base, data)


def _elm(responses: dict[str, str]) -> tuple[Elm327, FakeTransport]:
    transport = FakeTransport(responses)
    return Elm327(transport), transport


def test_supported_single_block() -> None:
    elm, transport = _elm({"0100": "4100BE1FA812"})  # Bit für 0x20 nicht gesetzt
    assert read_supported_pids(elm) == parse_supported(0, bytes.fromhex("BE1FA812"))
    assert transport.sent == ["0100"]


def test_supported_follows_chain_to_odometer() -> None:
    elm, transport = _elm(
        {
            "0100": "SEARCHING...\r4100BE1FA813",
            "0120": "41 20 80 00 00 01",
            "0140": "4140 40000001",
            "0160": "41600000 0001",
            "0180": "418000000001",
            "01A0": "41A004000000",
        }
    )
    supported = read_supported_pids(elm)
    assert transport.sent == ["0100", "0120", "0140", "0160", "0180", "01A0"]
    assert {0x0C, 0x21, 0x42, 0xA6} <= supported
    assert {0x20, 0x40, 0x60, 0x80, 0xA0} <= supported


def test_supported_chain_stops_at_c0() -> None:
    elm, transport = _elm({f"01{b:02X}": f"41{b:02X}FFFFFFFF" for b in range(0, 0x100, 0x20)})
    supported = read_supported_pids(elm)
    assert transport.sent == ["0100", "0120", "0140", "0160", "0180", "01A0", "01C0"]
    assert supported == set(range(0x01, 0xE1))


def test_supported_union_of_several_ecus() -> None:
    # Motor (nur 0C, Kette weiter) und Getriebe (nur 0D); in 0120 nur das Getriebe.
    elm, transport = _elm(
        {
            "0100": "410000100001\r410000080000",
            "0120": "41200000 0000\r4120 80000000",
        }
    )
    assert read_supported_pids(elm) == {0x0C, 0x0D, 0x20, 0x21}
    assert transport.sent == ["0100", "0120"]


def test_supported_chain_needs_bit_from_any_ecu() -> None:
    elm, transport = _elm({"0100": "410000100000\r410000080001", "0120": "412080000000"})
    assert read_supported_pids(elm) == {0x0C, 0x0D, 0x20, 0x21}
    assert transport.sent == ["0100", "0120"]


def test_supported_no_data() -> None:
    elm, transport = _elm({"0100": "NO DATA"})
    assert read_supported_pids(elm) == set()
    assert transport.sent == ["0100"]


def test_supported_no_data_ends_chain() -> None:
    elm, transport = _elm({"0100": "410000100001", "0120": "NO DATA"})
    assert read_supported_pids(elm) == {0x0C, 0x20}
    assert transport.sent == ["0100", "0120"]


@pytest.mark.parametrize(
    "noise",
    [
        "7F0112",  # Ablehnung
        "4100BE1F",  # zu kurz
        "4120FFFFFFFF",  # falscher Block
        "410C1AF8",  # andere PID
        "OK",  # keine Hex-Daten
        "41",
    ],
)
def test_supported_ignores_invalid_answers(noise: str) -> None:
    elm, transport = _elm({"0100": f"{noise}\r410000100000"})
    assert read_supported_pids(elm) == {0x0C}
    assert transport.sent == ["0100"]


def test_supported_adapter_error_goes_to_caller() -> None:
    elm, _ = _elm({"0100": "CAN ERROR"})
    with pytest.raises(ElmError):
        read_supported_pids(elm)


# --- Werte lesen -----------------------------------------------------------------


def test_read_value() -> None:
    elm, transport = _elm({"010C": "410C1AF8"})
    assert read_value(elm, pid_by_key("rpm")) == 1726.0
    assert transport.sent == ["010C"]


def test_read_value_odometer_four_bytes() -> None:
    elm, _ = _elm({"01A6": "41 A6 00 1E 84 81"})
    assert read_value(elm, pid_by_key("odometer")) == pytest.approx(200000.1)


def test_read_value_ignores_extra_bytes() -> None:
    elm, _ = _elm({"010D": "410D3200000000"})  # aufgefüllt
    assert read_value(elm, pid_by_key("speed")) == 50


@pytest.mark.parametrize(
    "response",
    ["NO DATA", "7F0112", "7F 01 31", "410C1A", "410C", "410D1AF8", "OK"],
)
def test_read_value_none(response: str) -> None:
    elm, transport = _elm({"010C": response})
    assert read_value(elm, pid_by_key("rpm")) is None
    assert transport.sent == ["010C"]


def test_read_value_first_valid_ecu() -> None:
    elm, _ = _elm({"010C": "410C0BB8\r410C1AF8"})
    assert read_value(elm, pid_by_key("rpm")) == 750.0


def test_read_value_skips_rejecting_or_short_ecu() -> None:
    elm, _ = _elm({"0105": "7F0112\r4105\r41057B"})
    assert read_value(elm, pid_by_key("coolant_temp")) == 83


def test_read_values_asks_once_for_all_values_of_a_pid() -> None:
    elm, transport = _elm({"0114": "41145A80"})
    specs = [pid_by_key("o2_s1_voltage"), pid_by_key("o2_s1_trim")]
    assert read_values(elm, specs) == {"o2_s1_voltage": 0.45, "o2_s1_trim": 0.0}
    assert transport.sent == ["0114"]


def test_read_values_short_answer_gives_only_what_fits() -> None:
    # Ein Steuergerät liefert nur Byte A: Spannung ja, Trimm nicht
    elm, _ = _elm({"0114": "41145A"})
    specs = [pid_by_key("o2_s1_voltage"), pid_by_key("o2_s1_trim")]
    assert read_values(elm, specs) == {"o2_s1_voltage": 0.45, "o2_s1_trim": None}


def test_read_values_takes_each_value_from_the_first_ecu_that_has_it() -> None:
    # Steuergerät 1 hat nur Sensor 1, Steuergerät 2 beide
    elm, _ = _elm({"0167": "41 67 01 7B 00\r41 67 03 50 64"})
    specs = [pid_by_key("coolant_temp_1"), pid_by_key("coolant_temp_2")]
    assert read_values(elm, specs) == {"coolant_temp_1": 83, "coolant_temp_2": 60}


def test_read_values_status_without_sensor_is_none() -> None:
    elm, _ = _elm({"0166": "4166010020FFFF"})
    specs = [pid_by_key("maf_a"), pid_by_key("maf_b")]
    assert read_values(elm, specs) == {"maf_a": 1.0, "maf_b": None}


@pytest.mark.parametrize("keys", [["rpm", "speed"], []])
def test_read_values_needs_exactly_one_pid(keys: list[str]) -> None:
    elm, transport = _elm({})
    with pytest.raises(ValueError, match="genau einer PID"):
        read_values(elm, [pid_by_key(k) for k in keys])
    assert transport.sent == []


def test_read_value_adapter_error_goes_to_caller() -> None:
    elm, _ = _elm({"010C": "410C1AF8\rSTOPPED"})
    with pytest.raises(ElmError):
        read_value(elm, pid_by_key("rpm"))
