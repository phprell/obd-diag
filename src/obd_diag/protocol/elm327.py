"""Minimaler ELM327-Treiber: Befehl senden, Antwort bis zum Prompt lesen."""

import re
from dataclasses import dataclass

from obd_diag.transport import Transport

PROMPT = b">"

# Statusmeldungen, die der Adapter vor die eigentliche Antwort setzt.
_STATUS_PREFIX = re.compile(r"^(SEARCHING\.\.\.|BUS INIT:\s*\.*\s*OK)\s*")

_ERRORS = frozenset(
    {
        "UNABLE TO CONNECT",
        "CAN ERROR",
        "BUS ERROR",
        "BUS BUSY",
        "BUFFER FULL",
        "DATA ERROR",
        "FB ERROR",
        "STOPPED",
    }
)

# Interne Fehler (``ERR94``: schwerer CAN-Fehler), Unterspannungs-Reset und als fehlerhaft
# markierte Zeilen (``... <DATA ERROR``, ``... <RX ERROR``), siehe Datenblatt ELM327DS,
# „Error Messages and Alerts“.
_ERROR_LINE = re.compile(r"^ERR[0-9A-F]{2}$|^LV RESET$|<(DATA|RX) ERROR$")

# Zeilen ohne ein einziges druckbares ASCII-Zeichen (leer oder reiner Zeichenmüll)
_JUNK = re.compile(r"^[^\x21-\x7e]*$")

# ISO 15765-4 (CAN); A bis C sind CAN-Protokolle der STN-Chips bzw. benutzerdefiniert.
_CAN_PROTOCOLS = frozenset("6789ABC")


class ElmError(Exception):
    """Der Adapter hat einen Befehl abgelehnt oder keine Daten geliefert."""


class NoDataError(ElmError):
    """``NO DATA``: kein Steuergerät hat geantwortet (z. B. keine Fehlercodes)."""


class UnknownCommandError(ElmError):
    """``?``: der Adapter kennt den Befehl nicht oder unterstützt ihn nicht."""


@dataclass(frozen=True)
class ObdProtocol:
    """Fahrzeugprotokoll laut ``ATDPN``/``ATDP``."""

    number: str  # "0" bis "C", ohne das "A" für automatische Erkennung
    name: str  # z. B. "ISO 15765-4 (CAN 11/500)"

    @property
    def is_can(self) -> bool:
        return self.number in _CAN_PROTOCOLS


class Elm327:
    def __init__(self, transport: Transport, timeout: float = 5.0) -> None:
        self.transport = transport
        self.timeout = timeout

    def initialize(self) -> str:
        """Setzt den Adapter zurück und schaltet Echo, Zeilenvorschub und Leerzeichen ab.

        Liefert die Kennung aus der letzten Zeile der ``ATZ``-Antwort (z. B.
        ``ELM327 v1.5``); davor stehen bei manchen Adaptern Leerzeilen oder Müll.
        """
        lines = self.command("ATZ").splitlines()
        for cmd in ("ATE0", "ATL0", "ATS0", "ATH0", "ATSP0"):
            self.command(cmd)
        return lines[-1] if lines else ""

    def command(self, cmd: str) -> str:
        """Sendet ``cmd`` und liefert die bereinigte Antwort; wirft ``ElmError`` bei Fehlern.

        Entfernt werden Echo, Leerzeilen, Statuszeilen (``SEARCHING...``,
        ``BUS INIT: OK``), Nullbytes und Zeilen aus reinem Zeichenmüll (manche Klone
        senden nach ``ATZ`` z. B. ein Byte ``FC``).
        """
        self.transport.write(cmd.encode("ascii") + b"\r")
        raw = self.transport.read_until(PROMPT, self.timeout)
        lines: list[str] = []
        decoded = raw[: -len(PROMPT)].replace(b"\x00", b"").decode("ascii", errors="replace")
        for line in decoded.splitlines():
            line = _STATUS_PREFIX.sub("", line.strip())
            # Echo und Leerzeilen entfernen; Echo ist bis zum ersten ATE0 aktiv.
            if line != cmd and not _JUNK.match(line):
                lines.append(line)
        text = "\n".join(lines)
        if text == "NO DATA":
            raise NoDataError(f"{cmd}: {text}")
        if text == "?":
            raise UnknownCommandError(f"{cmd}: {text}")
        for line in lines:
            # Fehler auch nach Teildaten (z. B. ``STOPPED``): die Antwort ist unvollständig.
            if line in _ERRORS or line.startswith("BUS INIT:") or _ERROR_LINE.search(line):
                raise ElmError(f"{cmd}: {text}")
        return text

    def query(self, cmd: str) -> str | None:
        """Wie ``command``, aber ``NO DATA`` ergibt ``None`` statt einer Ausnahme."""
        try:
            return self.command(cmd)
        except NoDataError:
            return None

    def voltage(self) -> float:
        """Bordspannung in Volt (``ATRV``)."""
        return float(self.command("ATRV").rstrip("Vv"))

    def protocol(self) -> ObdProtocol:
        """Lässt den Adapter das Fahrzeugprotokoll aushandeln und meldet es.

        Nach ``ATSP0`` sucht der ELM327 das Protokoll erst bei der ersten OBD-Anfrage;
        dafür dient ``0100`` (unterstützte PIDs), das jedes OBD-II-Fahrzeug beantwortet.
        """
        self.query("0100")
        number = self.command("ATDPN").upper()
        if len(number) == 2 and number.startswith("A"):
            number = number[1:]  # "A6": automatisch erkannt, Protokoll 6
        name = self.command("ATDP").strip().removeprefix("AUTO, ")
        return ObdProtocol(number, name)
