from collections.abc import Iterator

import pytest

from tests.fakes import FakeTransport


@pytest.fixture
def fake_transport() -> FakeTransport:
    return FakeTransport({"ATZ": "ELM327 v1.5", "ATRV": "12.6V", "03": "43 01 33 00 00 00 00"})


@pytest.fixture(autouse=True)
def _emulator_dtc_count_byte(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """ELM327-emulator 4.0.0 lässt bei CAN das Zählbyte nach dem Mode-Byte weg
    (``43 01 33`` statt ``43 01 01 33``) und gibt vorgefertigte mehrteilige Frames
    ohne Header nicht im ELM327-Format aus. Echte Steuergeräte senden das Zählbyte
    (SAE J1979); hier wird die Antwort standardgemäß gebaut und über ``<answer>``
    ausgegeben, wofür der Emulator das echte Format (``00A`` / ``0:`` / ``1:``) erzeugt."""
    try:
        from elm import obd_message
    except ImportError:
        yield
        return

    def dtc_frames(emulator: object, dtc_list: list[str], sid: str, header: str = "") -> str:
        cleared = emulator.counters.get("cmd_dtc_cleared")  # type: ignore[attr-defined]
        dtcs = [] if cleared else [d.replace(" ", "").upper() for d in dtc_list if d]
        dtcs = [d for d in dtcs if len(d) == 4]
        data = [sid, f"{len(dtcs):02X}"] + [d[i : i + 2] for d in dtcs for i in (0, 2)]
        return f"<answer>{' '.join(data)}</answer>"

    monkeypatch.setattr(obd_message, "dtc_frames", dtc_frames)
    yield
