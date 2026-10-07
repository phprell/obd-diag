import json

import pytest

from obd_diag import cli
from obd_diag.cli import main
from obd_diag.data.dtc_catalog import DtcCatalog
from tests.fakes import CAN_CAR, FakeCatalog, FakeTransport


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
