import io
import json
from datetime import date
from pathlib import Path
from typing import Any
from urllib.error import URLError

import pytest

from obd_diag.data import wmi
from obd_diag.protocol.elm327 import Elm327, ElmError
from obd_diag.services import vehicle
from obd_diag.services.vehicle import (
    VinInfo,
    check_digit,
    decode_vin,
    default_vpic_cache_dir,
    lookup_vpic,
    model_year,
    model_year_text,
    parse_vin_response,
    read_vin,
)
from tests.fakes import FakeTransport
from tests.samples import VEHICLE

# --- FIN lesen ---


def _read(response: str) -> str | None:
    return read_vin(Elm327(FakeTransport({"0902": response})))


def test_read_can_multiframe() -> None:
    # Datenblatt ELM327DS S. 44
    raw = "014\r0: 49 02 01 31 44 34\r1: 47 50 30 30 52 35 35\r2: 42 31 32 33 34 35 36"
    assert _read(raw) == "1D4GP00R55B123456"


def test_read_can_without_spaces_like_emulator() -> None:
    raw = "014\r0:490201575030\r1:5A5A5A39395A54\r2:53333930303030"
    assert _read(raw) == "WP0ZZZ99ZTS390000"


def test_read_can_padded_with_nul() -> None:
    # 3 Füllbytes 00 vor der FIN (manche Steuergeräte füllen auf 20 Zeichen auf)
    raw = "017\r0:49020100000057\r1:56575A5A5A314B\r2:5A365731323334\r3:35360000000000"
    assert _read(raw) == "WVWZZZ1KZ6W123456"


def test_read_can_without_count_byte() -> None:
    payload = b"\x49\x02" + b"WVWZZZ1KZ6W123456"
    hexed = payload.hex().upper()
    raw = f"013\r0:{hexed[:12]}\r1:{hexed[12:26]}\r2:{hexed[26:]}000000"
    assert _read(raw) == "WVWZZZ1KZ6W123456"


def test_read_legacy_lines() -> None:
    # Datenblatt ELM327DS S. 44 (J1850): Zeilenzähler im dritten Byte
    raw = (
        "49 02 01 00 00 00 31\r49 02 02 44 34 47 50\r49 02 03 30 30 52 35\r"
        "49 02 04 35 42 31 32\r49 02 05 33 34 35 36"
    )
    assert _read(raw) == "1D4GP00R55B123456"


def test_read_legacy_out_of_order_and_duplicated() -> None:
    lines = [
        "49020100000031",
        "49020244344750",
        "49020330305235",
        "49020435423132",
        "49020533343536",
    ]
    shuffled = [lines[2], lines[0], lines[4], lines[1], lines[3]]
    assert _read("\r".join(shuffled + lines)) == "1D4GP00R55B123456"  # zwei Steuergeräte


def test_read_legacy_conflicting_lines() -> None:
    raw = "49020100000031\r49020244344750\r49020244344751"
    assert _read(raw) is None


def test_read_legacy_missing_line() -> None:
    raw = "49020100000031\r49020244344750\r49020435423132\r49020533343536"
    assert _read(raw) is None


@pytest.mark.parametrize(
    "response",
    [
        "490201",  # nur Zählbyte
        "7F0912",  # abgelehnt
        "4100BE3FA813",  # Antwort auf etwas anderes
        # nur Füllbytes
        "014\r0:49020100000000\r1:00000000000000\r2:00000000000000",
        # 16 Zeichen
        "013\r0:49020131443447\r1:50303052353542\r2:31323334350000",
        # Steuerzeichen statt Buchstaben
        "014\r0:49020131443407\r1:47503030523535\r2:42313233343536",
    ],
)
def test_read_garbage_gives_none(response: str) -> None:
    assert _read(response) is None


def test_read_no_data() -> None:
    assert _read("NO DATA") is None


def test_read_unparseable_raises_elm_error() -> None:
    with pytest.raises(ElmError, match="0902"):
        _read("OK")
    with pytest.raises(ElmError):  # Frame 1 fehlt
        _read("014\r0:490201314434\r2:42313233343536")


def test_two_ecus_with_different_vins_takes_first() -> None:
    first = "014\r0:490201575030\r1:5A5A5A39395A54\r2:53333930303030"
    second = "014\r0:4902014D4154\r1:34303330393642\r2:4E4C3030303030"
    assert parse_vin_response(f"{first}\r{second}") == "WP0ZZZ99ZTS390000"


def test_two_ecus_mixed_vin_reread_with_headers() -> None:
    # Ohne Header: zweite Längenzeile, bevor die erste Nachricht vollständig ist
    mixed = (
        "014\r0:4902014D4154\r014\r0:490201575030\r1:34303330393642\r1:5A5A5A39395A54"
        "\r2:4E4C3030303030\r2:53333930303030"
    )
    with_headers = (
        "7E9 10 14 49 02 01 4D 41 54\r7E8 10 14 49 02 01 57 50 30\r"
        "7E9 21 34 30 33 30 39 36 42\r7E8 21 5A 5A 5A 39 39 5A 54\r"
        "7E9 22 4E 4C 30 30 30 30 30\r7E8 22 53 33 39 30 30 30 30"
    )
    transport = FakeTransport({"0902": mixed}, headers_on={"0902": with_headers})
    # 7E8 (Motorsteuergerät) zuerst, obwohl 7E9 zuerst eintraf
    assert read_vin(Elm327(transport)) == "WP0ZZZ99ZTS390000"
    assert transport.sent == ["0902", "ATH1", "0902", "ATH0"]


def test_lowercase_ascii_is_uppercased() -> None:
    payload = b"\x49\x02\x01" + b"wvwzzz1kz6w123456"
    hexed = payload.hex()
    raw = f"014\r0:{hexed[:12]}\r1:{hexed[12:26]}\r2:{hexed[26:]}"
    assert parse_vin_response(raw) == "WVWZZZ1KZ6W123456"


# --- Prüfziffer ---


@pytest.mark.parametrize(
    "vin",
    [
        "1M8GDM9AXKP042788",  # Beispiel aus 49 CFR 565 / Wikipedia, Prüfziffer X
        "1HGCM82633A004352",  # Honda Accord, oft zitiertes Beispiel
        "11111111111111111",
    ],
)
def test_north_american_checksum_ok(vin: str) -> None:
    info = decode_vin(vin)
    assert info.valid
    assert info.checksum_ok is True
    assert check_digit(vin) == vin[8]


def test_north_american_checksum_wrong() -> None:
    assert decode_vin("1M8GDM9A1KP042788").checksum_ok is False


@pytest.mark.parametrize(
    "vin",
    [
        "WVWZZZ1KZ6W123456",  # Europa, Z an Stelle 9: keine Prüfziffer
        "WP0ZZZ99ZTS390000",
        "WBA3A5C51CF256551",  # BMW: Stelle 9 stimmt hier nicht, ist aber nicht Pflicht
    ],
)
def test_european_checksum_not_mandatory(vin: str) -> None:
    assert decode_vin(vin).checksum_ok is None


def test_european_checksum_reported_when_it_matches() -> None:
    # Toyota Großbritannien (Emulator-FIN): Stelle 9 ist eine korrekte Prüfziffer
    assert decode_vin("SB1ZS3JE60E282102").checksum_ok is True


def test_china_checksum_mandatory() -> None:
    vin = "LSVAB2BM8EN123456"
    good = vin[:8] + check_digit(vin) + vin[9:]
    assert decode_vin(good).checksum_ok is True
    bad = vin[:8] + ("1" if good[8] != "1" else "2") + vin[9:]
    assert decode_vin(bad).checksum_ok is False


# --- Gültigkeit ---


@pytest.mark.parametrize(
    "vin",
    [
        "",
        "WVWZZZ1KZ6W12345",
        "WVWZZZ1KZ6W1234567",
        "WVWZZZ1KZ6W12345O",
        "IVWZZZ1KZ6W123456",
        "WVWZZZ1KZ6Q123456",
        "WVW ZZZ1KZ6W12345",
        "WVWZZZ1KZ6W12345Ä",
    ],
)
def test_invalid(vin: str) -> None:
    info = decode_vin(vin)
    assert not info.valid
    assert info.checksum_ok is None
    assert info.model_year is None


def test_decode_normalises_case_and_whitespace() -> None:
    assert decode_vin(" wvwzzz1kz6w123456\n").vin == "WVWZZZ1KZ6W123456"


def test_decode_sample_vehicle() -> None:
    expected = VinInfo(
        vin=VEHICLE.vin,
        valid=VEHICLE.valid,
        checksum_ok=VEHICLE.checksum_ok,
        wmi=VEHICLE.wmi,
        manufacturer=VEHICLE.manufacturer,
        country=VEHICLE.country,
        model_year=VEHICLE.model_year,
    )
    assert decode_vin(VEHICLE.vin) == expected


# --- WMI, Land ---


@pytest.mark.parametrize(
    ("vin", "manufacturer", "country"),
    [
        ("WVWZZZ1KZ6W123456", "Volkswagen", "Deutschland"),
        ("WAUZZZ8V5JA000000", "Audi", "Deutschland"),
        ("WBA3A5C51CF256551", "BMW", "Deutschland"),
        ("WDD2050071R000000", "Mercedes-Benz", "Deutschland"),
        ("TMBJJ7NE0F0000000", "Škoda", "Tschechien"),
        ("VF1RFB00X56000000", "Renault", "Frankreich"),
        ("VSSZZZ5FZJR000000", "SEAT", "Spanien"),
        ("ZFA31200000000000", "Fiat", "Italien"),
        ("YV1MW84000F000000", "Volvo", "Schweden"),
        ("SAJAA06E0EP000000", "Jaguar", "Vereinigtes Königreich"),
        ("JTDKB20U000000000", "Toyota", "Japan"),
        ("KMHD841CAJU000000", "Hyundai", "Südkorea"),
        ("LRW3E7EK0MC000000", "Tesla (Shanghai)", "China"),
        ("MAT403096BNL00000", "Tata Motors", "Indien"),
        ("5YJ3E1EA0KF000000", "Tesla", "USA"),
        ("7SAYGDEE0NF000000", "Tesla", "USA"),
        ("2T1BURHE0JC000000", "Toyota Kanada", "Kanada"),
        ("3VWFE21C04M000000", "Volkswagen Mexiko", "Mexiko"),
        ("9BWZZZ377VT000000", "Volkswagen Brasilien", "Brasilien"),
    ],
)
def test_wmi_lookup(vin: str, manufacturer: str, country: str) -> None:
    info = decode_vin(vin)
    assert (info.wmi, info.manufacturer, info.country) == (vin[:3], manufacturer, country)


def test_unknown_wmi_still_gives_country() -> None:
    info = decode_vin("WZZ12345678901234")
    assert info.manufacturer is None
    assert info.country == "Deutschland"


@pytest.mark.parametrize(
    ("prefix", "country"),
    [
        ("SN", "Deutschland"),
        ("SU", "Polen"),
        ("TJ", "Tschechien"),
        ("TR", "Ungarn"),
        ("UU", "Rumänien"),
        ("U5", "Slowakei"),
        ("VA", "Österreich"),
        ("XL", "Niederlande"),
        ("XT", "Russland"),
        ("YA", "Belgien"),
        ("YS", "Schweden"),
        ("ZA", "Italien"),
        ("NM", "Türkei"),
        ("6Y", "Neuseeland"),
        ("61", "Neuseeland"),
        ("6A", "Australien"),
        ("7A", "USA"),
        ("VX", "Frankreich"),  # z. B. VXK Opel seit PSA
        ("XA", "Bulgarien"),
        ("XF", "Griechenland"),
        ("UA", "Spanien"),
        ("T7", "Niederlande"),
        ("EA", "Russland"),
        ("KS", "Jordanien"),
        ("ZS", "Italien"),
        ("3X", "Mexiko"),
        ("84", "Costa Rica"),
        ("93", "Brasilien"),
    ],
)
def test_country_regions(prefix: str, country: str) -> None:
    assert wmi.country_for(prefix + "X") == country


@pytest.mark.parametrize("prefix", ["", "W", "IA", "ZW", "Ö1", "C1", "BW"])
def test_country_unknown(prefix: str) -> None:
    assert wmi.country_for(prefix) is None


def test_wmi_table_is_large_and_well_formed() -> None:
    assert len(wmi.MANUFACTURERS) >= 150
    for code, name in wmi.MANUFACTURERS.items():
        assert len(code) == 3 and code == code.upper()
        assert not set(code) & set("IOQ"), code
        assert name
        assert wmi.country_for(code) is not None, code


# Bekannte WMIs, geprüft 2026-10-07 gegen Wikipedia (en/de) und NHTSA vPIC DecodeWMI
PINNED_WMIS = {
    "WVW": "Volkswagen",
    "WAU": "Audi",
    "WBA": "BMW",
    "WDB": "Mercedes-Benz",
    "WDD": "Mercedes-Benz",
    "W1K": "Mercedes-Benz",
    "WP0": "Porsche",
    "W0L": "Opel",
    "VXK": "Opel (seit Übernahme durch PSA)",
    "TMB": "Škoda",
    "VSS": "SEAT",
    "VF1": "Renault",
    "VF3": "Peugeot",
    "VF7": "Citroën",
    "ZFA": "Fiat",
    "ZFF": "Ferrari",
    "SAJ": "Jaguar",
    "SAL": "Land Rover",
    "YV1": "Volvo",
    "JTD": "Toyota",
    "JHM": "Honda",
    "KMH": "Hyundai",
    "KNA": "Kia",
    "KMT": "Genesis",
    "1G1": "Chevrolet",
    "1FA": "Ford",
    "5YJ": "Tesla",
    "7SA": "Tesla",
    "LRW": "Tesla (Shanghai)",
    "WUA": "Audi Sport (quattro GmbH)",
}


@pytest.mark.parametrize(("code", "name"), PINNED_WMIS.items())
def test_pinned_wmis(code: str, name: str) -> None:
    assert wmi.manufacturer_for(code) == name


@pytest.mark.parametrize(
    "code",
    [
        "VF9",  # Kleinserienhersteller: Stelle 3 = 9, Hersteller erst aus Stelle 12-14
        "VWV",  # in keiner Quelle bestätigt
    ],
)
def test_unverified_or_shared_wmis_removed(code: str) -> None:
    assert wmi.manufacturer_for(code) is None


def test_no_small_manufacturer_wmis() -> None:
    assert not [code for code in wmi.MANUFACTURERS if code[2] == "9"]


@pytest.mark.parametrize(
    ("code", "name"),
    [
        ("ZFC", "Ram 1200 (Fiat)"),  # vorher fälschlich „Fiat (Nutzfahrzeuge)“
        ("SFD", "Dennis"),  # vorher „Alexander Dennis“
        ("WV3", "Volkswagen Nutzfahrzeuge (Fahrgestell)"),  # vPIC: Incomplete Vehicle
    ],
)
def test_corrected_wmis(code: str, name: str) -> None:
    assert wmi.manufacturer_for(code) == name


# --- Modelljahr ---


@pytest.mark.parametrize(
    ("vin", "year"),
    [
        ("1M8GDM9AXKP042788", 1989),  # Stelle 7 Ziffer: 1980-2009
        ("1HGCM82633A004352", 2003),
        ("5YJ3E1EA0KF000000", 2019),  # Stelle 7 Buchstabe: 2010-2039
        ("1FTFW1E5XPFA00000", 2023),
        ("2T1BURHE0JC000000", 2018),
    ],
)
def test_model_year_north_america(vin: str, year: int) -> None:
    assert model_year(vin, today=date(2026, 10, 7)) == year


@pytest.mark.parametrize(
    ("code", "today", "year"),
    [
        ("6", date(2026, 10, 7), 2006),  # 2036 liegt in der Zukunft
        ("A", date(2026, 10, 7), 2010),
        ("T", date(2026, 10, 7), 2026),
        ("V", date(2026, 10, 7), 2027),  # nächstes Modelljahr ist schon verkauft
        ("W", date(2026, 10, 7), 1998),
        ("Y", date(2026, 10, 7), 2000),
        ("1", date(2026, 10, 7), 2001),
        ("W", date(2027, 1, 1), 2028),
        ("A", date(2040, 1, 1), 2040),
    ],
)
def test_model_year_elsewhere_most_recent(code: str, today: date, year: int) -> None:
    vin = f"WVWZZZ1KZ{code}W123456"
    assert model_year(vin, today=today) == year


@pytest.mark.parametrize("code", ["0", "Z", "U"])
def test_model_year_unknown_code(code: str) -> None:
    assert model_year(f"WVWZZZ1KZ{code}W123456") is None


TODAY = date(2026, 10, 7)


@pytest.mark.parametrize(
    ("code", "years"),
    [
        ("T", (2026, 1996)),
        ("V", (2027, 1997)),
        ("A", (2010, 1980)),
        ("S", (2025, 1995)),
        ("W", (1998,)),  # 2028 liegt zu weit in der Zukunft
        ("6", (2006,)),  # 1976: vor den Modelljahr-Codes
        ("Y", (2000,)),
    ],
)
def test_model_years_elsewhere(code: str, years: tuple[int, ...]) -> None:
    assert vehicle.model_years(f"WVWZZZ1KZ{code}W123456", today=TODAY) == years


@pytest.mark.parametrize(
    "vin",
    ["WDD1690071J236589", "WDD1770031J000000", "W1K1770031J000000", "WDB2030461A000000"],
)
def test_mercedes_has_no_model_year(vin: str) -> None:
    # Stelle 10 ist bei Mercedes die Lenkung (1 = links); der W177 ergab sonst „2001“.
    info = vehicle.decode_vin(vin, protocol="ISO 15765-4 (CAN 29/500)", today=TODAY)
    assert info.model_year is None
    assert info.model_year_alternatives == ()
    assert model_year_text(info) is None
    assert info.manufacturer == "Mercedes-Benz"


def test_mercedes_usa_keeps_model_year() -> None:
    assert vehicle.decode_vin("4JGDA5HB0JA000000", today=TODAY).model_year == 2018


def test_model_years_north_america_unambiguous() -> None:
    assert vehicle.model_years("5YJ3E1EA0KF000000", today=TODAY) == (2019,)
    assert vehicle.model_years("1M8GDM9AXKP042788", today=TODAY) == (1989,)


@pytest.mark.parametrize(
    ("protocol", "earliest"),
    [
        (None, 1980),
        ("", 1980),
        ("ISO 15765-4 (CAN 11/500)", 2000),
        ("ISO 15765-4 (CAN 29/250)", 2000),
        ("ISO 9141-2", 1994),
        ("ISO 14230-4 (KWP FAST)", 1994),
        ("SAE J1850 PWM", 1994),
        ("SAE J1939 (CAN 29/250)", 1980),  # Nutzfahrzeuge, keine Einschränkung
        ("unbekannt", 1980),
    ],
)
def test_earliest_plausible_year(protocol: str | None, earliest: int) -> None:
    assert vehicle.earliest_plausible_year(protocol) == earliest


@pytest.mark.parametrize(
    ("vin", "protocol", "year", "alternatives"),
    [
        ("WVWZZZ1KZTW123456", None, 2026, (1996,)),
        ("WVWZZZ1KZTW123456", "ISO 9141-2", 2026, (1996,)),  # OBD-II ab ca. 1994
        ("WVWZZZ1KZTW123456", "ISO 15765-4 (CAN 11/500)", 2026, ()),  # CAN nicht 1996
        ("WVWZZZ1KZAW123456", None, 2010, (1980,)),
        ("WVWZZZ1KZAW123456", "ISO 14230-4 (KWP FAST)", 2010, ()),  # 1980 kein OBD-II
        ("WVWZZZ1KZ6W123456", None, 2006, ()),
        ("1HGCM82633A004352", None, 2003, ()),  # Nordamerika: Stelle 7 entscheidet
        ("WVWZZZ1KZ6W12345", None, None, ()),  # ungültig
    ],
)
def test_decode_vin_model_year_alternatives(
    vin: str, protocol: str | None, year: int | None, alternatives: tuple[int, ...]
) -> None:
    info = decode_vin(vin, protocol=protocol, today=TODAY)
    assert (info.model_year, info.model_year_alternatives) == (year, alternatives)


@pytest.mark.parametrize(
    ("vin", "text"),
    [
        ("WVWZZZ1KZTW123456", "2026 oder 1996 (aus Stelle 10, ohne Gewähr)"),
        ("WVWZZZ1KZ6W123456", "2006 (aus Stelle 10, ohne Gewähr)"),
        ("WVWZZZ1KZ0W123456", None),
    ],
)
def test_model_year_text(vin: str, text: str | None) -> None:
    assert vehicle.model_year_text(decode_vin(vin, today=TODAY)) == text


@pytest.mark.parametrize(
    ("vin", "checksum_ok", "text"),
    [
        ("1M8GDM9AXKP042788", True, "stimmt"),  # Nordamerika, vorgeschrieben
        ("1M8GDM9A1KP042788", False, "stimmt nicht"),
        ("WBA3A5C53CF256551", True, "stimmt"),  # Europa: nicht vorgeschrieben, stimmt aber
        ("WVWZZZ1KZ6W123456", None, "nicht vorgeschrieben (weicht ab)"),
        ("WVWZZZ1KZ6W12345", None, "nicht prüfbar (FIN ungültig)"),
    ],
)
def test_checksum_text(vin: str, checksum_ok: bool | None, text: str) -> None:
    info = decode_vin(vin)
    assert info.checksum_ok is checksum_ok
    assert vehicle.checksum_text(info) == text


# --- vPIC ---

VPIC_RESPONSE = {
    "Count": 1,
    "Message": "Results returned successfully",
    "Results": [
        {
            "Make": "HONDA",
            "Model": "Accord",
            "ModelYear": "2003",
            "BodyClass": "Coupe",
            "EngineCylinders": "4",
            "DisplacementL": "2.4",
            "FuelTypePrimary": "Gasoline",
            "PlantCountry": "UNITED STATES (USA)",
            "Trim": "",
            "EngineHP": None,
            "ErrorCode": "0",
            "Series": "EX",
        }
    ],
}
VPIC_FIELDS = {
    "Make": "HONDA",
    "Model": "Accord",
    "ModelYear": "2003",
    "BodyClass": "Coupe",
    "EngineCylinders": "4",
    "DisplacementL": "2.4",
    "FuelTypePrimary": "Gasoline",
    "PlantCountry": "UNITED STATES (USA)",
}


class _Urlopen:
    """Ersatz für ``urlopen``: zählt Aufrufe, liefert festen Text oder wirft."""

    def __init__(self, body: bytes | Exception) -> None:
        self.body = body
        self.calls: list[tuple[str, float]] = []

    def __call__(self, url: str, timeout: float) -> io.BytesIO:
        self.calls.append((url, timeout))
        if isinstance(self.body, Exception):
            raise self.body
        return io.BytesIO(self.body)


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Netzwerkzugriff im Test")

    monkeypatch.setattr(vehicle, "urlopen", refuse)


def _fake(monkeypatch: pytest.MonkeyPatch, body: bytes | Exception) -> _Urlopen:
    fake = _Urlopen(body)
    monkeypatch.setattr(vehicle, "urlopen", fake)
    return fake


def test_vpic_maps_fields_and_caches(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _fake(monkeypatch, json.dumps(VPIC_RESPONSE).encode())
    vin = "1HGCM82633A004352"
    assert lookup_vpic(vin, cache_dir=tmp_path, timeout=2.5) == VPIC_FIELDS
    assert fake.calls == [
        (f"https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues/{vin}?format=json", 2.5)
    ]
    assert json.loads((tmp_path / f"{vin}.json").read_text(encoding="utf-8")) == VPIC_RESPONSE
    # zweiter Aufruf aus dem Cache, ohne Netz
    assert lookup_vpic(vin.lower(), cache_dir=tmp_path) == VPIC_FIELDS
    assert len(fake.calls) == 1


def test_vpic_labels_are_german() -> None:
    assert vehicle.VPIC_FIELDS["Model"] == "Modell"
    assert vehicle.VPIC_FIELDS["PlantCountry"] == "Werk-Land"
    assert vehicle.VPIC_FIELDS["DisplacementL"] == "Hubraum (l)"


@pytest.mark.parametrize(
    "error", [OSError("Netz weg"), URLError("DNS"), TimeoutError("zu langsam")]
)
def test_vpic_network_error_gives_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, error: Exception
) -> None:
    _fake(monkeypatch, error)
    assert lookup_vpic("1HGCM82633A004352", cache_dir=tmp_path) == {}
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "body", [b"<html>Wartung</html>", b"[]", b'{"Results": []}', b'{"Results": [5]}', b"\xff"]
)
def test_vpic_unexpected_answer_gives_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, body: bytes
) -> None:
    _fake(monkeypatch, body)
    assert lookup_vpic("1HGCM82633A004352", cache_dir=tmp_path) == {}
    assert list(tmp_path.iterdir()) == []  # nichts Unbrauchbares cachen


def test_vpic_broken_cache_is_refetched(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    vin = "1HGCM82633A004352"
    (tmp_path / f"{vin}.json").write_text("kaputt", encoding="utf-8")
    fake = _fake(monkeypatch, json.dumps(VPIC_RESPONSE).encode())
    assert lookup_vpic(vin, cache_dir=tmp_path) == VPIC_FIELDS
    assert len(fake.calls) == 1


def test_vpic_unwritable_cache_still_returns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    blocker = tmp_path / "datei"
    blocker.write_text("", encoding="utf-8")
    _fake(monkeypatch, json.dumps(VPIC_RESPONSE).encode())
    assert lookup_vpic("1HGCM82633A004352", cache_dir=blocker / "vpic") == VPIC_FIELDS


@pytest.mark.usefixtures("no_network")
def test_vpic_invalid_vin_not_sent(tmp_path: Path) -> None:
    assert lookup_vpic("ABC/../../etc", cache_dir=tmp_path) == {}


def test_vpic_default_cache_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert default_vpic_cache_dir() == tmp_path / "obd-diag" / "vpic"
    monkeypatch.setenv("XDG_CACHE_HOME", "relativ")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert default_vpic_cache_dir() == tmp_path / ".cache" / "obd-diag" / "vpic"


def test_vpic_uses_default_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    _fake(monkeypatch, json.dumps(VPIC_RESPONSE).encode())
    lookup_vpic("1HGCM82633A004352")
    assert (tmp_path / "obd-diag" / "vpic" / "1HGCM82633A004352.json").is_file()
