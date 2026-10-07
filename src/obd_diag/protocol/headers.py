"""Antworten mit Headern (``ATH1``) zerlegen und je Steuergerät zusammensetzen.

Ohne Header kann der ELM327 gleichzeitige mehrteilige Antworten mehrerer Steuergeräte
nicht trennen (Datenblatt ELM327DS, „Multiline Responses“, S. 45). Mit Headern trägt
jede Zeile ihren Absender, und die Frames lassen sich je Absender nach ISO 15765-2
(ISO-TP) zusammensetzen. Formate einer Zeile (mit oder ohne Leerzeichen):

- CAN 11 Bit: ``7E8 10 0A 43 04 01 33 03 00``: 3 Hex-Ziffern CAN-ID, dann das
  CAN-Datenfeld samt PCI-Byte(s).
- CAN 29 Bit: ``18 DA F1 10 06 41 00 BE 3F A8 13``: 4 Bytes CAN-ID (OBD-Antworten
  ``18 DA F1 <Quelle>``), dann das Datenfeld.
- J1850/ISO 9141/KWP: ``48 6B 10 41 00 BE 3E B8 11 FA``: 3 Header-Bytes (Priorität,
  Ziel, Quelle), Nutzdaten, 1 Prüfsummen-Byte (Datenblatt S. 44). Die Prüfsumme wird
  entfernt, aber nicht geprüft (J1850 nutzt eine CRC, ISO 9141/KWP eine Summe; der
  ELM327 verwirft fehlerhafte Nachrichten ohnehin bzw. meldet ``<DATA ERROR``).

PCI-Byte (ISO 15765-2): ``0L`` Einzel-Frame mit L Bytes; ``1L LL`` erster Frame mit
12-Bit-Länge, dann 6 Bytes; ``2N`` Folge-Frame Nummer N (1, 2, … F, 0, …) mit bis zu
7 Bytes; ``3x`` Flusssteuerung (sendet nur der Tester, wird übersprungen). Füllbytes
nach dem Ende einer Nachricht werden abgeschnitten.

Reihenfolge des Ergebnisses: nach Steuergeräte-Adresse (``EcuMessage.ecu``), bei
gleicher Adresse in der Reihenfolge, in der die Nachrichten (ihr erster Frame)
eingetroffen sind. Ohne Header gilt dagegen die Eingangsreihenfolge.
"""

import re
from dataclasses import dataclass
from enum import StrEnum


class HeaderFormat(StrEnum):
    CAN_11 = "CAN 11 Bit"
    CAN_29 = "CAN 29 Bit"
    LEGACY = "J1850/ISO 9141/KWP"

    @property
    def is_can(self) -> bool:
        return self is not HeaderFormat.LEGACY


@dataclass(frozen=True)
class EcuMessage:
    """Eine vollständige Nachricht eines Steuergeräts."""

    ecu: int  # Adresse: 11 Bit die CAN-ID (7E8), 29 Bit/Legacy die Quelladresse (10)
    header: str  # Absender wie empfangen, ohne Leerzeichen: "7E8", "18DAF110", "486B10"
    data: bytes  # Nutzdaten ohne Header, PCI, Füllung und Prüfsumme


_HEX = re.compile(r"^[0-9A-F]+$")
_MAX_SF = 7  # Nutzdaten eines Einzel-Frames bei klassischem CAN


def _classify(line: str, can: bool | None) -> tuple[HeaderFormat, str, bytes]:
    """Format, Header (ohne Leerzeichen) und Rest einer Zeile; ``ValueError`` sonst."""
    tokens = line.split()
    compact = "".join(tokens)
    if not compact or not _HEX.match(compact):
        raise ValueError(f"keine Hex-Daten: {line!r}")
    eleven = len(tokens[0]) == 3 if len(tokens) > 1 else len(compact) % 2 == 1
    if eleven:
        if can is False:
            raise ValueError(f"CAN-Header bei Nicht-CAN-Protokoll: {line!r}")
        fmt, size = HeaderFormat.CAN_11, 3
    elif len(compact) % 2:
        raise ValueError(f"ungerade Zahl von Hex-Ziffern: {line!r}")
    elif can is True or (can is None and compact.startswith("18DA")):
        fmt, size = HeaderFormat.CAN_29, 8
    else:
        fmt, size = HeaderFormat.LEGACY, 6
    rest = compact[size:]
    if len(rest) % 2:
        raise ValueError(f"ungerade Zahl von Hex-Ziffern nach dem Header: {line!r}")
    return fmt, compact[:size], bytes.fromhex(rest)


def header_format(response: str, *, can: bool | None = None) -> HeaderFormat | None:
    """Format der ersten Zeile einer Antwort mit Headern; ``None``, wenn unlesbar."""
    for line in response.upper().splitlines():
        if line.strip():
            try:
                return _classify(line, can)[0]
            except ValueError:
                return None
    return None


def _ecu(fmt: HeaderFormat, header: str) -> int:
    if fmt is HeaderFormat.CAN_11:
        return int(header, 16)
    return int(header[-2:], 16)  # 29 Bit: Quelle im letzten Byte; Legacy: drittes Byte


class _Open:
    """Eine mehrteilige Nachricht im Aufbau."""

    def __init__(self, index: int, length: int, data: bytes) -> None:
        self.index = index
        self.length = length
        self.data = bytearray(data)
        self.next_seq = 1

    @property
    def complete(self) -> bool:
        return len(self.data) >= self.length


def parse_header_response(response: str, *, can: bool | None = None) -> list[EcuMessage]:
    """Zerlegt eine Antwort mit Headern in vollständige Nachrichten je Steuergerät.

    ``can``: ``True``/``False``, wenn das Protokoll bekannt ist, ``None`` erkennt das
    Format je Zeile an der Form (siehe Modul-Doku). Wirft ``ValueError`` bei Zeilen
    ohne Hex-Daten, ungültigen PCI-Bytes, Folge-Frames ohne ersten Frame, Lücken in der
    Nummerierung und unvollständigen Nachrichten.
    """
    found: list[tuple[int, int, str, bytearray]] = []  # (ecu, Eingang, Header, Daten)
    lengths: list[int | None] = []
    opened: dict[str, _Open] = {}
    for raw in response.upper().splitlines():
        line = raw.strip()
        if not line:
            continue
        fmt, header, frame = _classify(line, can)
        ecu = _ecu(fmt, header)
        if fmt is HeaderFormat.LEGACY:
            if len(frame) < 2:
                raise ValueError(f"Nachricht ohne Daten: {line!r}")
            found.append((ecu, len(found), header, bytearray(frame[:-1])))
            lengths.append(None)
            continue
        if not frame:
            raise ValueError(f"CAN-Frame ohne Daten: {line!r}")
        kind, low = frame[0] >> 4, frame[0] & 0xF
        current = opened.get(header)
        if kind == 0:
            if not 1 <= low <= _MAX_SF or len(frame) < 1 + low:
                raise ValueError(f"ungültiger Einzel-Frame: {line!r}")
            if current is not None and not current.complete:
                raise ValueError(f"Einzel-Frame von {header} vor Ende der mehrteiligen Nachricht")
            found.append((ecu, len(found), header, bytearray(frame[1 : 1 + low])))
            lengths.append(None)
        elif kind == 1:
            if len(frame) < 2:
                raise ValueError(f"erster Frame ohne Länge: {line!r}")
            length = low << 8 | frame[1]
            if length <= _MAX_SF:
                raise ValueError(f"erster Frame mit Länge {length}: {line!r}")
            if current is not None and not current.complete:
                raise ValueError(f"neue Nachricht von {header} vor Ende der vorigen")
            opened[header] = _Open(len(found), length, frame[2:])
            found.append((ecu, len(found), header, opened[header].data))
            lengths.append(length)
        elif kind == 2:
            if current is None or current.complete:
                raise ValueError(f"Folge-Frame ohne ersten Frame von {header}: {line!r}")
            if low != current.next_seq:
                raise ValueError(
                    f"Folge-Frame {low:X} statt {current.next_seq:X} von {header}: "
                    "Frames fehlen oder sind vertauscht"
                )
            current.data += frame[1:]
            current.next_seq = (current.next_seq + 1) % 16
        elif kind != 3:
            raise ValueError(f"unbekanntes PCI-Byte {frame[0]:02X}: {line!r}")
    for header, current in opened.items():
        if not current.complete:
            raise ValueError(
                f"mehrteilige Nachricht von {header} unvollständig "
                f"({len(current.data)} von {current.length} Bytes)"
            )
    messages = [
        EcuMessage(ecu, header, bytes(data if length is None else data[:length]))
        for (ecu, _, header, data), length in zip(found, lengths, strict=True)
    ]
    order = sorted(range(len(found)), key=lambda i: (found[i][0], found[i][1]))
    return [messages[i] for i in order]
