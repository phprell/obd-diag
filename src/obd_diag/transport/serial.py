"""USB-Seriell-Transport über pyserial (z. B. /dev/ttyUSB0)."""

from types import TracebackType
from typing import Self

import serial

from obd_diag.transport.base import TransportError, TransportTimeout


class SerialTransport:
    def __init__(self, port: str, baudrate: int = 38400) -> None:
        self.port = port
        self.baudrate = baudrate
        self._serial: serial.Serial | None = None

    def open(self) -> None:
        try:
            self._serial = serial.Serial(self.port, self.baudrate, timeout=1)
        except serial.SerialException as e:
            raise TransportError(f"{self.port} lässt sich nicht öffnen ({e.strerror or e})") from e

    def close(self) -> None:
        if self._serial is not None:
            self._serial.close()
            self._serial = None

    def write(self, data: bytes) -> None:
        port = self._port()
        # Reste einer früheren Antwort (z. B. ein zweiter Prompt nach STOPPED) würden
        # sonst als Antwort auf diesen Befehl gelesen und alles Weitere verschieben.
        try:
            port.reset_input_buffer()
            port.write(data)
        except (serial.SerialException, OSError) as e:
            raise self._lost(e) from e

    def read_until(self, terminator: bytes, timeout: float) -> bytes:
        port = self._port()
        try:
            port.timeout = timeout
            data = port.read_until(terminator)
        except (serial.SerialException, OSError) as e:
            raise self._lost(e) from e
        if not data.endswith(terminator):
            raise TransportTimeout(f"keine Antwort von {self.port} nach {timeout} s")
        return bytes(data)

    def _lost(self, error: Exception) -> TransportError:
        """Ein-/Ausgabefehler (z. B. Adapter abgezogen) als verständlicher TransportError."""
        reason = (error.strerror if isinstance(error, OSError) else None) or str(error)
        return TransportError(
            f"Verbindung zu {self.port} unterbrochen ({reason}). Adapter abgezogen?"
        )

    def _port(self) -> serial.Serial:
        if self._serial is None:
            raise TransportError("Transport ist nicht geöffnet")
        return self._serial

    def __enter__(self) -> Self:
        self.open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
