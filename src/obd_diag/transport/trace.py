"""Mitschnitt der Adapter-Kommunikation für die Fehlersuche an echten Fahrzeugen.

``TracingTransport`` legt sich um einen beliebigen Transport und schreibt jede
gesendete und empfangene Byte-Folge mit Zeitstempel in eine Textdatei. Steuer- und
Nicht-ASCII-Zeichen werden sichtbar gemacht (``\\r``, ``\\x00``), damit sich die
Rohantworten später exakt als Test-Fixtures nachspielen lassen. Jede Zeile wird
sofort geschrieben, sodass der Mitschnitt auch nach einem Absturz vollständig ist.

Die Datei enthält die FIN, falls sie gelesen wird; sie bleibt lokal.
"""

import time
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Self, TextIO

from obd_diag import __version__
from obd_diag.i18n import tr
from obd_diag.transport.base import Transport, TransportError, TransportTimeout
from obd_diag.transport.serial import SerialTransport


def new_free_path(directory: Path, prefix: str, suffix: str) -> Path:
    """Freier Dateiname ``<prefix>-YYYYmmdd-HHMMSS[-n]<suffix>``; legt den Ordner an."""
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"{prefix}-{datetime.now():%Y%m%d-%H%M%S}"
    for n in range(1, 1000):
        path = directory / (f"{stem}{suffix}" if n == 1 else f"{stem}-{n}{suffix}")
        if not path.exists():
            return path
    raise FileExistsError(
        tr("kein freier Dateiname für {stem} in {directory}").format(stem=stem, directory=directory)
    )


def new_trace_path(directory: Path) -> Path:
    """Freier Dateiname ``trace-YYYYmmdd-HHMMSS[-n].log``; legt den Ordner an."""
    return new_free_path(directory, "trace", ".log")


def escape(data: bytes) -> str:
    """Bytes als lesbarer, eindeutiger Text: druckbares ASCII bleibt, der Rest wird
    als ``\\r``, ``\\n`` oder ``\\xNN`` geschrieben, ``\\`` als ``\\\\``."""
    out = []
    for b in data:
        if b == 0x0D:
            out.append("\\r")
        elif b == 0x0A:
            out.append("\\n")
        elif b == 0x5C:
            out.append("\\\\")
        elif 0x20 <= b < 0x7F:
            out.append(chr(b))
        else:
            out.append(f"\\x{b:02x}")
    return "".join(out)


class TracingTransport:
    """Reicht alles an ``inner`` weiter und protokolliert es in ``log``.

    Zeilenformat: ``<Sekunden seit Öffnen> <Richtung> <Daten>`` mit ``>>`` für
    gesendet, ``<<`` für empfangen und ``!!`` für Fehler.
    """

    def __init__(self, inner: Transport, log: TextIO, label: str = "") -> None:
        self.inner = inner
        self.log = log
        self.label = label
        self._start = time.monotonic()

    def _line(self, direction: str, text: str) -> None:
        self.log.write(f"{time.monotonic() - self._start:9.3f} {direction} {text}\n")
        self.log.flush()

    def open(self) -> None:
        self.log.write(
            f"# obd-diag {__version__} Mitschnitt {datetime.now().astimezone().isoformat()}"
            f"{' ' + self.label if self.label else ''}\n"
        )
        self.log.flush()
        self._start = time.monotonic()
        try:
            self.inner.open()
        except TransportError as e:
            self._line("!!", str(e))
            raise

    def close(self) -> None:
        self.inner.close()
        self._line("--", "geschlossen")

    def write(self, data: bytes) -> None:
        self._line(">>", escape(data))
        self.inner.write(data)

    def read_until(self, terminator: bytes, timeout: float) -> bytes:
        try:
            data = self.inner.read_until(terminator, timeout)
        except TransportError as e:
            self._line("!!", str(e))
            raise
        self._line("<<", escape(data))
        return data

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


class FileTracingTransport(TracingTransport):
    """Mitschnitt von ``inner`` in die Datei ``path``; sie wird angehängt."""

    def __init__(self, inner: Transport, path: Path, label: str = "") -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file = path.open("a", encoding="utf-8")
        super().__init__(inner, self._file, label)

    def open(self) -> None:
        try:
            super().open()
        except TransportError:
            self._file.close()
            raise

    def close(self) -> None:
        try:
            super().close()
        finally:
            self._file.close()


def open_serial(port: str, baudrate: int, trace: Path | None = None) -> Transport:
    """Serieller Transport, mit Mitschnitt nach ``trace``, falls angegeben."""
    transport = SerialTransport(port, baudrate)
    if trace is None:
        return transport
    return FileTracingTransport(transport, trace, f"{port} {baudrate} Baud")


def unescape(text: str) -> bytes:
    """Umkehrung von ``escape``."""
    out = bytearray()
    i = 0
    while i < len(text):
        c = text[i]
        if c != "\\":
            out.append(ord(c))
            i += 1
            continue
        nxt = text[i + 1 : i + 2]
        if nxt == "r":
            out.append(0x0D)
            i += 2
        elif nxt == "n":
            out.append(0x0A)
            i += 2
        elif nxt == "\\":
            out.append(0x5C)
            i += 2
        elif nxt == "x":
            out.append(int(text[i + 2 : i + 4], 16))
            i += 4
        else:
            raise ValueError(
                tr("ungültige Escape-Sequenz an Stelle {pos}: {text}").format(
                    pos=i, text=repr(text[i : i + 4])
                )
            )
    return bytes(out)


def read_trace(path: Path) -> list[tuple[str, bytes]]:
    """Liest einen Mitschnitt als Liste von (Richtung, Bytes); Richtung ``>>``/``<<``."""
    entries: list[tuple[str, bytes]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        # Zeitstempel ist rechtsbündig; die Daten selbst dürfen mit Leerzeichen beginnen
        parts = line.lstrip(" ").split(" ", 2)
        if len(parts) == 3 and parts[1] in (">>", "<<"):
            entries.append((parts[1], unescape(parts[2])))
    return entries


class ReplayTransport:
    """Spielt einen Mitschnitt ab: erwartet die aufgezeichneten Befehle in derselben
    Reihenfolge und liefert die aufgezeichneten Antworten. So lässt sich eine Sitzung
    an einem echten Fahrzeug ohne Adapter nachstellen (z. B. als Test-Fixture)."""

    def __init__(self, entries: list[tuple[str, bytes]]) -> None:
        self._entries = list(entries)
        self._pos = 0

    @classmethod
    def from_file(cls, path: Path) -> "ReplayTransport":
        return cls(read_trace(path))

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def write(self, data: bytes) -> None:
        if self._pos >= len(self._entries) or self._entries[self._pos][0] != ">>":
            raise TransportError(
                tr("Mitschnitt: unerwarteter Befehl {command}").format(command=repr(escape(data)))
            )
        expected = self._entries[self._pos][1]
        if expected != data:
            raise TransportError(
                tr("Mitschnitt: Befehl {command}, aufgezeichnet war {expected}").format(
                    command=repr(escape(data)), expected=repr(escape(expected))
                )
            )
        self._pos += 1

    def read_until(self, terminator: bytes, timeout: float) -> bytes:
        if self._pos < len(self._entries) and self._entries[self._pos][0] == "<<":
            data = self._entries[self._pos][1]
            self._pos += 1
            return data
        raise TransportTimeout(tr("Mitschnitt: keine aufgezeichnete Antwort"))

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        pass
