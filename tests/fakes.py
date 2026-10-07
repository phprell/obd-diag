from types import TracebackType
from typing import Self


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
