"""Zerlegt ELM327-Antworten (ohne Header) in Nachrichten, eine je Steuergerät.

Bei CAN setzt der Adapter mehrteilige ISO-TP-Nachrichten selbst zusammen und gibt sie
so aus (``ATH0``, mit oder ohne Leerzeichen)::

    008
    0: 43 03 01 33 03 00
    1: 01 71 00 00 00 00 00

Die erste Zeile ist die Nutzdatenlänge in Bytes (hex), dann folgen die Frames mit
fortlaufender Nummer 0 bis F (danach wieder 0). Der letzte Frame ist aufgefüllt und
wird auf die Länge gekürzt. Alle anderen Zeilen sind je eine vollständige Nachricht;
sie dürfen auch zwischen den Frames einer mehrteiligen Nachricht stehen (Einzel-Frame
eines anderen Steuergeräts).

Senden zwei Steuergeräte gleichzeitig mehrteilige Nachrichten, mischt der ELM327 ohne
Header deren Frames (Datenblatt ELM327DS, „Multiline Responses“, Beispiel ``09 04``);
eine Zuordnung ist dann unmöglich. Das wird an Lücken in der Frame-Nummerierung und an
unvollständigen Nachrichten erkannt und als ``FrameSequenceError`` (ein ``ValueError``)
gemeldet, statt falsche Daten zu liefern. Die Dienste wiederholen die Anfrage dann mit
Headern (``ATH1``) und ordnen die Frames über die CAN-ID zu (``protocol.headers``).
"""

import re

_BYTE_COUNT = re.compile(r"^[0-9A-F]{3}$")
_FRAME = re.compile(r"^([0-9A-F]):\s*(.*)$")


class FrameSequenceError(ValueError):
    """Frames einer mehrteiligen Nachricht fehlen, sind vertauscht oder vermischt.

    Ohne Header nicht zu unterscheiden: verlorene Frames oder gleichzeitige mehrteilige
    Antworten mehrerer Steuergeräte. Mit Headern (``ATH1``) lässt sich das klären.
    """


def _hex(text: str) -> bytes:
    try:
        return bytes.fromhex(text.replace(" ", ""))
    except ValueError:
        raise ValueError(f"keine Hex-Daten: {text!r}") from None


class _Collector:
    """Sammelt Nachrichten; höchstens eine mehrteilige ist gleichzeitig offen."""

    def __init__(self) -> None:
        self.messages: list[bytearray] = []
        self.lengths: list[int | None] = []
        self.open: int | None = None  # Index der offenen mehrteiligen Nachricht
        self.next_seq = 0

    def add(self, data: bytes, length: int | None) -> int:
        self.messages.append(bytearray(data))
        self.lengths.append(length)
        return len(self.messages) - 1

    def complete(self, index: int) -> bool:
        length = self.lengths[index]
        return length is not None and len(self.messages[index]) >= length

    def close(self) -> None:
        """Schließt die offene Nachricht; fehlen Frames, ist die Antwort unbrauchbar."""
        index = self.open
        if index is not None and self.lengths[index] is not None and not self.complete(index):
            raise FrameSequenceError(
                f"mehrteilige Nachricht unvollständig ({len(self.messages[index])} von "
                f"{self.lengths[index]} Bytes): Frames fehlen oder stammen von mehreren "
                "Steuergeräten"
            )
        self.open = None

    def frame(self, seq: int, data: bytes) -> None:
        index = self.open
        if (
            index is not None
            and seq == 0
            and self.messages[index]
            and (self.lengths[index] is None or self.complete(index))
        ):
            index = None  # nächste Nachricht ohne Längenzeile (ELM327-emulator)
        if index is not None and self.complete(index):
            raise FrameSequenceError(f"Frame {seq:X} nach vollständiger Nachricht")
        if index is None:
            index = self.open = self.add(b"", None)
            self.next_seq = 0
        if seq != self.next_seq:
            raise FrameSequenceError(
                f"Frame {seq:X} statt {self.next_seq:X}; Antworten mehrerer Steuergeräte "
                "vermischt? Nur mit Headern (ATH1) zuzuordnen"
            )
        self.messages[index] += data
        self.next_seq = (seq + 1) % 16


def split_messages(response: str) -> list[bytes]:
    """Liefert die Nutzdaten jeder Nachricht in ``response``.

    Wirft ``ValueError`` bei Zeilen, die keine Hex-Daten sind, und
    ``FrameSequenceError`` (Unterklasse von ``ValueError``) bei mehrteiligen Nachrichten
    mit fehlenden, vertauschten oder vermischten Frames.
    """
    collector = _Collector()
    for raw in response.upper().splitlines():
        line = raw.strip()
        if not line:
            continue
        if _BYTE_COUNT.match(line):
            collector.close()
            collector.open = collector.add(b"", int(line, 16))
            collector.next_seq = 0
            continue
        frame = _FRAME.match(line)
        if frame is None:
            collector.add(_hex(line), None)
        else:
            collector.frame(int(frame.group(1), 16), _hex(frame.group(2)))
    collector.close()
    return [
        bytes(data if length is None else data[:length])
        for data, length in zip(collector.messages, collector.lengths, strict=True)
    ]
