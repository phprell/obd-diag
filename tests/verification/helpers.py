"""Hilfen für die Verifikation: byte-genauer Fake-Adapter und unabhängiger Encoder.

Der Encoder erzeugt Adapter-Text nach den Regeln des ELM327-Datenblatts (ELM327DSI,
S. 32 und 44 bis 46) und ISO 15765-2, ohne Code aus ``obd_diag.protocol`` zu verwenden.
"""

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import TracebackType
from typing import Self

from obd_diag.transport import TransportTimeout

# Datenblatt S. 32: erste Hex-Ziffer -> erste zwei Zeichen des Codes
_FIRST_DIGIT = [f"{system}{n}" for system in "PCBU" for n in range(4)]

Dtc = tuple[int, int]  # die zwei Rohbytes eines Codes


def dtc_text(dtc: Dtc) -> str:
    """Code nach der Tabelle im Datenblatt, z. B. (0xD0, 0x16) -> U1016."""
    digits = f"{dtc[0]:02X}{dtc[1]:02X}"
    return _FIRST_DIGIT[int(digits[0], 16)] + digits[1:]


class RawTransport:
    """Spielt Rohbytes je Befehl ab und liest wie ein serieller Port bis zum Prompt.

    Was nach dem ersten Prompt kommt, bleibt im Puffer und wird beim nächsten Lesen
    geliefert, genau wie bei pyserial.
    """

    def __init__(self, responses: Mapping[str, bytes]) -> None:
        self.responses = responses
        self.sent: list[str] = []
        self._buffer = b""

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def write(self, data: bytes) -> None:
        cmd = data.decode("ascii").strip()
        self.sent.append(cmd)
        self._buffer += self.responses.get(cmd, b"OK\r\r>")

    def read_until(self, terminator: bytes, timeout: float) -> bytes:
        end = self._buffer.find(terminator)
        if end < 0:
            raise TransportTimeout("kein Prompt")
        end += len(terminator)
        data, self._buffer = self._buffer[:end], self._buffer[end:]
        return data

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        pass


@dataclass(frozen=True)
class Message:
    """Eine Nachricht eines Steuergeräts: ihre Zeilen und die enthaltenen Codes."""

    lines: tuple[str, ...]
    codes: tuple[str, ...]
    multi: bool  # mehrteilig (Längenzeile + Frames)


def _hex(data: Sequence[int], spaces: bool) -> str:
    return (" " if spaces else "").join(f"{b:02X}" for b in data)


def legacy_messages(mode: int, dtcs: Sequence[Dtc], spaces: bool) -> list[Message]:
    """J1850/ISO 9141/KWP: je Zeile Mode-Byte + 3 Codes, mit 00 auf 6 Bytes aufgefüllt.

    Ohne Codes antwortet das Steuergerät mit einer Zeile voller 00 (Datenblatt S. 32).
    """
    groups = [list(dtcs[i : i + 3]) for i in range(0, len(dtcs), 3)] or [[]]
    messages = []
    for group in groups:
        data = [b for dtc in group for b in dtc]
        data += [0] * (6 - len(data))
        line = _hex([0x40 + mode, *data], spaces)
        messages.append(Message((line,), tuple(dtc_text(d) for d in group), False))
    return messages


def can_message(mode: int, dtcs: Sequence[Dtc], spaces: bool, pad: int) -> Message:
    """ISO 15765-4: Mode-Byte, Zählbyte, Codes; ab 8 Bytes mehrteilig wie der ELM327 (ATH0).

    Einzel-Frame: der ELM327 zeigt mit CAF1 genau die Nutzdaten (ohne PCI und Füllung).
    Mehrteilig: Längenzeile ``LLL`` (3 Hex-Ziffern), dann ``0:`` mit 6 Bytes und Folgeframes
    ``1:`` … ``F:``, ``0:`` … mit je 7 Bytes; der letzte ist mit ``pad`` aufgefüllt.
    """
    payload = [0x40 + mode, len(dtcs), *(b for dtc in dtcs for b in dtc)]
    codes = tuple(dtc_text(d) for d in dtcs)
    if len(payload) <= 7:
        return Message((_hex(payload, spaces),), codes, False)
    sep = ": " if spaces else ":"
    lines = [f"{len(payload):03X}", "0" + sep + _hex(payload[:6], spaces)]
    rest = payload[6:]
    seq = 1
    while rest:
        chunk, rest = rest[:7], rest[7:]
        chunk += [pad] * (7 - len(chunk))
        lines.append(f"{seq % 16:X}{sep}{_hex(chunk, spaces)}")
        seq += 1
    return Message(tuple(lines), codes, True)


def interleave(messages: Sequence[Message], rng: random.Random) -> tuple[list[str], list[str]]:
    """Mischt die Zeilen mehrerer Steuergeräte so, wie es ohne Header eindeutig bleibt.

    Mehrteilige Nachrichten folgen nacheinander; einteilige dürfen an beliebiger Stelle
    stehen, auch zwischen den Frames einer mehrteiligen. Liefert die Zeilen und die
    erwarteten Codes in der Reihenfolge, in der die Nachrichten beginnen.
    """
    multi = [m for m in messages if m.multi]
    single = [m for m in messages if not m.multi]
    rng.shuffle(multi)
    rng.shuffle(single)
    base: list[tuple[str, int]] = []  # (Zeile, Index der Nachricht oder -1 für Folgezeile)
    order: list[Message] = []
    for m in multi:
        order.append(m)
        base.append((m.lines[0], len(order) - 1))
        base.extend((line, -1) for line in m.lines[1:])
    for m in single:
        order.append(m)
        base.insert(rng.randint(0, len(base)), (m.lines[0], len(order) - 1))
    lines = [line for line, _ in base]
    starts = [index for _, index in base if index >= 0]
    codes = [code for index in starts for code in order[index].codes]
    return lines, codes


def adapter_output(
    lines: Sequence[str],
    *,
    echo: str | None = None,
    status: str | None = None,
    blank_lines: Sequence[int] = (),
    newline: str = "\r",
) -> bytes:
    """Setzt Zeilen zur Byte-Folge des Adapters zusammen (mit ``\\r\\r>`` am Ende)."""
    out = list(lines)
    for pos in sorted(blank_lines, reverse=True):
        out.insert(min(pos, len(out)), "")
    if status is not None:
        out.insert(0, status)
    if echo is not None:
        out.insert(0, echo)
    return ("".join(line + newline for line in out) + newline + ">").encode("ascii")
