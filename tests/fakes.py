from collections.abc import Mapping
from types import TracebackType
from typing import Self

from obd_diag.data.dtc_catalog import DtcInfo

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
# Lambdasonde offen) und FIN WVWZZZ1KZ6W123456 (CAN, mehrteilig).
CAN_CAR_FULL = {
    **CAN_CAR_ENGINE_OFF,
    "0101": "4101 8307 6521",
    "0902": "014\r0:490201575657\r1:5A5A5A314B5A36\r2:57313233343536",
}

# Antworten, die nach bestätigtem Mode 04 gelten (``FakeTransport(after_clear=...)``)
CLEARED = {"03": "4300", "07": "4700", "020200": "4202000000"}


class FakeTransport:
    """Spielt vorbereitete Adapter-Antworten ab und merkt sich gesendete Befehle.

    Unbekannte Befehle beantwortet der Fake mit ``OK``. Sobald ``04`` mit ``44``
    bestätigt ist, gelten zusätzlich die Antworten aus ``after_clear``.
    """

    def __init__(
        self, responses: Mapping[str, str], after_clear: Mapping[str, str] | None = None
    ) -> None:
        self.responses = responses
        self.after_clear = after_clear or {}
        self.cleared = False
        self.sent: list[str] = []
        self._pending = b""

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def write(self, data: bytes) -> None:
        cmd = data.decode("ascii").strip()
        self.sent.append(cmd)
        if self.cleared and cmd in self.after_clear:
            response = self.after_clear[cmd]
        else:
            response = self.responses.get(cmd, "OK")
        if cmd == "04" and response.replace(" ", "").startswith("44"):
            self.cleared = True
        self._pending = f"{response}\r\r>".encode("ascii")

    def read_until(self, terminator: bytes, timeout: float) -> bytes:
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
