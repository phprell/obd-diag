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


class FakeTransport:
    """Spielt vorbereitete Adapter-Antworten ab und merkt sich gesendete Befehle."""

    def __init__(self, responses: dict[str, str]) -> None:
        self.responses = responses
        self.sent: list[str] = []
        self._pending = b""

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def write(self, data: bytes) -> None:
        cmd = data.decode("ascii").strip()
        self.sent.append(cmd)
        self._pending = f"{self.responses.get(cmd, 'OK')}\r\r>".encode("ascii")

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
