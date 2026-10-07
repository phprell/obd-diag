"""Gemeinsame Schnittstelle aller Transporte.

Jede Implementierung (USB-Seriell, Bluetooth, Emulator, Test-Fake) erfüllt dieses
Protocol. Höhere Schichten kennen nur ``Transport`` und laufen damit ohne Auto.
"""

from types import TracebackType
from typing import Protocol, Self


class TransportError(Exception):
    """Verbindung zum Adapter fehlgeschlagen oder abgebrochen."""


class TransportTimeout(TransportError):
    """Der Adapter hat nicht rechtzeitig geantwortet."""


class Transport(Protocol):
    def open(self) -> None: ...

    def close(self) -> None: ...

    def write(self, data: bytes) -> None: ...

    def read_until(self, terminator: bytes, timeout: float) -> bytes:
        """Liest bis einschließlich ``terminator``; wirft ``TransportTimeout`` sonst."""
        ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...
