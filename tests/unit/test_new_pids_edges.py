"""PIDs 68, 6C, 6D, 70 und 85 (``protocol.pids``): Randfälle nach SAE J1979.

Aufbau laut J1979 (Service $01, Anhang B), wie im Modul-Docstring von ``protocol.pids``:

- 68: A = Sensoren vorhanden (Bit 0-5: Bank 1 Sensor 1-3, Bank 2 Sensor 1-3), B-G je
  X - 40 °C.
- 6C: A = Bit 0-3, B-E je 100*X/255 % (Sollwert A, Stellung A, Sollwert B, Stellung B).
- 6D: A = Bit 0-5, B-C/D-E Soll-/Istdruck A ((256X+Y)*10 kPa), F Temperatur A, G-K
  dasselbe für B.
- 70: A = Bit 0/1 Soll-/Istladedruck A, Bit 3/4 B (Bit 2/5: Regelstatus), B-I je
  (256X+Y)/32 kPa, J Regelstatus.
- 85: A = Bit 0-3, B-C/D-E (256X+Y)/200 L/h, F 100*X/255 %, G-J Sekunden (4 Bytes).

Am Fahrzeug sind diese PIDs noch nicht geprüft; die Antworten hier sind konstruiert.
"""

from collections.abc import Callable

import pytest
from hypothesis import given
from hypothesis import strategies as st

from obd_diag.i18n import set_language
from obd_diag.protocol.elm327 import Elm327, ElmError
from obd_diag.protocol.pids import PIDS, PidSpec, pid_by_key, read_values
from obd_diag.transport import TransportError
from tests.fakes import FakeTransport, can_lines

_SENSORS = ("b1s1", "b1s2", "b1s3", "b2s1", "b2s2", "b2s3")
KEYS = {
    0x68: [f"intake_temp_{s}" for s in _SENSORS],
    0x6C: [
        "commanded_throttle_a",
        "relative_throttle_a",
        "commanded_throttle_b",
        "relative_throttle_b",
    ],
    0x6D: [
        "commanded_fuel_rail_pressure_a",
        "fuel_rail_pressure_a",
        "fuel_rail_temp_a",
        "commanded_fuel_rail_pressure_b",
        "fuel_rail_pressure_b",
        "fuel_rail_temp_b",
    ],
    0x70: ["commanded_boost_a", "boost_a", "commanded_boost_b", "boost_b"],
    0x85: ["reagent_consumption", "reagent_demand", "reagent_level", "nox_warning_time"],
}


def _specs(pid: int) -> list[PidSpec]:
    return [pid_by_key(k) for k in KEYS[pid]]


def _read(pid: int, responses: dict[str, str]) -> tuple[dict[str, float | None], FakeTransport]:
    transport = FakeTransport(responses)
    return read_values(Elm327(transport), _specs(pid)), transport


def _word(data: bytes, offset: int) -> int:
    return data[offset] * 256 + data[offset + 1]


def _temperature_at(index: int) -> Callable[[bytes], float]:
    return lambda d: d[index] - 40


# Erwartung je Wert unabhängig vom Produktcode nach J1979: (Statusbit, Formel auf A..)
EXPECTED: dict[str, tuple[int, Callable[[bytes], float]]] = {
    **{f"intake_temp_{s}": (bit, _temperature_at(bit + 1)) for bit, s in enumerate(_SENSORS)},
    "commanded_throttle_a": (0, lambda d: d[1] * 100 / 255),
    "relative_throttle_a": (1, lambda d: d[2] * 100 / 255),
    "commanded_throttle_b": (2, lambda d: d[3] * 100 / 255),
    "relative_throttle_b": (3, lambda d: d[4] * 100 / 255),
    "commanded_fuel_rail_pressure_a": (0, lambda d: _word(d, 1) * 10),
    "fuel_rail_pressure_a": (1, lambda d: _word(d, 3) * 10),
    "fuel_rail_temp_a": (2, lambda d: d[5] - 40),
    "commanded_fuel_rail_pressure_b": (3, lambda d: _word(d, 6) * 10),
    "fuel_rail_pressure_b": (4, lambda d: _word(d, 8) * 10),
    "fuel_rail_temp_b": (5, lambda d: d[10] - 40),
    "commanded_boost_a": (0, lambda d: _word(d, 1) / 32),
    "boost_a": (1, lambda d: _word(d, 3) / 32),
    "commanded_boost_b": (3, lambda d: _word(d, 5) / 32),
    "boost_b": (4, lambda d: _word(d, 7) / 32),
    "reagent_consumption": (0, lambda d: _word(d, 1) / 200),
    "reagent_demand": (1, lambda d: _word(d, 3) / 200),
    "reagent_level": (2, lambda d: d[5] * 100 / 255),
    "nox_warning_time": (3, lambda d: int.from_bytes(d[6:10], "big")),
}

# Datenbytes (A bis zum letzten Byte) der langen Form je PID
_LENGTH = {0x68: 7, 0x6C: 5, 0x6D: 11, 0x70: 10, 0x85: 10}


def test_expected_table_covers_all_new_keys() -> None:
    assert set(EXPECTED) == {k for keys in KEYS.values() for k in keys}
    for pid, keys in KEYS.items():
        assert [s.key for s in PIDS.values() if s.pid == pid] == keys
        # der letzte Wert endet mit der langen Form; bei 70 folgt nur noch Byte J
        # (Regelstatus, kein Messwert)
        last = 9 if pid == 0x70 else _LENGTH[pid]
        assert max(pid_by_key(k).size for k in keys) == last


@pytest.mark.parametrize("pid", sorted(KEYS))
@given(data=st.data())
def test_decode_matches_j1979_for_every_status_byte(pid: int, data: st.DataObject) -> None:
    # Jedes Statusbyte (auch reservierte Bits 6/7) und beliebige Datenbytes
    raw = data.draw(st.binary(min_size=_LENGTH[pid], max_size=_LENGTH[pid]))
    for key in KEYS[pid]:
        spec = pid_by_key(key)
        bit, formula = EXPECTED[key]
        value = spec.decode(raw[: spec.size])
        if raw[0] & (1 << bit):
            assert value is not None
            assert value == pytest.approx(formula(raw))
            assert spec.minimum <= value <= spec.maximum
        else:
            assert value is None


# --- 68: Ansauglufttemperatur ---


def test_68_long_form_all_six_sensors() -> None:
    values, transport = _read(0x68, {"0168": "41 68 3F 3C 46 50 5A 64 6E"})
    assert values == dict(zip(KEYS[0x68], [20, 30, 40, 50, 60, 70], strict=True))
    assert transport.sent == ["0168"]  # eine Anfrage für alle sechs Werte


def test_68_short_form_two_sensors() -> None:
    # Ältere Form: nur Bit 0/1 und Bytes B, C; Sensoren dahinter fehlen
    values, _ = _read(0x68, {"0168": "4168033C46"})
    assert values == {**dict.fromkeys(KEYS[0x68]), "intake_temp_b1s1": 20, "intake_temp_b1s2": 30}


def test_68_bit_set_but_byte_missing_is_none() -> None:
    # Statusbyte meldet alle sechs Sensoren, die Antwort ist nach Sensor 3 abgeschnitten
    values, _ = _read(0x68, {"0168": "41683F000000"})
    assert values == {
        "intake_temp_b1s1": -40,
        "intake_temp_b1s2": -40,
        "intake_temp_b1s3": -40,
        "intake_temp_b2s1": None,
        "intake_temp_b2s2": None,
        "intake_temp_b2s3": None,
    }


def test_68_only_second_sensor_ignores_byte_b() -> None:
    values, _ = _read(0x68, {"0168": "4168 02 FF 46"})
    assert values["intake_temp_b1s1"] is None  # Byte B ist FF, aber Bit 0 fehlt
    assert values["intake_temp_b1s2"] == 30


def test_68_reserved_bits_do_not_count() -> None:
    # Bit 6/7 sind reserviert: kein siebter Sensor, kein Einfluss auf die übrigen
    values, _ = _read(0x68, {"0168": "4168C0FFFFFFFFFFFF"})
    assert values == dict.fromkeys(KEYS[0x68])
    values, _ = _read(0x68, {"0168": "4168C1FF"})
    assert values["intake_temp_b1s1"] == 215


def test_68_first_valid_ecu_per_sensor() -> None:
    # Steuergerät 1 nur Sensor 1 (kurz), Steuergerät 2 Sensor 1 und 2
    values, _ = _read(0x68, {"0168": "41 68 01 3C\r41 68 03 00 50"})
    assert values["intake_temp_b1s1"] == 20  # erstes Steuergerät
    assert values["intake_temp_b1s2"] == 40  # nur das zweite hat ihn


def test_68_ecu_without_sensor_bit_does_not_hide_later_ecu() -> None:
    values, _ = _read(0x68, {"0168": "41 68 00 3C\r41 68 01 50"})
    assert values["intake_temp_b1s1"] == 40


@pytest.mark.parametrize(
    "answer",
    ["NO DATA", "7F 01 12", "41 68", "41 69 01 3C", "41 0F 3C", "41 68 01\r7F 01 31"],
)
def test_68_unusable_answers_give_none(answer: str) -> None:
    values, transport = _read(0x68, {"0168": answer})
    assert values == dict.fromkeys(KEYS[0x68])
    assert transport.sent == ["0168"]


# --- 6C: Drosselklappensteller ---


def test_6c_all_four_values() -> None:
    values, _ = _read(0x6C, {"016C": "416C0F336699CC"})
    assert values == {
        "commanded_throttle_a": 20,
        "relative_throttle_a": 40,
        "commanded_throttle_b": 60,
        "relative_throttle_b": 80,
    }


def test_6c_only_a_side_short_answer() -> None:
    # Fahrzeug mit einer Drosselklappe: Bit 0/1, nur B und C
    values, _ = _read(0x6C, {"016C": "416C0333FF"})
    assert values == {
        "commanded_throttle_a": 20,
        "relative_throttle_a": 100,
        "commanded_throttle_b": None,
        "relative_throttle_b": None,
    }


@pytest.mark.parametrize(("byte", "percent"), [("00", 0.0), ("80", 128 * 100 / 255), ("FF", 100)])
def test_6c_percent_boundaries(byte: str, percent: float) -> None:
    values, _ = _read(0x6C, {"016C": f"416C08000000{byte}"})
    assert values["relative_throttle_b"] == pytest.approx(percent)
    assert values["commanded_throttle_a"] is None


# --- 6D: Kraftstoffdruckregelung ---

# 13 Bytes (41 6D + A-K) sind mehrteilig: erster Frame 6, Folge-Frame 7 Bytes
_6D_FULL = "00D\r0:416D3F0AF00A\r1:EB461388138750"


def test_6d_both_rails_from_one_multiframe_answer() -> None:
    values, transport = _read(0x6D, {"016D": _6D_FULL})
    assert values == {
        "commanded_fuel_rail_pressure_a": 28000,
        "fuel_rail_pressure_a": 27950,
        "fuel_rail_temp_a": 30,
        "commanded_fuel_rail_pressure_b": 50000,
        "fuel_rail_pressure_b": 49990,
        "fuel_rail_temp_b": 40,
    }
    assert transport.sent == ["016D"]


def test_6d_status_bits_select_single_values() -> None:
    # Nur Istdruck A und Temperatur B gemeldet (Bit 1 und 5), alle Bytes vorhanden
    values, _ = _read(0x6D, {"016D": "00D\r0:416D220AF00A\r1:EB461388138750"})
    assert values == {
        **dict.fromkeys(KEYS[0x6D]),
        "fuel_rail_pressure_a": 27950,
        "fuel_rail_temp_b": 40,
    }


def test_6d_two_ecus_with_headers_each_one_rail() -> None:
    # Zwei Steuergeräte, je ein Rail, mehrteilig: ohne Header vermischt, mit Headern
    # nach Adresse sortiert zusammengesetzt (wie beim Scan)
    rail_a = "416D070AF00AEB460000000000"  # A-Seite: Bit 0-2
    rail_b = "416D3800000000001388138750"  # B-Seite: Bit 3-5
    mixed = (
        "00D\r0:" + rail_a[:12] + "\r00D\r0:" + rail_b[:12]
        + "\r1:" + rail_a[12:] + "\r1:" + rail_b[12:]
    )  # fmt: skip
    headers = can_lines("7E9", rail_b) + "\r" + can_lines("7E8", rail_a)
    transport = FakeTransport({"016D": mixed}, headers_on={"016D": headers})
    values = read_values(Elm327(transport), _specs(0x6D))
    assert values == {
        "commanded_fuel_rail_pressure_a": 28000,
        "fuel_rail_pressure_a": 27950,
        "fuel_rail_temp_a": 30,
        "commanded_fuel_rail_pressure_b": 50000,
        "fuel_rail_pressure_b": 49990,
        "fuel_rail_temp_b": 40,
    }
    assert transport.sent == ["016D", "ATH1", "016D", "ATH0"]


def test_6d_single_frame_gives_only_rail_a_pressure() -> None:
    # Nur sieben Bytes (Einzel-Frame): A, Solldruck A, Istdruck A; Temperatur fehlt
    values, _ = _read(0x6D, {"016D": "416D3F0AF00AEB"})
    assert values == {
        **dict.fromkeys(KEYS[0x6D]),
        "commanded_fuel_rail_pressure_a": 28000,
        "fuel_rail_pressure_a": 27950,
    }


def test_6d_adapter_error_goes_to_caller() -> None:
    transport = FakeTransport({"016D": "CAN ERROR"})
    with pytest.raises(ElmError):
        read_values(Elm327(transport), _specs(0x6D))


class _BrokenTransport(FakeTransport):
    """Adapter, der beim Senden von ``broken`` abgezogen wird."""

    def __init__(self, responses: dict[str, str], broken: str) -> None:
        super().__init__(responses)
        self.broken = broken

    def write(self, data: bytes) -> None:
        if data.decode("ascii").strip() == self.broken:
            raise TransportError("Verbindung unterbrochen")
        super().write(data)


def test_6d_transport_error_goes_to_caller() -> None:
    with pytest.raises(TransportError):
        read_values(Elm327(_BrokenTransport({}, "016D")), _specs(0x6D))


# --- 70: Ladedruckregelung ---

# 41 70 + A-J (12 Bytes): Soll/Ist A 101/100 kPa, Soll/Ist B 200/199 kPa, J beliebig
_70_FULL = "00C\r0:41701B0CA00C\r1:80190018E0AA00"


def test_70_all_four_pressures() -> None:
    values, transport = _read(0x70, {"0170": _70_FULL})
    assert values == {
        "commanded_boost_a": 101,
        "boost_a": 100,
        "commanded_boost_b": 200,
        "boost_b": 199,
    }
    assert transport.sent == ["0170"]


def test_70_control_status_bits_alone_give_nothing() -> None:
    # Bit 2 und 5 melden nur den Regelstatus (Byte J), keinen Druck
    values, _ = _read(0x70, {"0170": "00C\r0:4170240CA00C\r1:80190018E0AA00"})
    assert values == dict.fromkeys(KEYS[0x70])


def test_70_reserved_bits_ignored_all_set() -> None:
    values, _ = _read(0x70, {"0170": "00C\r0:4170FF0CA00C\r1:80190018E0AA00"})
    assert values == {
        "commanded_boost_a": 101,
        "boost_a": 100,
        "commanded_boost_b": 200,
        "boost_b": 199,
    }


def test_70_missing_status_byte_j_does_not_matter() -> None:
    # Ohne Byte J (9 Datenbytes) sind alle vier Drücke trotzdem vollständig
    values, _ = _read(0x70, {"0170": "00B\r0:41701B0CA00C\r1:80190018E00000"})
    assert values["boost_b"] == 199


def test_70_boost_resolution_and_maximum() -> None:
    values, _ = _read(0x70, {"0170": "41 70 03 00 01 FF FF"})
    assert values["commanded_boost_a"] == 1 / 32  # kleinste Stufe 0,03125 kPa
    assert values["boost_a"] == 65535 / 32 == pid_by_key("boost_a").maximum


# --- 85: Reduktionsmittel ---


def test_85_full_answer_from_one_ecu() -> None:
    # 12 Bytes, mehrteilig: 1,0 L/h, 0,5 L/h, 70,2 %, 3600 s
    values, _ = _read(0x85, {"0185": "00C\r0:41850F00C800\r1:64B300000E1000"})
    assert values == {
        "reagent_consumption": 1.0,
        "reagent_demand": 0.5,
        "reagent_level": pytest.approx(179 * 100 / 255),
        "nox_warning_time": 3600,
    }


def test_85_warning_time_uses_all_four_bytes() -> None:
    values, _ = _read(0x85, {"0185": "00C\r0:418508000000\r1:000001020304AA"})
    assert values["nox_warning_time"] == 0x01020304
    values, _ = _read(0x85, {"0185": "00C\r0:418508000000\r1:0000FFFFFFFF00"})
    assert values["nox_warning_time"] == 0xFFFFFFFF


def test_85_warning_time_short_answer_is_none() -> None:
    # Bit 3 gesetzt, aber nur drei der vier Zeitbytes: kein Wert statt eines falschen
    values, _ = _read(0x85, {"0185": "00B\r0:41850C000000\r1:00FF000E100000"})
    assert values["nox_warning_time"] is None
    assert values["reagent_level"] == 100


def test_85_level_without_consumption() -> None:
    values, _ = _read(0x85, {"0185": "41 85 04 FF FF FF FF 80"})
    assert values == {**dict.fromkeys(KEYS[0x85]), "reagent_level": pytest.approx(128 * 100 / 255)}


# --- Bezeichnungen ---


@pytest.mark.parametrize(
    ("key", "german", "english"),
    [
        (
            "intake_temp_b2s3",
            "Ansauglufttemperatur Bank 2, Sensor 3",
            "Intake air temperature bank 2, sensor 3",
        ),
        ("boost_a", "Ladedruck A", "Boost pressure A"),
        ("commanded_boost_a", "Ladedruck A Sollwert", "Commanded boost pressure A"),
        ("nox_warning_time", "Laufzeit mit NOx-Warnung", "Run time with NOx warning"),
    ],
)
def test_names_in_both_languages(key: str, german: str, english: str) -> None:
    assert pid_by_key(key).name == german
    set_language("en")
    assert pid_by_key(key).name == english


@pytest.mark.parametrize(
    ("key", "unit", "maximum"),
    [
        ("intake_temp_b1s1", "°C", 215),
        ("relative_throttle_b", "%", 100),
        ("fuel_rail_pressure_b", "kPa", 655350),
        ("fuel_rail_temp_b", "°C", 215),
        ("boost_b", "kPa", 2047.96875),
        ("reagent_demand", "L/h", 327.675),
        ("reagent_level", "%", 100),
        ("nox_warning_time", "s", 4294967295),
    ],
)
def test_units_and_ranges(key: str, unit: str, maximum: float) -> None:
    spec = pid_by_key(key)
    assert spec.unit == unit
    assert spec.maximum == pytest.approx(maximum)
