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


# --- Antworten mit Headern (ATH1), ISO 15765-2 / ELM327DS S. 44-46 ---


@dataclass(frozen=True)
class Frame:
    """Ein CAN-Frame eines Steuergeräts, wie der ELM327 ihn mit und ohne Header zeigt."""

    can_id: str  # "7E8" oder "18DAF110"
    data: tuple[int, ...]  # CAN-Datenfeld mit PCI-Byte(s), ggf. aufgefüllt
    off_lines: tuple[str, ...]  # Darstellung ohne Header (ATH0, CAF1)


def isotp_frames(
    can_id: str, payload: Sequence[int], spaces: bool, pad: int, pad_single: bool = False
) -> list[Frame]:
    """Zerlegt ``payload`` in ISO-TP-Frames: SF ``0L``, FF ``1L LL`` + 6, CF ``2N`` + 7.

    Mit ``pad_single`` hat auch ein Einzel-Frame 8 Bytes (DLC 8, aufgefüllt), sonst nur
    so viele wie nötig; Folge-Frames sind immer aufgefüllt.
    Ohne Header zeigt der ELM327 einen Einzel-Frame nur mit seinen Nutzdaten, einen
    ersten Frame als Längenzeile ``LLL`` plus ``0:`` und Folge-Frames als ``N:``.
    """
    sep = ": " if spaces else ":"
    if len(payload) <= 7:
        data = (len(payload), *payload)
        if pad_single:
            data += (pad,) * (8 - len(data))
        return [Frame(can_id, data, (_hex(payload, spaces),))]
    frames = [
        Frame(
            can_id,
            (0x10 | len(payload) >> 8, len(payload) & 0xFF, *payload[:6]),
            (f"{len(payload):03X}", "0" + sep + _hex(payload[:6], spaces)),
        )
    ]
    rest = list(payload[6:])
    seq = 1
    while rest:
        chunk, rest = rest[:7], rest[7:]
        chunk += [pad] * (7 - len(chunk))
        frames.append(
            Frame(can_id, (0x20 | seq % 16, *chunk), (f"{seq % 16:X}{sep}{_hex(chunk, spaces)}",))
        )
        seq += 1
    return frames


def header_line(frame: Frame, spaces: bool) -> str:
    """Zeile mit Header: ``7E8 10 0A 43 …`` bzw. ``18 DA F1 10 10 0A …`` (ATH1)."""
    eleven = len(frame.can_id) == 3
    head = frame.can_id if eleven else _hex(bytes.fromhex(frame.can_id), spaces)
    return head + (" " if spaces else "") + _hex(frame.data, spaces)


def mix_frames(ecus: Sequence[Sequence[Frame]], rng: random.Random) -> list[Frame]:
    """Mischt die Frames mehrerer Steuergeräte; je Steuergerät bleibt die Reihenfolge."""
    queues = [list(frames) for frames in ecus]
    out: list[Frame] = []
    while any(queues):
        queue = rng.choice([q for q in queues if q])
        out.append(queue.pop(0))
    return out


class HeaderRawTransport(RawTransport):
    """Wie ``RawTransport``; nach ``ATH1`` (bis ``ATH0``) gelten ``headers_on``."""

    def __init__(self, responses: Mapping[str, bytes], headers_on: Mapping[str, bytes]) -> None:
        super().__init__(responses)
        self.headers_on = headers_on
        self.headers = False

    def write(self, data: bytes) -> None:
        cmd = data.decode("ascii").strip()
        if cmd in ("ATH0", "ATH1"):
            self.headers = cmd == "ATH1"
        if self.headers and cmd in self.headers_on:
            self.sent.append(cmd)
            self._buffer += self.headers_on[cmd]
        else:
            super().write(data)
