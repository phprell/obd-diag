"""Dekodierung von Fehlercodes (DTC) nach SAE J2012 aus Mode-03/07/0A-Antworten."""

from obd_diag.i18n import tr
from obd_diag.protocol.frames import split_messages

_SYSTEM = "PCBU"

# Ablehnungsgründe (``7F <Mode> <NRC>``, ISO 14229-1 / ISO 15031-5), die nur heißen: dieses
# Steuergerät kennt den Mode nicht (z. B. 0A bei älteren). Es hat dann keine Codes dazu.
UNSUPPORTED_NRCS = frozenset({0x11, 0x12})
RESPONSE_PENDING = 0x78  # Steuergerät arbeitet noch, die eigentliche Antwort folgt


class NegativeDtcResponse(ValueError):
    """Ein Steuergerät hat die Anfrage abgelehnt oder noch nicht beantwortet.

    Anders als „Mode nicht unterstützt“ heißt das nicht „keine Codes“: der Fehlerspeicher
    dieses Steuergeräts ist unbekannt (z. B. ``21`` beschäftigt, ``22`` Bedingungen nicht
    erfüllt, ``78`` Antwort folgt).
    """

    def __init__(self, mode: int, nrc: int) -> None:
        self.mode = mode
        self.nrc = nrc
        super().__init__(
            tr("Steuergerät lehnt Mode {mode:02X} ab (Antwort 7F {mode:02X} {nrc:02X})").format(
                mode=mode, nrc=nrc
            )
        )


def decode_dtc(high: int, low: int) -> str:
    """Zwei Rohbytes in einen Code wie ``P0133`` umwandeln."""
    return f"{_SYSTEM[high >> 6]}{(high >> 4) & 0x3}{high & 0xF:X}{low:02X}"


def parse_dtc_response(response: str, mode: int = 0x03, *, can: bool = False) -> list[str]:
    """Liest alle Codes aus einer Adapter-Antwort.

    ``response`` ist der Hex-Text des ELM327 ohne Header, mit oder ohne Leerzeichen,
    eine Nachricht pro Steuergerät; mehrteilige CAN-Nachrichten (``0: …``/``1: …``)
    werden zusammengesetzt. Bei CAN (ISO 15765-4) folgt auf das Mode-Byte ein
    Zählbyte (SAE J1979); Bytes nach den gezählten Codes sind Füllbytes. Ältere
    Protokolle füllen stattdessen mit ``00 00`` auf. Negative Antworten (``7F``)
    einzelner Steuergeräte werden nur übersprungen, wenn sie „Mode nicht unterstützt“
    heißen (``UNSUPPORTED_NRCS``); jede andere ergibt ``NegativeDtcResponse``, damit eine
    Ablehnung nie als „keine Codes“ gelesen wird.

    ``ValueError`` bei unerwarteten Antworten; ``FrameSequenceError`` (Unterklasse),
    wenn Frames mehrerer Steuergeräte vermischt sind (dann mit Headern neu lesen).
    """
    return parse_dtc_messages(split_messages(response), mode, can=can)


def parse_dtc_messages(messages: list[bytes], mode: int = 0x03, *, can: bool = False) -> list[str]:
    """Wie ``parse_dtc_response``, aber für bereits zerlegte Nachrichten (je Steuergerät)."""
    sid = mode + 0x40
    codes: list[str] = []
    for message in messages:
        if len(message) >= 2 and message[0] == 0x7F and message[1] == mode:
            if len(message) >= 3 and message[2] in UNSUPPORTED_NRCS:
                continue
            raise NegativeDtcResponse(mode, message[2] if len(message) >= 3 else 0x00)
        if not message or message[0] != sid:
            raise ValueError(
                tr("unerwartete Antwort auf Mode {mode:02X}: {data}").format(
                    mode=mode, data=repr(message.hex(" ").upper())
                )
            )
        data = message[1:]
        if can:
            if not data:
                raise ValueError(tr("Antwort auf Mode {mode:02X} ohne Zählbyte").format(mode=mode))
            count, data = data[0], data[1:]
            if len(data) < 2 * count:
                raise ValueError(
                    tr(
                        "Antwort auf Mode {mode:02X} unvollständig ({got} von {count} Codes)"
                    ).format(mode=mode, got=len(data) // 2, count=count)
                )
            data = data[: 2 * count]
        for i in range(0, len(data) - 1, 2):
            high, low = data[i], data[i + 1]
            if high == 0 and low == 0:
                continue
            codes.append(decode_dtc(high, low))
    return codes
