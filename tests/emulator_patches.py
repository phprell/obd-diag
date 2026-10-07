"""Standardgemäße Antworten für ELM327-emulator 4.0.0 (Tests und tools/emulator.py).

Der Emulator weicht an zwei Stellen von SAE J1979 ab, die unser strikter Parser zu
Recht ablehnt:

- Mode 03/07/0A bei CAN ohne Zählbyte nach dem Mode-Byte (``43 04 20`` statt
  ``43 01 04 20``); vorgefertigte mehrteilige Frames ohne Header kommen zudem nicht
  im ELM327-Format. Über ``<answer>`` erzeugt der Emulator das echte Format
  (``00A`` / ``0:`` / ``1:``).
- Mode 02 (Freeze Frame) erwartet die Anfrage ohne Frame-Nummer (``020C`` statt
  ``020C00``).

Außerdem bleiben gelöschte Codes über ``ATZ`` hinweg gelöscht, wie bei einem echten
Steuergerät.
"""

from typing import Any, Protocol


class Patcher(Protocol):
    """Die Teile von ``pytest.MonkeyPatch``, die hier gebraucht werden."""

    def setattr(self, target: Any, name: str, value: Any) -> None: ...

    def setitem(self, dic: Any, name: Any, value: Any) -> None: ...


def _answer(obd_message: Any, data: str) -> str:
    return str(
        obd_message.HD(obd_message.ECU_R_ADDR_E)
        + obd_message.SZ(f"{len(data.split()):02X}")
        + obd_message.DT(data)
    )


def rpm_zero(obd_message: Any) -> str:
    """Antwort auf ``010C`` mit Drehzahl 0 (Motor aus), für ``emulator.answer["RPM"]``."""
    return _answer(obd_message, "41 0C 00 00")


def patch_dtc_count_byte(mp: Patcher, obd_message: Any) -> None:
    def dtc_frames(emulator: Any, dtc_list: list[str], sid: str, header: str = "") -> str:
        cleared = emulator.counters.get("cmd_dtc_cleared")
        dtcs = [] if cleared else [d.replace(" ", "").upper() for d in dtc_list if d]
        dtcs = [d for d in dtcs if len(d) == 4]
        data = [sid, f"{len(dtcs):02X}"] + [d[i : i + 2] for d in dtcs for i in (0, 2)]
        return f"<answer>{' '.join(data)}</answer>"

    mp.setattr(obd_message, "dtc_frames", dtc_frames)


def patch_freeze_frame(mp: Patcher, obd_message: Any, dtc: str = "0133") -> None:
    """Mode 02 mit Frame-Nummer 00; Freeze Frame zum Code ``dtc`` (ohne Buchstaben)."""
    car = obd_message.ObdMessage["car"]
    footer = obd_message.ELM_FOOTER
    for name, pid, data in (
        ("INJ_MF_2", "02", f"42 02 00 {dtc[:2]} {dtc[2:]}"),
        ("DTC_COOLANT_TEMP", "05", "42 05 00 73"),
        ("DTC_RPM", "0C", "42 0C 00 1A F8"),
        ("DTC_SPEED", "0D", "42 0D 00 00"),
    ):
        mp.setitem(car[name], "Request", f"^02{pid}00{footer}")
        mp.setitem(car[name], "Response", _answer(obd_message, data))


def patch_persistent_clear(mp: Patcher, obd_message: Any) -> None:
    mp.setitem(
        obd_message.ObdMessage["car"]["CLEAR_DIAG_TC"],
        "Exec",
        'self.counters["cmd_dtc_cleared"] = self.presets["cmd_dtc_cleared"] = True',
    )
