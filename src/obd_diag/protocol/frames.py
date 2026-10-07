"""Zerlegt ELM327-Antworten (ohne Header) in Nachrichten, eine je Steuergerät.

Bei CAN setzt der Adapter mehrteilige ISO-TP-Nachrichten selbst zusammen und gibt sie
so aus (``ATH0``, mit oder ohne Leerzeichen)::

    008
    0: 43 03 01 33 03 00
    1: 01 71 00 00 00 00 00

Die erste Zeile ist die Nutzdatenlänge in Bytes (hex), dann folgen die Frames mit
fortlaufender Nummer 0 bis F. Der letzte Frame ist aufgefüllt und wird auf die Länge
gekürzt. Alle anderen Zeilen sind je eine vollständige Nachricht.
"""

import re

_BYTE_COUNT = re.compile(r"^[0-9A-F]{3}$")
_FRAME = re.compile(r"^([0-9A-F]):\s*(.*)$")


def _hex(text: str) -> bytes:
    try:
        return bytes.fromhex(text.replace(" ", ""))
    except ValueError:
        raise ValueError(f"keine Hex-Daten: {text!r}") from None


def split_messages(response: str) -> list[bytes]:
    """Liefert die Nutzdaten jeder Nachricht in ``response``."""
    messages: list[bytearray] = []
    lengths: list[int | None] = []
    multi = False  # gehört die nächste ``N:``-Zeile zur aktuellen Nachricht?
    for raw in response.upper().splitlines():
        line = raw.strip()
        if not line:
            continue
        if _BYTE_COUNT.match(line):
            messages.append(bytearray())
            lengths.append(int(line, 16))
            multi = True
            continue
        frame = _FRAME.match(line)
        if frame is None:
            messages.append(bytearray(_hex(line)))
            lengths.append(None)
            multi = False
            continue
        length = lengths[-1] if multi else None
        complete = length is None or len(messages[-1]) >= length
        if not multi or (frame.group(1) == "0" and messages[-1] and complete):
            # Mehrteilige Nachricht ohne Längenzeile
            messages.append(bytearray())
            lengths.append(None)
            multi = True
        messages[-1] += _hex(frame.group(2))
    return [
        bytes(data if length is None else data[:length])
        for data, length in zip(messages, lengths, strict=True)
    ]
