"""Online-Erklärungen für Fehlercodes: Quelle, Auswahl der Datei, Cache, Fehler."""

import io
from pathlib import Path
from urllib.error import URLError
from urllib.parse import parse_qs, urlparse

import pytest

from obd_diag.data.dtc_catalog import DtcInfo
from obd_diag.services import dtc_online
from obd_diag.services.diagnostics import (
    DiagnosticCode,
    DtcKind,
    ScanResult,
    add_online_explanations,
)
from obd_diag.services.dtc_online import (
    SOURCE_COMMIT,
    OnlineExplanation,
    brand_file,
    default_cache_dir,
    is_generic,
    lookup_online,
    parse_source,
    search_url,
)

RAW = f"https://raw.githubusercontent.com/Wal33D/dtc-database/{SOURCE_COMMIT}/data/source-data/"

# Auszüge im Format der Quelle (data/source-data/*.txt am gepinnten Commit)
FILES = {
    "mercedes_codes.txt": (
        "P1105 - Atmospheric Pressure Sensor In Control Module\n"
        "P1220 - Fuel Quantity Actuator Y231\n"
    ),
    "p_codes.txt": (
        "P0420 - Catalyst System Efficiency Below Threshold Bank 1\n"
        "P2002 - Diesel Particulate Filter Efficiency Below Threshold Bank 1\n"
    ),
    "u_codes.txt": "U0100 - Lost Communication With ECM/PCM A\n",
    "ford_codes.txt": "U1218 - SCP (J1850) Invalid or Missing Data for External Lamps\n",
}


class FakeNet:
    """Ersatz für ``urlopen``: liefert ``FILES``, zählt Abrufe, wirft auf Wunsch."""

    def __init__(self, error: Exception | None = None) -> None:
        self.urls: list[str] = []
        self.timeouts: list[float] = []
        self.error = error

    def __call__(self, url: str, timeout: float) -> io.BytesIO:
        self.urls.append(url)
        self.timeouts.append(timeout)
        if self.error is not None:
            raise self.error
        name = url.removeprefix(RAW)
        if name not in FILES:
            raise URLError("404")
        return io.BytesIO(FILES[name].encode())


@pytest.fixture
def net(monkeypatch: pytest.MonkeyPatch) -> FakeNet:
    fake = FakeNet()
    monkeypatch.setattr(dtc_online, "urlopen", fake)
    return fake


@pytest.mark.parametrize(
    ("code", "generic"),
    [
        ("P0420", True),
        ("P1220", False),
        ("P2002", True),
        ("P3000", False),
        ("P3300", False),
        ("P3400", True),
        ("P3999", True),
        ("B0001", True),
        ("B1234", False),
        ("B2000", False),
        ("B3000", True),
        ("C0035", True),
        ("C1100", False),
        ("U0100", True),
        ("U1218", False),
        ("U2000", False),
        ("U3000", True),
    ],
)
def test_is_generic_follows_j2012(code: str, generic: bool) -> None:
    assert is_generic(code) is generic


@pytest.mark.parametrize(
    ("manufacturer", "file"),
    [
        ("Mercedes-Benz", "mercedes_codes.txt"),
        ("Mercedes-Benz (SUV)", "mercedes_codes.txt"),
        ("Volkswagen Nutzfahrzeuge", "volkswagen_codes.txt"),
        ("Ford (Europa)", "ford_codes.txt"),
        ("Chevrolet", "chevy_codes.txt"),
        ("Changan Ford", None),  # Name beginnt nicht mit der Marke
        ("Fordson", None),
        ("Opel", None),
        ("", None),
        (None, None),
    ],
)
def test_brand_file(manufacturer: str | None, file: str | None) -> None:
    assert brand_file(manufacturer) == file


def test_brand_files_exist_in_wmi_names() -> None:
    from obd_diag.data.wmi import MANUFACTURERS

    names = set(MANUFACTURERS.values())
    for prefix, _ in dtc_online.BRAND_FILES:
        assert any(n == prefix or n.startswith(prefix + " ") for n in names), prefix


def test_search_url_names_code_and_brand() -> None:
    query = parse_qs(urlparse(search_url("U1218", "Mercedes-Benz (SUV)")).query)
    assert query["q"] == ["U1218 Mercedes-Benz"]
    assert parse_qs(urlparse(search_url("P0420")).query)["q"] == ["OBD P0420"]


def test_parse_source_skips_junk_and_keeps_first() -> None:
    text = (
        "P0420 - Catalyst  System\tBank 1\n"
        "kein Eintrag\n"
        "P04 - zu kurz\n"
        "X0420 - falsche Familie\n"
        "P0420 - zweiter Eintrag\n"
        "U0100 - " + "x" * 400 + "\n"
    )
    entries = parse_source(text)
    assert entries["P0420"] == ("Catalyst System Bank 1", 1)
    assert set(entries) == {"P0420", "U0100"}
    assert len(entries["U0100"][0]) == dtc_online.MAX_TEXT
    assert entries["U0100"][0].endswith("…")


def test_brand_code_from_brand_file(net: FakeNet, tmp_path: Path) -> None:
    found = lookup_online(["P1220"], "Mercedes-Benz", cache_dir=tmp_path, timeout=2.5)
    assert found == {
        "P1220": OnlineExplanation(
            text="Fuel Quantity Actuator Y231",
            source="Wal33D/dtc-database (MIT), mercedes_codes.txt",
            url=(
                f"https://github.com/Wal33D/dtc-database/blob/{SOURCE_COMMIT}"
                "/data/source-data/mercedes_codes.txt#L2"
            ),
            manufacturer="Mercedes-Benz",
        )
    }
    assert net.urls == [RAW + "mercedes_codes.txt"]
    assert net.timeouts == [2.5]


def test_generic_code_falls_back_to_family_file(net: FakeNet, tmp_path: Path) -> None:
    found = lookup_online(["P0420", "U0100"], "Mercedes-Benz", cache_dir=tmp_path)
    assert found["P0420"].text == "Catalyst System Efficiency Below Threshold Bank 1"
    assert found["P0420"].manufacturer is None
    assert found["U0100"].source.endswith("u_codes.txt")
    # Herstellerdatei nur einmal geladen
    assert net.urls == [RAW + f for f in ("mercedes_codes.txt", "p_codes.txt", "u_codes.txt")]


def test_brand_specific_code_never_from_other_brand(net: FakeNet, tmp_path: Path) -> None:
    # U1218 steht nur bei Ford; am Mercedes wäre der Text falsch
    assert lookup_online(["U1218"], "Mercedes-Benz", cache_dir=tmp_path) == {}
    assert net.urls == [RAW + "mercedes_codes.txt"]
    assert lookup_online(["U1218"], "Ford", cache_dir=tmp_path)["U1218"].text.startswith("SCP")


def test_brand_specific_code_without_manufacturer_sends_nothing(
    net: FakeNet, tmp_path: Path
) -> None:
    assert lookup_online(["U1218", "P1220"], None, cache_dir=tmp_path) == {}
    assert net.urls == []


def test_unknown_brand_only_generic(net: FakeNet, tmp_path: Path) -> None:
    found = lookup_online(["P0420", "P1220"], "Opel", cache_dir=tmp_path)
    assert set(found) == {"P0420"}
    assert net.urls == [RAW + "p_codes.txt"]


def test_invalid_codes_are_not_looked_up(net: FakeNet, tmp_path: Path) -> None:
    assert lookup_online(["../x", "P04200", "", "Z0420"], "Ford", cache_dir=tmp_path) == {}
    assert net.urls == []


def test_cache_is_used_and_keyed_by_commit(net: FakeNet, tmp_path: Path) -> None:
    lookup_online(["P0420"], None, cache_dir=tmp_path)
    assert (tmp_path / SOURCE_COMMIT / "p_codes.txt").read_text() == FILES["p_codes.txt"]
    found = lookup_online(["p0420 ", "P2002"], None, cache_dir=tmp_path)
    assert set(found) == {"P0420", "P2002"}
    assert net.urls == [RAW + "p_codes.txt"]  # zweiter Aufruf nur aus dem Cache
    assert not list(tmp_path.rglob("*.tmp"))


def test_network_error_gives_empty_and_is_not_cached(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(dtc_online, "urlopen", FakeNet(URLError("kein Netz")))
    assert lookup_online(["P0420", "P2002"], None, cache_dir=tmp_path) == {}
    assert not tmp_path.exists() or not any(tmp_path.rglob("*.txt"))


def test_timeout_and_os_error_give_empty(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for error in (TimeoutError("zu langsam"), OSError("Netz weg")):
        monkeypatch.setattr(dtc_online, "urlopen", FakeNet(error))
        assert lookup_online(["P0420"], None, cache_dir=tmp_path) == {}


def test_unexpected_answer_is_not_cached(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(dtc_online, "urlopen", lambda url, timeout: io.BytesIO(b"<html>"))
    assert lookup_online(["P0420"], None, cache_dir=tmp_path) == {}
    assert not (tmp_path / SOURCE_COMMIT / "p_codes.txt").exists()


def test_broken_cache_is_reloaded(net: FakeNet, tmp_path: Path) -> None:
    cache = tmp_path / SOURCE_COMMIT / "p_codes.txt"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"\xff\xfe kaputt")
    assert set(lookup_online(["P0420"], None, cache_dir=tmp_path)) == {"P0420"}
    assert net.urls == [RAW + "p_codes.txt"]


def test_unwritable_cache_still_returns(net: FakeNet, tmp_path: Path) -> None:
    blocker = tmp_path / "datei"
    blocker.write_text("")
    assert set(lookup_online(["P0420"], None, cache_dir=blocker / "x")) == {"P0420"}


def test_default_cache_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert default_cache_dir() == tmp_path / "obd-diag" / "dtc-online"
    monkeypatch.setenv("XDG_CACHE_HOME", "relativ")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert default_cache_dir() == tmp_path / ".cache" / "obd-diag" / "dtc-online"


# --- Anreichern eines Scans ---


def _scan(*codes: DiagnosticCode) -> ScanResult:
    return ScanResult("ELM327 v1.5", "ISO 15765-4 (CAN 29/500)", 12.4, list(codes))


EXPLANATION = OnlineExplanation("Text", "Quelle", "https://example.invalid/#L1")


def test_add_online_only_for_codes_without_catalog_text() -> None:
    calls: list[tuple[list[str], str | None]] = []

    def lookup(codes: list[str], manufacturer: str | None) -> dict[str, OnlineExplanation]:
        calls.append((codes, manufacturer))
        return {"U1218": EXPLANATION}

    result = _scan(
        DiagnosticCode("P0420", DtcKind.STORED, DtcInfo("P0420", "Katalysator")),
        DiagnosticCode("U1218", DtcKind.STORED),
        DiagnosticCode("U1218", DtcKind.PENDING),
        DiagnosticCode("P1220", DtcKind.STORED),
    )
    out = add_online_explanations(result, "Mercedes-Benz", lookup)
    assert calls == [(["P1220", "U1218"], "Mercedes-Benz")]
    assert [c.online for c in out.codes] == [None, EXPLANATION, EXPLANATION, None]
    assert [c.online for c in result.codes] == [None] * 4  # Original unverändert


def test_add_online_without_missing_codes_sends_nothing() -> None:
    def refuse(codes: list[str], manufacturer: str | None) -> dict[str, OnlineExplanation]:
        raise AssertionError("ohne fehlenden Text nachgeschlagen")

    result = _scan(DiagnosticCode("P0420", DtcKind.STORED, DtcInfo("P0420", "Katalysator")))
    assert add_online_explanations(result, None, refuse) is result
    assert add_online_explanations(_scan(), None, refuse).codes == []
