"""Dekodierung von Fehlercodes (DTC) nach SAE J2012 aus Mode-03/07/0A-Antworten."""

_SYSTEM = "PCBU"


def decode_dtc(high: int, low: int) -> str:
    """Zwei Rohbytes in einen Code wie ``P0133`` umwandeln."""
    return f"{_SYSTEM[high >> 6]}{(high >> 4) & 0x3}{high & 0xF:X}{low:02X}"


def parse_dtc_response(response: str, mode: int = 0x03, *, can: bool = False) -> list[str]:
    """Liest alle Codes aus einer Adapter-Antwort.

    ``response`` ist der Hex-Text des ELM327, mit oder ohne Leerzeichen, eine Zeile
    pro Steuergerät. Bei CAN (ISO 15765-4) folgt auf das Mode-Byte ein Zählbyte;
    ältere Protokolle füllen stattdessen mit ``00 00`` auf.
    """
    expected = f"{mode + 0x40:02X}"
    codes: list[str] = []
    for line in response.upper().splitlines():
        hex_text = line.replace(" ", "").strip()
        if not hex_text:
            continue
        if not hex_text.startswith(expected):
            raise ValueError(f"unerwartete Antwort auf Mode {mode:02X}: {line!r}")
        data = bytes.fromhex(hex_text[2:])
        if can:
            count, data = data[0], data[1:]
            data = data[: 2 * count]
        for i in range(0, len(data) - 1, 2):
            high, low = data[i], data[i + 1]
            if high == 0 and low == 0:
                continue
            codes.append(decode_dtc(high, low))
    return codes
