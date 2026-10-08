from collections.abc import Iterator

import pytest

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
        original(self, data)

    monkeypatch.setattr(SerialTransport, "write", checked)
