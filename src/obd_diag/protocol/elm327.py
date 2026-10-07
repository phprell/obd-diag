"""Minimaler ELM327-Treiber: Befehl senden, Antwort bis zum Prompt lesen."""

from obd_diag.transport import Transport

PROMPT = b">"


class ElmError(Exception):
    """Der Adapter hat einen Befehl abgelehnt oder keine Daten geliefert."""


class Elm327:
    def __init__(self, transport: Transport, timeout: float = 5.0) -> None:
        self.transport = transport
        self.timeout = timeout

    def initialize(self) -> str:
        """Setzt den Adapter zurück und schaltet Echo, Zeilenvorschub und Leerzeichen ab."""
        version = self.command("ATZ")
        for cmd in ("ATE0", "ATL0", "ATS0", "ATH0", "ATSP0"):
            self.command(cmd)
        return version

    def command(self, cmd: str) -> str:
        self.transport.write(cmd.encode("ascii") + b"\r")
        raw = self.transport.read_until(PROMPT, self.timeout)
        lines = [
            line.strip()
            for line in raw[: -len(PROMPT)].decode("ascii", errors="replace").splitlines()
        ]
        # Echo und Leerzeilen entfernen; Echo ist bis zum ersten ATE0 aktiv.
        lines = [line for line in lines if line and line != cmd]
        text = "\n".join(lines)
        if text in ("?", "NO DATA", "UNABLE TO CONNECT", "CAN ERROR"):
            raise ElmError(f"{cmd}: {text}")
        return text

    def voltage(self) -> float:
        """Bordspannung in Volt (``ATRV``)."""
        return float(self.command("ATRV").rstrip("Vv"))
