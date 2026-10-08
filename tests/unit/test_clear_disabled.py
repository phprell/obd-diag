"""Löschen ist ausgeliefert gesperrt, bis das Lesen an einem echten Fahrzeug geprüft ist.

Diese Tests sehen die Sperre wie ausgeliefert (``clear_disabled``); alle übrigen
Tests prüfen den Löschablauf mit freigegebenem Löschen (``tests/conftest.py``).
"""

from pathlib import Path

import pytest

from obd_diag import cli
from obd_diag.protocol.elm327 import Elm327
from obd_diag.services import clear
from obd_diag.services.clear import CLEAR_DISABLED_MESSAGE, ClearRefused, clear_codes
from tests.fakes import CAN_CAR_FULL
from tests.unit.test_command_guard import WireCheckingTransport

# Beim Import gelesen, bevor eine Fixture den Wert umstellt: so wird ausgeliefert.
SHIPPED = clear.CLEAR_ENABLED

pytestmark = pytest.mark.clear_disabled


def test_clearing_is_disabled_as_shipped() -> None:
    assert SHIPPED is False
    assert clear.clear_enabled() is False


def test_clear_codes_sends_nothing(tmp_path: Path) -> None:
    transport = WireCheckingTransport(CAN_CAR_FULL)
    with pytest.raises(ClearRefused, match="vorerst deaktiviert"):
        clear_codes(Elm327(transport), None, backup_dir=tmp_path / "backups")
    assert transport.sent == []
    assert not (tmp_path / "backups").exists()


def test_cli_clear_does_not_even_open_the_port(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_port(*args: object, **kwargs: object) -> None:
        raise AssertionError("Port darf nicht geöffnet werden")

    monkeypatch.setattr(cli, "_transport", no_port)
    assert cli.main(["clear", "--yes"]) == 1
    assert capsys.readouterr().err == f"Fehler: {CLEAR_DISABLED_MESSAGE}\n"
