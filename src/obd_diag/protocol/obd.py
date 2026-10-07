"""OBD-II-Dienste über den ELM327.

Bis auf ``clear_dtcs`` (Mode 04) nur lesend.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from obd_diag.protocol.dtc_decode import decode_dtc, parse_dtc_response
from obd_diag.protocol.elm327 import Elm327, ElmError
from obd_diag.protocol.frames import split_messages

# Modes, die Fehlercodes liefern: gespeichert, ausstehend, permanent.
DTC_MODES = (0x03, 0x07, 0x0A)


def read_dtcs(elm: Elm327, mode: int, *, can: bool) -> list[str]:
    """Fehlercodes aller Steuergeräte für Mode 03, 07 oder 0A.

    ``NO DATA`` heißt: kein Code gespeichert, also eine leere Liste.
    """
    if mode not in DTC_MODES:
        raise ValueError(f"Mode {mode:02X} liefert keine Fehlercodes")
    cmd = f"{mode:02X}"
    response = elm.query(cmd)
    if response is None:
        return []
    try:
        return parse_dtc_response(response, mode, can=can)
    except ValueError as e:
        raise ElmError(f"{cmd}: {e}") from e


# Gründe negativer Antworten (``7F <Mode> <NRC>``, ISO 14229 bzw. ISO 15031-5)
_NRC_TEXT = {
    0x10: "allgemein abgelehnt",
    0x11: "Dienst nicht unterstützt",
    0x12: "Unterfunktion nicht unterstützt",
    0x21: "Steuergerät beschäftigt",
    0x22: "Bedingungen nicht erfüllt",
    0x31: "Anfrage außerhalb des gültigen Bereichs",
    0x33: "Zugriff verweigert",
}
_RESPONSE_PENDING = 0x78  # Steuergerät arbeitet noch, die eigentliche Antwort folgt


class NegativeResponseError(ElmError):
    """Ein Steuergerät hat mit ``7F <Mode> <NRC>`` abgelehnt."""

    def __init__(self, mode: int, nrc: int) -> None:
        self.mode = mode
        self.nrc = nrc
        reason = _NRC_TEXT.get(nrc, "unbekannter Grund")
        super().__init__(f"{reason} (Antwort 7F {mode:02X} {nrc:02X})")


def _messages(cmd: str, response: str) -> list[bytes]:
    try:
        return split_messages(response)
    except ValueError as e:
        raise ElmError(f"{cmd}: {e}") from e


def clear_dtcs(elm: Elm327) -> None:
    """Mode 04: gespeicherte und ausstehende Codes, Freeze Frame und Readiness löschen.

    Schreibender Befehl! Vorbedingungen prüfen und sichern muss der Aufrufer (siehe
    ``services.clear``). Erfolg heißt: mindestens ein Steuergerät bestätigt mit ``44``
    und keines lehnt ab. Wirft ``NegativeResponseError`` bei ``7F 04 xx``,
    ``NoDataError`` ohne Antwort und ``ElmError`` bei unerwarteter Antwort.
    """
    response = elm.command("04")
    confirmed = False
    for message in _messages("04", response):
        if len(message) >= 3 and message[0] == 0x7F and message[1] == 0x04:
            if message[2] == _RESPONSE_PENDING:
                continue
            raise NegativeResponseError(0x04, message[2])
        if message[:1] != b"\x44":
            raise ElmError(f"04: unerwartete Antwort {message.hex(' ').upper()!r}")
        confirmed = True
    if not confirmed:
        raise ElmError(f"04: keine Bestätigung ({response!r})")


def read_pid(elm: Elm327, pid: int) -> bytes | None:
    """Mode 01: Datenbytes des ersten Steuergeräts, das ``pid`` beantwortet.

    ``None``, wenn kein Steuergerät antwortet (``NO DATA``) oder alle ablehnen.
    """
    cmd = f"01{pid:02X}"
    response = elm.query(cmd)
    if response is None:
        return None
    for message in _messages(cmd, response):
        if len(message) >= 2 and message[0] == 0x41 and message[1] == pid:
            return message[2:]
    return None


def decode_rpm(data: bytes) -> float:
    """PID 0C: Motordrehzahl in 1/min, ((A*256)+B)/4."""
    if len(data) < 2:
        raise ValueError(f"PID 0C braucht zwei Datenbytes, nicht {len(data)}")
    return (data[0] * 256 + data[1]) / 4


def read_rpm(elm: Elm327) -> float | None:
    """Motordrehzahl in 1/min oder ``None``, wenn sie nicht lesbar ist."""
    data = read_pid(elm, 0x0C)
    if data is None or len(data) < 2:
        return None
    return decode_rpm(data)


# PIDs im Freeze Frame: Schlüssel in ``FreezeFrame.values``, Datenbytes, Umrechnung
_FREEZE_PIDS: dict[int, tuple[str, int, Callable[[bytes], float]]] = {
    0x04: ("engine_load_pct", 1, lambda d: round(d[0] * 100 / 255, 1)),
    0x05: ("coolant_temp_c", 1, lambda d: d[0] - 40),
    0x0C: ("rpm", 2, decode_rpm),
    0x0D: ("speed_kmh", 1, lambda d: d[0]),
}


@dataclass(frozen=True)
class FreezeFrame:
    """Momentaufnahme (Mode 02, Frame 00) beim Setzen eines Fehlercodes.

    ``raw`` enthält die Antworten wie empfangen (Befehl -> Hex-Text), ``values`` die
    daraus dekodierten Werte. Unbeantwortete PIDs fehlen in beiden.
    """

    dtc: str | None = None  # PID 02: Code, der den Freeze Frame ausgelöst hat
    raw: dict[str, str] = field(default_factory=dict)
    values: dict[str, float] = field(default_factory=dict)


def _freeze_data(elm: Elm327, pid: int) -> tuple[str, str, bytes] | None:
    """Befehl, Antwort und Datenbytes zu ``02 <pid> 00``; ``None``, wenn nicht vorhanden."""
    cmd = f"02{pid:02X}00"
    response = elm.query(cmd)
    if response is None:
        return None
    for message in _messages(cmd, response):
        if len(message) >= 3 and message[:3] == bytes((0x42, pid, 0x00)):
            return cmd, response, message[3:]
    return None  # z. B. 7F 02 12: dieser PID ist nicht gespeichert


def read_freeze_frame(elm: Elm327) -> FreezeFrame:
    """Liest Frame 00 von Mode 02: auslösender Code und einige häufige Werte."""
    dtc: str | None = None
    raw: dict[str, str] = {}
    values: dict[str, float] = {}
    found = _freeze_data(elm, 0x02)
    if found is not None:
        cmd, response, data = found
        raw[cmd] = response
        if len(data) >= 2 and (data[0] or data[1]):
            dtc = decode_dtc(data[0], data[1])
    for pid, (name, size, decode) in _FREEZE_PIDS.items():
        found = _freeze_data(elm, pid)
        if found is None:
            continue
        cmd, response, data = found
        raw[cmd] = response
        if len(data) >= size:
            values[name] = decode(data)
    return FreezeFrame(dtc, raw, values)
