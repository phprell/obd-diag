from collections.abc import Iterator

import pytest

from tests.command_spec import load_spec
from tests.emulator_patches import patch_dtc_count_byte
from tests.fakes import FakeTransport


@pytest.fixture
def fake_transport() -> FakeTransport:
    return FakeTransport({"ATZ": "ELM327 v1.5", "ATRV": "12.6V", "03": "43 01 33 00 00 00 00"})


@pytest.fixture(autouse=True)
def _clear_enabled(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Löschen ist ausgeliefert gesperrt (``services/clear.py``). Die Tests des
    Löschablaufs prüfen ihn trotzdem vollständig, damit er bei der Freigabe stimmt;
    Tests mit ``@pytest.mark.clear_disabled`` sehen die ausgelieferte Sperre."""
    from obd_diag.services import clear

    if request.node.get_closest_marker("clear_disabled") is None:
        monkeypatch.setattr(clear, "CLEAR_ENABLED", True)


@pytest.fixture(autouse=True)
def _no_request_gap(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Die Mindestpause zwischen zwei Anfragen ans Fahrzeug (``MIN_REQUEST_GAP``) kostete
    bei rund 1600 Tests Minuten. Sie wird in ``tests/unit/test_request_gap.py`` geprüft;
    Tests mit ``@pytest.mark.request_gap`` sehen den ausgelieferten Wert."""
    from obd_diag.protocol import elm327

    if request.node.get_closest_marker("request_gap") is None:
        monkeypatch.setattr(elm327, "MIN_REQUEST_GAP", 0.0)


@pytest.fixture(autouse=True)
def _emulator_dtc_count_byte(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Fehlercode-Antworten des ELM327-emulator standardgemäß (siehe emulator_patches)."""
    try:
        from elm import obd_message
    except ImportError:
        yield
        return
    patch_dtc_count_byte(monkeypatch, obd_message)
    yield


@pytest.fixture(autouse=True)
def _serial_wire_format(monkeypatch: pytest.MonkeyPatch) -> None:
    """Auch über den echten seriellen Transport (Emulator-Tests) geht nur, was dem
    ELM327-Befehlsformat entspricht."""
    from obd_diag.transport.serial import SerialTransport
    from tests.fakes import WIRE_FORMAT

    original = SerialTransport.write

    def checked(self: SerialTransport, data: bytes) -> None:
        assert WIRE_FORMAT.fullmatch(data), f"falsches Befehlsformat: {data!r}"
        cmd = data.decode("ascii").removesuffix("\r")
        assert load_spec().allows(cmd), f"{cmd!r} steht nicht in command_spec.yaml"
        original(self, data)

    monkeypatch.setattr(SerialTransport, "write", checked)


@pytest.fixture(autouse=True)
def _commands_match_spec(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Jeder Befehl, der in irgendeinem Test tatsächlich an einen Transport geht, muss in
    ``tests/fixtures/command_spec.yaml`` stehen (Datenblatt bzw. J1979, mit Seite).

    ``Elm327.command`` ist der einzige Weg zum Adapter (Architekturtest in
    ``test_command_guard.py``). Weist die Freigabeliste einen Befehl ab, ist nichts
    gesendet; kommt er durch, muss die Spezifikation ihn erlauben. Ausgenommen sind nur
    Tests mit ``@pytest.mark.foreign_commands``, die Mitschnitte anderer Programme
    nachspielen und die Freigabeliste dafür ausdrücklich umgehen."""
    if request.node.get_closest_marker("foreign_commands") is not None:
        return
    from obd_diag.protocol.elm327 import Elm327, ForbiddenCommandError

    original = Elm327.command

    def checked(self: Elm327, cmd: str) -> str:
        allowed = load_spec().allows(cmd)
        try:
            result = original(self, cmd)
        except ForbiddenCommandError:
            raise  # nichts gesendet
        except Exception:
            if not allowed:
                pytest.fail(f"{cmd!r} gesendet, steht aber nicht in command_spec.yaml")
            raise
        if not allowed:
            pytest.fail(f"{cmd!r} gesendet, steht aber nicht in command_spec.yaml")
        return result

    monkeypatch.setattr(Elm327, "command", checked)
