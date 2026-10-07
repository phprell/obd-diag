import json
from pathlib import Path

import pytest

from obd_diag import cli
from obd_diag.cli import main
from obd_diag.data.dtc_catalog import DtcCatalog
from obd_diag.services.session import save_session
from obd_diag.transport.discovery import PortInfo
from tests.fakes import CAN_CAR, CAN_CAR_ENGINE_OFF, CLEARED, FakeCatalog, FakeTransport
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
    assert data["codes"][1] == {"code": "P0300", "kind": "stored", "info": None}


def test_scan_adapter_error(
    car: dict[str, str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _use_catalog(monkeypatch, None)
    car["0100"] = "SEARCHING...\rUNABLE TO CONNECT"
    assert main(["scan"]) == 1
    assert capsys.readouterr().err.endswith("Fehler: 0100: UNABLE TO CONNECT\n")


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
    assert "Gelöscht. Sicherung: " in capsys.readouterr().out
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
