from collections.abc import Iterator

import pytest

from tests.emulator_patches import patch_dtc_count_byte
from tests.fakes import FakeTransport


@pytest.fixture
def fake_transport() -> FakeTransport:
    return FakeTransport({"ATZ": "ELM327 v1.5", "ATRV": "12.6V", "03": "43 01 33 00 00 00 00"})


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
