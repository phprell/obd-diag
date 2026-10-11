"""USB-Seriell-Transport über pyserial (z. B. /dev/ttyUSB0)."""

import contextlib
import termios
from types import TracebackType
from typing import Self

import serial

from obd_diag.i18n import tr
from obd_diag.transport.base import TransportError, TransportTimeout

# Fehler, die ein verschwundener Adapter auslöst: pyserial meldet sie je nach Aufruf als
# SerialException, OSError oder, beim Leeren des Puffers und beim Umstellen des
# Timeouts (tcflush, tcsetattr), als termios.error, das kein OSError ist.
_IO_ERRORS = (serial.SerialException, OSError, termios.error)


class SerialTransport:
    def __init__(self, port: str, baudrate: int = 38400) -> None:
        self.port = port
        self.baudrate = baudrate
        self._serial: serial.Serial | None = None

    def open(self) -> None:
        try:
            self._serial = serial.Serial(self.port, self.baudrate, timeout=1)
        except serial.SerialException as e:
            raise TransportError(
                tr("{port} lässt sich nicht öffnen ({reason})").format(
                    port=self.port, reason=e.strerror or e
                )
            ) from e

    def close(self) -> None:
        if self._serial is not None:
            port, self._serial = self._serial, None
            # Ist der Adapter abgezogen, kann schon das Schließen scheitern. Das darf die
            # eigentliche Meldung (``TransportError`` aus read/write) nicht verdecken.
            with contextlib.suppress(*_IO_ERRORS):
                port.close()

    def write(self, data: bytes) -> None:
        port = self._port()
        # Reste einer früheren Antwort (z. B. ein zweiter Prompt nach STOPPED) würden
        # sonst als Antwort auf diesen Befehl gelesen und alles Weitere verschieben.
        try:
            port.reset_input_buffer()
            port.write(data)
        except _IO_ERRORS as e:
            raise self._lost(e) from e

    def read_until(self, terminator: bytes, timeout: float) -> bytes:
        port = self._port()
        try:
            port.timeout = timeout
            data = port.read_until(terminator)
        except _IO_ERRORS as e:
            raise self._lost(e) from e
        if not data.endswith(terminator):
            raise TransportTimeout(
                tr("keine Antwort von {port} nach {timeout} s").format(
                    port=self.port, timeout=timeout
                )
            )
        return bytes(data)

    def _lost(self, error: Exception) -> TransportError:
        """Ein-/Ausgabefehler (z. B. Adapter abgezogen) als verständlicher TransportError."""
        if isinstance(error, OSError):
            reason = error.strerror or str(error)
        elif isinstance(error, termios.error) and len(error.args) == 2:
            reason = str(error.args[1])  # (errno, Text)
        else:
            reason = str(error)
        return TransportError(
            tr("Verbindung zu {port} unterbrochen ({reason}). Adapter abgezogen?").format(
                port=self.port, reason=reason
            )
        )

    def _port(self) -> serial.Serial:
        if self._serial is None:
            raise TransportError(tr("Transport ist nicht geöffnet"))
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
