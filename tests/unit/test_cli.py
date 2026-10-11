import io
import json
from pathlib import Path

import pytest

from obd_diag import cli
from obd_diag.cli import main
from obd_diag.data.dtc_catalog import DtcCatalog
from obd_diag.services import vehicle
from obd_diag.services.session import save_session
from obd_diag.transport.discovery import PortInfo
from tests.fakes import (
    CAN_CAR,
    CAN_CAR_ENGINE_OFF,
    CAN_CAR_FULL,
    CLEARED,
    FakeCatalog,
    FakeTransport,
)
from tests.samples import full_session


def test_missing_port_gives_clean_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["info", "--port", "/dev/does-not-exist"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("Fehler: /dev/does-not-exist lässt sich nicht öffnen")
    assert "Traceback" not in err


@pytest.fixture
def car(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Ersetzt den seriellen Port durch einen Fake; Antworten lassen sich anpassen."""
    responses = dict(CAN_CAR)
    monkeypatch.setattr(cli, "SerialTransport", lambda port, baud: FakeTransport(responses))
    return responses


def _use_catalog(monkeypatch: pytest.MonkeyPatch, catalog: FakeCatalog | None) -> None:
    monkeypatch.setattr(DtcCatalog, "default", classmethod(lambda cls: catalog))


def test_scan_table(
    car: dict[str, str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _use_catalog(monkeypatch, FakeCatalog({"P0133": "Lambdasonde reagiert zu langsam"}))
    assert main(["scan"]) == 0
    out, err = capsys.readouterr()
    assert err == ""
    assert "Protokoll:    ISO 15765-4 (CAN 11/500)" in out
    assert "Bordspannung: 12.4 V" in out
    assert "Batteriespannung niedrig" not in out
    assert "Gespeichert:\n  P0133  Lambdasonde reagiert zu langsam\n  P0300  (keine" in out
    assert "Ausstehend:\n  P0133  Lambdasonde" in out
    assert "Permanent:" not in out


def test_scan_without_codes_and_catalog(
    car: dict[str, str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _use_catalog(monkeypatch, None)
    car.update({"ATRV": "11.0V", "03": "4300", "07": "NO DATA", "0A": "?"})
    assert main(["scan"]) == 0
    out, err = capsys.readouterr()
    assert "tools/build_dtc_db.py" in err
    assert "Batteriespannung niedrig – Ergebnisse können unzuverlässig sein" in out  # noqa: RUF001
    assert out.endswith("Keine Fehlercodes gespeichert.\n")


def test_scan_json(
    car: dict[str, str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _use_catalog(monkeypatch, FakeCatalog({"P0133": "Lambdasonde reagiert zu langsam"}))
    assert main(["scan", "--json", "--lang", "en"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["voltage"] == 12.4
    assert data["low_voltage"] is False
    assert data["codes"][0]["kind"] == "stored"
    assert data["codes"][0]["info"]["title"] == "Lambdasonde reagiert zu langsam"
    assert data["codes"][1] == {"code": "P0300", "kind": "stored", "info": None, "online": None}


def test_scan_adapter_error(
    car: dict[str, str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _use_catalog(monkeypatch, None)
    car["0100"] = "SEARCHING...\rUNABLE TO CONNECT"
    assert main(["scan"]) == 1
    err = capsys.readouterr().err
    assert "Fehler: 0100: UNABLE TO CONNECT\nHinweis: Kein Steuergerät antwortet. Zündung" in err


Car = tuple[dict[str, str], list[FakeTransport]]


@pytest.fixture
def ready_car(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Car:
    """Fahrzeug mit Motor aus; Sicherungen landen unter ``tmp_path``."""
    responses = dict(CAN_CAR_ENGINE_OFF)
    transports: list[FakeTransport] = []

    def open_port(port: str, baud: int) -> FakeTransport:
        transports.append(FakeTransport(responses, CLEARED))
        return transports[-1]

    monkeypatch.setattr(cli, "SerialTransport", open_port)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    _use_catalog(monkeypatch, FakeCatalog({"P0133": "Lambdasonde reagiert zu langsam"}))
    return responses, transports


def _sent(transports: list[FakeTransport]) -> list[str]:
    return [cmd for t in transports for cmd in t.sent]


def _answer(monkeypatch: pytest.MonkeyPatch, text: str | None) -> list[str]:
    """Beantwortet die Rückfrage mit ``text`` (``None``: Eingabe beendet, Strg+D)."""
    prompts: list[str] = []

    def fake_input(prompt: str) -> str:
        prompts.append(prompt)
        if text is None:
            raise EOFError
        return text

    monkeypatch.setattr("builtins.input", fake_input)
    return prompts


def test_clear_after_confirmation(
    ready_car: Car,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    prompts = _answer(monkeypatch, " Ja\n")
    assert main(["clear"]) == 0
    out, err = capsys.readouterr()
    assert err == ""
    assert prompts == ['Wirklich löschen? Zum Bestätigen "ja" eingeben: ']
    assert (
        "Folgende Fehlercodes werden im Steuergerät gelöscht:\n"
        "  P0133  Lambdasonde reagiert zu langsam\n"
        "  P0300  (keine Beschreibung im Katalog)\n"
        "  P0171  (keine Beschreibung im Katalog)\n"
        "Achtung:"
    ) in out
    assert "Freeze Frame und Readiness-Status" in out
    (backup,) = (tmp_path / "obd-diag" / "backups").iterdir()
    assert f"Gelöscht. Sicherung: {backup}\n" in out
    assert out.split("Kontroll-Scan:\n")[1].endswith("\nKeine Fehlercodes gespeichert.\n")
    assert _sent(ready_car[1]).count("04") == 1


@pytest.mark.parametrize("answer", ["nein", "", "j", "yes", None])
def test_clear_declined(
    ready_car: Car,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    answer: str | None,
) -> None:
    _answer(monkeypatch, answer)
    assert main(["clear"]) == 1
    assert capsys.readouterr().out.endswith("Abgebrochen, nichts gelöscht.\n")
    assert "04" not in _sent(ready_car[1])
    assert not (tmp_path / "obd-diag").exists()


def test_clear_yes_skips_prompt(
    ready_car: Car, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    prompts = _answer(monkeypatch, "nein")
    assert main(["clear", "--yes", "--lang", "en"]) == 0
    assert prompts == []
    assert "Cleared. Backup: " in capsys.readouterr().out  # --lang gilt für alle Ausgaben
    assert _sent(ready_car[1]).count("04") == 1


def test_clear_nothing_to_do(
    ready_car: Car, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    prompts = _answer(monkeypatch, "ja")
    ready_car[0].update({"03": "4300", "07": "4700"})
    assert main(["clear"]) == 0
    assert capsys.readouterr().out.endswith(
        "Keine gespeicherten oder ausstehenden Fehlercodes, es wird nichts gelöscht.\n"
    )
    assert prompts == []
    assert "04" not in _sent(ready_car[1])


def test_clear_refused_while_engine_runs(
    ready_car: Car, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    prompts = _answer(monkeypatch, "ja")
    ready_car[0]["010C"] = "410C0C80"
    assert main(["clear"]) == 1
    assert capsys.readouterr().err.startswith("Fehler: Motor läuft (800 1/min).")
    assert prompts == []  # abgelehnt, bevor überhaupt gefragt wird
    assert "04" not in _sent(ready_car[1])


def test_clear_refused_by_ecu(ready_car: Car, capsys: pytest.CaptureFixture[str]) -> None:
    ready_car[0]["04"] = "7F0422"
    assert main(["clear", "--yes"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("Fehler: Steuergerät lehnt das Löschen ab: Bedingungen nicht erfüllt")
    assert "Sicherung: " in err


def test_ports(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    ports = [
        PortInfo("/dev/rfcomm0", "Bluetooth (RFCOMM)"),
        PortInfo("/dev/ttyUSB10", "FT232R USB UART (FTDI)"),
    ]
    monkeypatch.setattr(cli, "list_ports", lambda: ports)
    assert main(["ports"]) == 0
    assert capsys.readouterr().out == (
        "/dev/rfcomm0   Bluetooth (RFCOMM)\n/dev/ttyUSB10  FT232R USB UART (FTDI)\n"
    )


def test_no_ports(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cli, "list_ports", lambda: [])
    assert main(["ports"]) == 0
    assert capsys.readouterr().out.startswith("Keine Adapter gefunden")


# --- export ---


def test_export_pdf_and_csv(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    session_file = save_session(full_session(), tmp_path)
    pdf, csv_path = tmp_path / "bericht.pdf", tmp_path / "codes.csv"
    assert main(["export", str(session_file), "--pdf", str(pdf), "--csv", str(csv_path)]) == 0
    out, err = capsys.readouterr()
    assert err == ""
    assert out == f"PDF-Bericht: {pdf}\nCSV: {csv_path}\n"
    assert pdf.read_bytes().startswith(b"%PDF")
    assert csv_path.read_bytes().startswith(b"\xef\xbb\xbfCode;Art;")


def test_export_only_csv(tmp_path: Path) -> None:
    session_file = save_session(full_session(), tmp_path)
    assert main(["export", str(session_file), "--csv", str(tmp_path / "c.csv")]) == 0
    assert not list(tmp_path.glob("*.pdf"))


def test_export_needs_target(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    session_file = save_session(full_session(), tmp_path)
    with pytest.raises(SystemExit) as info:
        main(["export", str(session_file)])
    assert info.value.code == 2
    assert "--pdf und/oder --csv" in capsys.readouterr().err


@pytest.mark.parametrize("content", [None, '{"adapter": "x"}'])
def test_export_rejects_missing_or_foreign_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], content: str | None
) -> None:
    path = tmp_path / "fremd.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    assert main(["export", str(path), "--pdf", str(tmp_path / "x.pdf")]) == 1
    err = capsys.readouterr().err
    assert err.startswith("Fehler: ")
    assert "Traceback" not in err
    assert not (tmp_path / "x.pdf").exists()


# --- diagnose ---


@pytest.fixture
def full_car(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[FakeTransport]:
    """Vollständig antwortendes Fahrzeug; Sitzungen und Cache landen unter ``tmp_path``."""
    transports: list[FakeTransport] = []

    def open_port(port: str, baud: int) -> FakeTransport:
        transports.append(FakeTransport(dict(CAN_CAR_FULL)))
        return transports[-1]

    monkeypatch.setattr(cli, "SerialTransport", open_port)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    _use_catalog(monkeypatch, FakeCatalog({"P0133": "Lambdasonde reagiert zu langsam"}))

    def no_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("Netzwerkzugriff ohne --online-vin")

    monkeypatch.setattr(vehicle, "urlopen", no_network)
    return transports


def test_diagnose_table(
    full_car: list[FakeTransport], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["diagnose"]) == 0
    out, err = capsys.readouterr()
    assert err == ""
    assert out.startswith(
        "Fahrzeug:\n"
        "  FIN:        WVWZZZ1KZ6W123456\n"
        "  Prüfziffer: nicht vorgeschrieben (weicht ab)\n"
        "  Hersteller: Volkswagen\n"
        "  Land:       Deutschland\n"
        "  Modelljahr: 2006 (aus Stelle 10, ohne Gewähr)\n"
        "\nAdapter:      ELM327 v1.5\n"
    )
    assert "Gespeichert:\n  P0133  Lambdasonde reagiert zu langsam\n" in out
    assert (
        "Readiness (Otto-Motor):\n  Kontrollleuchte (MIL): an\n  Gemeldete Fehlercodes: 3\n" in out
    )
    assert "  Katalysator:               nicht abgeschlossen\n" in out
    assert "  Katalysatorheizung:        nicht unterstützt\n" in out
    assert "  Tankentlüftung:            abgeschlossen\n" in out
    assert "  Alle Tests abgeschlossen: nein\n  Hinweis: Das ist keine AU-Bewertung" in out
    assert "AU-bereit" not in out
    assert out.endswith(
        "Freeze Frame (ausgelöst durch P0133):\n"
        "  Kühlmitteltemperatur:  75 °C\n"
        "  Drehzahl:              1726 1/min\n"
    )
    assert "04" not in full_car[0].sent
    assert not (tmp_path / "data").exists()  # ohne --save wird nichts gespeichert


def test_diagnose_json_and_save(
    full_car: list[FakeTransport], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["diagnose", "--json", "--save"]) == 0
    out, err = capsys.readouterr()
    data = json.loads(out)  # stdout bleibt reines JSON
    assert data["format"] == "obd-diag-session"
    assert data["vehicle"]["vin"] == "WVWZZZ1KZ6W123456"
    assert data["readiness"]["ready"] is False
    assert data["readiness"]["all_complete"] is False
    assert data["freeze_frame"]["dtc"] == "P0133"
    (saved,) = (tmp_path / "data" / "obd-diag" / "sessions").iterdir()
    assert err == f"Sitzung gespeichert: {saved}\n"
    assert json.loads(saved.read_text(encoding="utf-8")) == data


def test_diagnose_without_optional_parts(
    full_car: list[FakeTransport],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing = dict(CAN_CAR_FULL)
    missing.update(dict.fromkeys(["0101", "0902", "020200", "020500", "020C00"], "NO DATA"))
    monkeypatch.setattr(cli, "SerialTransport", lambda port, baud: FakeTransport(missing))
    assert main(["diagnose"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Fahrzeug:\n  FIN nicht verfügbar")
    assert "Readiness: nicht verfügbar (PID 01 nicht beantwortet).\n" in out
    assert out.endswith("Freeze Frame: keiner gespeichert.\n")


def test_diagnose_pdf_and_csv(
    full_car: list[FakeTransport], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pdf, csv_path = tmp_path / "bericht.pdf", tmp_path / "codes.csv"
    assert main(["diagnose", "--pdf", str(pdf), "--csv", str(csv_path)]) == 0
    err = capsys.readouterr().err
    assert err == f"PDF-Bericht: {pdf}\nCSV: {csv_path}\n"
    assert pdf.read_bytes().startswith(b"%PDF")
    assert "WVWZZZ1KZ6W123456" in csv_path.read_text(encoding="utf-8-sig")


def test_diagnose_export_error(
    full_car: list[FakeTransport], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "fehlt" / "bericht.csv"
    assert main(["diagnose", "--csv", str(target)]) == 1
    assert capsys.readouterr().err.startswith("Fehler: ")


def test_diagnose_online_vin(
    full_car: list[FakeTransport],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    body = {"Results": [{"Model": "Golf", "EngineCylinders": "4", "BodyClass": ""}]}
    urls: list[str] = []

    def fake_urlopen(url: str, timeout: float) -> io.BytesIO:
        urls.append(url)
        return io.BytesIO(json.dumps(body).encode())

    monkeypatch.setattr(vehicle, "urlopen", fake_urlopen)
    assert main(["diagnose", "--online-vin"]) == 0
    out = capsys.readouterr().out
    assert urls == [vehicle.VPIC_URL.format(vin="WVWZZZ1KZ6W123456")]
    assert "  Modell:     Golf\n  Zylinder:   4\n" in out
    assert "Karosserie" not in out


def test_diagnose_scan_error(
    full_car: list[FakeTransport],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    broken = dict(CAN_CAR_FULL, **{"0100": "SEARCHING...\rUNABLE TO CONNECT"})
    monkeypatch.setattr(cli, "SerialTransport", lambda port, baud: FakeTransport(broken))
    assert main(["diagnose", "--save"]) == 1
    err = capsys.readouterr().err
    assert "Fehler: 0100: UNABLE TO CONNECT\nHinweis: Kein Steuergerät antwortet. Zündung" in err
    assert not (tmp_path / "data").exists()


# --- vin ---


def test_vin_from_car(full_car: list[FakeTransport], capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["vin"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Fahrzeug:\n  FIN:        WVWZZZ1KZ6W123456\n")
    assert "  Hersteller: Volkswagen\n" in out
    assert full_car[0].sent[0] == "ATZ"
    assert "0902" in full_car[0].sent


def test_vin_offline_argument(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["vin", "1M8GDM9AXKP042788", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data == {
        "vin": "1M8GDM9AXKP042788",
        "valid": True,
        "checksum_ok": True,
        "wmi": "1M8",
        "manufacturer": "Motor Coach Industries",
        "country": "USA",
        "model_year": 1989,
        "online": {},
        "model_year_alternatives": [],
    }


def test_vin_ambiguous_model_year_offline(capsys: pytest.CaptureFixture[str]) -> None:
    # Europa, Stelle 10 „T“: 2026 oder 1996; ohne Fahrzeug kein Protokoll, beide möglich
    assert main(["vin", "WVWZZZ1KZTW123456"]) == 0
    out = capsys.readouterr().out
    assert "  Modelljahr: 2026 oder 1996 (aus Stelle 10, ohne Gewähr)\n" in out


def test_vin_from_can_car_drops_implausible_year(
    full_car: list[FakeTransport],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Stelle 10 „T“ über CAN gelesen: 1996 ist für CAN-OBD unplausibel
    vin_t = "014\r0:490201575657\r1:5A5A5A314B5A54\r2:57313233343536"
    car = dict(CAN_CAR_FULL, **{"0902": vin_t})
    monkeypatch.setattr(cli, "SerialTransport", lambda port, baud: FakeTransport(car))
    assert main(["vin"]) == 0
    out = capsys.readouterr().out
    assert "WVWZZZ1KZTW123456" in out
    assert "  Modelljahr: 2026 (aus Stelle 10, ohne Gewähr)\n" in out


@pytest.mark.parametrize(
    ("vin", "line"),
    [
        ("WVWZZZ1KZ6W123456", "  Prüfziffer: nicht vorgeschrieben (weicht ab)\n"),
        ("WBA3A5C53CF256551", "  Prüfziffer: stimmt\n"),  # Europa, stimmt freiwillig
    ],
)
def test_vin_checksum_lines_outside_north_america(
    vin: str, line: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["vin", vin]) == 0
    assert line in capsys.readouterr().out


@pytest.mark.parametrize(
    ("vin", "line"),
    [
        ("1M8GDM9A1KP042788", "  Prüfziffer: stimmt nicht\n"),
        ("1M8GDM9AXKP042788", "  Prüfziffer: stimmt\n"),
        ("WVWZZZ1KZ6W12345", "  Hinweis:    FIN ungültig (Länge oder Zeichen)\n"),
    ],
)
def test_vin_checksum_lines(vin: str, line: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["vin", vin]) == 0
    assert line in capsys.readouterr().out


def test_vin_not_supported(
    full_car: list[FakeTransport],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    old = dict(CAN_CAR_FULL, **{"0902": "NO DATA"})
    monkeypatch.setattr(cli, "SerialTransport", lambda port, baud: FakeTransport(old))
    assert main(["vin"]) == 1
    assert "keine FIN" in capsys.readouterr().err


def test_scan_online_codes(
    car: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from obd_diag.services import dtc_online

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    urls: list[str] = []

    def fake_urlopen(url: str, timeout: float) -> io.BytesIO:
        urls.append(url)
        return io.BytesIO(b"P0300 - Random/Multiple Cylinder Misfire Detected\n")

    monkeypatch.setattr(dtc_online, "urlopen", fake_urlopen)
    _use_catalog(monkeypatch, FakeCatalog({"P0133": "Lambdasonde reagiert zu langsam"}))
    assert main(["scan", "--online-codes"]) == 0
    out = capsys.readouterr().out
    assert [u.rsplit("/", 1)[1] for u in urls] == ["p_codes.txt"]  # ohne FIN kein Hersteller
    assert (
        "  P0300  (keine Beschreibung im Katalog)\n"
        "         Online, ungeprüft: Random/Multiple Cylinder Misfire Detected\n"
        "         Quelle: Wal33D/dtc-database (MIT), p_codes.txt, https://github.com/" in out
    )
    assert "         Im Web suchen: https://duckduckgo.com/?q=OBD+P0300\n" in out
    assert "P0133  Lambdasonde reagiert zu langsam\n  P0300" in out  # Katalogtext ohne Link


def test_scan_online_codes_without_network(
    car: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from urllib.error import URLError

    from obd_diag.services import dtc_online

    def offline(url: str, timeout: float) -> None:
        raise URLError("kein Netz")

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setattr(dtc_online, "urlopen", offline)
    _use_catalog(monkeypatch, None)
    assert main(["scan", "--online-codes", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [c["online"] for c in data["codes"]] == [None] * len(data["codes"])


def test_diagnose_online_codes_uses_manufacturer(
    full_car: list[FakeTransport],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from obd_diag.services import dtc_online

    urls: list[str] = []

    def fake_urlopen(url: str, timeout: float) -> io.BytesIO:
        urls.append(url)
        return io.BytesIO(b"P0171 - System Too Lean Bank 1\n")

    monkeypatch.setattr(dtc_online, "urlopen", fake_urlopen)
    assert main(["diagnose", "--online-codes"]) == 0
    out = capsys.readouterr().out
    # FIN-Hersteller Volkswagen: erst dessen Datei, dann die allgemeine; FIN nie in der URL
    assert [u.rsplit("/", 1)[1] for u in urls] == ["volkswagen_codes.txt", "p_codes.txt"]
    assert not any("WVWZZZ" in u for u in urls)
    assert "Online, ungeprüft: System Too Lean Bank 1" in out
    assert "Im Web suchen: https://duckduckgo.com/?q=P0300+Volkswagen" in out
