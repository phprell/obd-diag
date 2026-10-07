import pytest

from obd_diag.cli import main


def test_missing_port_gives_clean_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["info", "--port", "/dev/does-not-exist"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("Fehler: /dev/does-not-exist lässt sich nicht öffnen")
    assert "Traceback" not in err
