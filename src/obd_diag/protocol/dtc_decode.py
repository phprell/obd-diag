"""Dekodierung von Fehlercodes (DTC) nach SAE J2012 aus Mode-03/07/0A-Antworten."""

from obd_diag.protocol.frames import split_messages

_SYSTEM = "PCBU"


def decode_dtc(high: int, low: int) -> str:
    """Zwei Rohbytes in einen Code wie ``P0133`` umwandeln."""
    return f"{_SYSTEM[high >> 6]}{(high >> 4) & 0x3}{high & 0xF:X}{low:02X}"


def parse_dtc_response(response: str, mode: int = 0x03, *, can: bool = False) -> list[str]:
    """Liest alle Codes aus einer Adapter-Antwort.

    ``response`` ist der Hex-Text des ELM327 ohne Header, mit oder ohne Leerzeichen,
    eine Nachricht pro Steuergerät; mehrteilige CAN-Nachrichten (``0: …``/``1: …``)
    werden zusammengesetzt. Bei CAN (ISO 15765-4) folgt auf das Mode-Byte ein
    Zählbyte; ältere Protokolle füllen stattdessen mit ``00 00`` auf. Fehlt bei CAN
    das Zählbyte (gerade Byteanzahl nach dem Mode-Byte, so z. B. beim
    ELM327-emulator), werden alle Bytepaare als Codes gelesen. Negative Antworten
    (``7F``) einzelner Steuergeräte werden übersprungen.
    """
    sid = mode + 0x40
    codes: list[str] = []
    for message in split_messages(response):
        if len(message) >= 2 and message[0] == 0x7F and message[1] == mode:
            continue
        if not message or message[0] != sid:
            raise ValueError(
                f"unerwartete Antwort auf Mode {mode:02X}: {message.hex(' ').upper()!r}"
            )
        data = message[1:]
        if can and len(data) % 2:
            count, data = data[0], data[1:]
            data = data[: 2 * count]
        for i in range(0, len(data) - 1, 2):
            high, low = data[i], data[i + 1]
            if high == 0 and low == 0:
                continue
            codes.append(decode_dtc(high, low))
    return codes
