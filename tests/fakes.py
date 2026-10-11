import re
from collections.abc import Mapping, Sequence
from types import TracebackType
from typing import Self

from obd_diag.data.dtc_catalog import DtcInfo
from obd_diag.transport import TransportTimeout

# Antworten eines CAN-Fahrzeugs (ATH0, ATS0) mit drei gespeicherten Codes, davon
# einer zusätzlich ausstehend; Mode 0A ohne Codes.
CAN_CAR = {
    "ATZ": "ELM327 v1.5",
    "ATRV": "12.4V",
    "0100": "SEARCHING...\r4100BE3FA813",
    "ATDPN": "A6",
    "ATDP": "AUTO, ISO 15765-4 (CAN 11/500)",
    "03": "008\r0:430301330300\r1:01710000000000",
    "07": "47010133",
    "0A": "NO DATA",
}

# Dasselbe Fahrzeug mit Motor aus und Zündung an, bereit zum Löschen: Freeze Frame zu
# P0133, Mode 04 wird bestätigt.
CAN_CAR_ENGINE_OFF = {
    **CAN_CAR,
    "010C": "410C0000",
    "020200": "4202000133",
    "020400": "NO DATA",
    "020500": "42050073",
    "020C00": "420C001AF8",
    "020D00": "7F0212",
    "04": "44",
}

# Für die vollständige Diagnose zusätzlich Readiness (MIL an, 3 Codes, Katalysator und
# Lambdasonde offen), Readiness dieser Fahrt und FIN WVWZZZ1KZ6W123456 (CAN, mehrteilig).
CAN_CAR_FULL = {
    **CAN_CAR_ENGINE_OFF,
    "0101": "4101 8307 6521",
    # Readiness dieser Fahrt (PID 41): Katalysator freigegeben und noch offen,
    # Tankentlüftung ausgesetzt
    "0141": "4141 0007 6101",
    "0902": "014\r0:490201575657\r1:5A5A5A314B5A36\r2:57313233343536",
    # Mode 09 (außer der FIN) und Mode 06 unterstützt dieses Fahrzeug nicht; mit Headern
    # antworten sie, wenn ``headers_on`` ECU_INFO_HEADERS bzw. MONITOR_TESTS_HEADERS hat
    "0900": "NO DATA",
    "0600": "NO DATA",
}

# Adapter-Check (nur Adapter-Befehle): ein echter ELM327 v2.1 mit den Antwortformaten des
# Datenblatts (ATI S. 18, AT@1 S. 28, ATPPS S. 68, ATIGN S. 19) und ein Nachbau wie der
# FORScan-ELMconfig-Adapter, der v1.5 meldet und ATAMC (v2.0) nicht kennt.
ADAPTER_PPS_DEFAULT = (
    "00:FF F 01:FF F 02:FF F 03:32 F\r04:01 F 05:FF F 06:F1 F 07:09 F\r"
    "2C:E0 F 2D:04 F 2E:80 F 2F:0A F"
)
ADAPTER_V21 = {
    "ATZ": "\r\rELM327 v2.1",
    "ATI": "ELM327 v2.1",
    "AT@1": "OBDII to RS232 Interpreter",
    "ATRV": "12.6V",
    "ATPPS": ADAPTER_PPS_DEFAULT,
    "ATIGN": "ON",
    "ATRD": "FF",
    "ATAMC": "0A",
}
ADAPTER_V15 = {**ADAPTER_V21, "ATZ": "ELM327 v1.5", "ATI": "ELM327 v1.5", "ATAMC": "?"}


def can_lines(header: str, payload: str) -> str:
    """Antwortzeilen mit Header (``ATH1``, Leerzeichen) einer Nachricht nach ISO 15765-2:
    Einzel-Frame bis 7 Bytes, sonst erster Frame und Folge-Frames."""
    data = bytes.fromhex(payload)
    if len(data) <= 7:
        frames = [bytes([len(data)]) + data]
    else:
        frames = [bytes([0x10 | len(data) >> 8, len(data) & 0xFF]) + data[:6]]
        for n, i in enumerate(range(6, len(data), 7), 1):
            frames.append(bytes([0x20 | n % 16]) + data[i : i + 7])
    return "\r".join(f"{header} {frame.hex(' ').upper()}" for frame in frames)


def _text(text: str, size: int) -> str:
    return text.encode("ascii").ljust(size, b"\0").hex()


# Mode 09 eines CAN-Fahrzeugs (11 Bit) mit Motor (7E8) und Getriebe (7E9), nur mit Headern
# (``FakeTransport(headers_on=...)``). 0904 ist das Beispiel aus dem Datenblatt ELM327DSJ
# S. 43, wörtlich (CALID 56028949AC und 56028954AC). Unterstützt: 7E8 02/04/06/08/0A,
# 7E9 04/06/0A. Überwachungszähler (Otto, 20 Werte) des Motors: OBDCOND 20, IGNCNTR 50,
# dann Katalysator Bank 1 15 von 20, Bank 2 0 von 0, Lambdasonde 18 von 20 usw.
IPT_SPARK = (20, 50, 15, 20, 0, 0, 18, 20, 0, 0, 5, 20, 0, 0, 3, 19, 17, 20, 0, 0)
ECU_INFO_HEADERS = {
    "0900": can_lines("7E8", "490055400000") + "\r" + can_lines("7E9", "490014400000"),
    "0904": (
        "7E8 10 13 49 04 01 35 36 30\r7E8 21 32 38 39 34 39 41 43\r"
        "7E9 10 13 49 04 01 35 36 30\r7E8 22 00 00 00 00 00 00 31\r"
        "7E9 21 32 38 39 35 34 41 43\r7E9 22 00 00 00 00 00 00 00"
    ),
    "0906": can_lines("7E8", "49060112 34AB CD") + "\r" + can_lines("7E9", "4906010000 0F3A"),
    "0908": can_lines("7E8", "490814" + "".join(f"{v:04X}" for v in IPT_SPARK)),
    "090A": can_lines("7E8", "490A01" + _text("ECM\0-EngineControl", 20))
    + "\r"
    + can_lines("7E9", "490A01" + _text("TCM\0-TransmissionCtl", 20)),
}

# Mode 06 desselben Fahrzeugs, nur mit Headern: MIDs 01, 02 (Lambdasonde Bank 1) und 21
# (Katalysator Bank 1). 0601 mit zwei Tests (genormte TIDs 01 und 05), 0602 lehnt das
# Steuergerät ab, 0621 liegt außerhalb der Grenzen.
MONITOR_TESTS_HEADERS = {
    "0600": can_lines("7E8", "4600C0000001"),
    "0620": can_lines("7E8", "462080000000"),
    "0601": can_lines(
        "7E8",
        "46" + "01010A" + "0B00" + "0900" + "0D00" + "010510" + "0050" + "0000" + "0064",
    ),
    "0602": can_lines("7E8", "7F0612"),
    "0621": can_lines("7E8", "4621" + "8024" + "00F0" + "0000" + "00C8"),
}

# Antworten, die nach bestätigtem Mode 04 gelten (``FakeTransport(after_clear=...)``)
CLEARED = {"03": "4300", "07": "4700", "020200": "4202000000"}


# „@“ nur in AT@1 (Gerätebeschreibung, Datenblatt S. 28)
WIRE_FORMAT = re.compile(rb"(?:[0-9A-Z]+|AT@1)\r")


class FakeTransport:
    """Spielt vorbereitete Adapter-Antworten ab und merkt sich gesendete Befehle.

    Unbekannte Befehle beantwortet der Fake mit ``OK``. Sobald ``04`` mit ``44``
    bestätigt ist, gelten zusätzlich die Antworten aus ``after_clear``. Nach ``ATH1``
    (bis ``ATH0``) gelten zuerst die Antworten aus ``headers_on``. ``later`` liefert je
    Befehl weitere Antworten, die ohne erneutes Senden gelesen werden
    (``Elm327.read_more``); ist nichts mehr da, wirft ``read_until``
    ``TransportTimeout``.
    """

    def __init__(
        self,
        responses: Mapping[str, str],
        after_clear: Mapping[str, str] | None = None,
        *,
        headers_on: Mapping[str, str] | None = None,
        later: Mapping[str, Sequence[str]] | None = None,
    ) -> None:
        self.responses = responses
        self.after_clear = after_clear or {}
        self.headers_on = headers_on or {}
        self.later = later or {}
        self.headers = False
        self.cleared = False
        self.sent: list[str] = []
        self.reads = 0
        self._pending = b""
        self._queue: list[bytes] = []

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def write(self, data: bytes) -> None:
        # Jeder Befehl auf der Leitung: ASCII-Großbuchstaben/Ziffern und genau ein CR.
        assert WIRE_FORMAT.fullmatch(data), f"falsches Befehlsformat: {data!r}"
        cmd = data.decode("ascii").strip()
        self.sent.append(cmd)
        if cmd.upper() in ("ATH0", "ATH1"):
            self.headers = cmd.upper() == "ATH1"
        if self.headers and cmd in self.headers_on:
            response = self.headers_on[cmd]
        elif self.cleared and cmd in self.after_clear:
            response = self.after_clear[cmd]
        else:
            response = self.responses.get(cmd, "OK")
        self._queue = [f"{r}\r\r>".encode("ascii") for r in self.later.get(cmd, ())]
        if cmd == "04" and any(
            r.replace(" ", "").startswith("44") for r in (response, *self.later.get(cmd, ()))
        ):
            self.cleared = True
        self._pending = f"{response}\r\r>".encode("ascii")

    def read_until(self, terminator: bytes, timeout: float) -> bytes:
        self.reads += 1
        if not self._pending and self._queue:
            self._pending = self._queue.pop(0)
        if not self._pending:
            raise TransportTimeout(f"keine Antwort nach {timeout} s")
        data, self._pending = self._pending, b""
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


class FakeCatalog:
    """Katalog mit festen Einträgen; merkt sich die Abfragen."""

    def __init__(self, titles: dict[str, str]) -> None:
        self.titles = titles
        self.lookups: list[tuple[str, str]] = []

    def lookup(self, code: str, lang: str = "de") -> DtcInfo | None:
        self.lookups.append((code, lang))
        title = self.titles.get(code)
        return None if title is None else DtcInfo(code, title)

    def close(self) -> None:
        pass
