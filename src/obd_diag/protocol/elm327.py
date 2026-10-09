"""Minimaler ELM327-Treiber: Befehl senden, Antwort bis zum Prompt lesen."""

import re
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from obd_diag.protocol.headers import HeaderFormat, header_format
from obd_diag.transport import Transport, TransportError

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
# Protokollnummern laut ATDPN; "0" heißt „automatisch“, also noch nicht bestimmt.
_KNOWN_PROTOCOLS = frozenset("123456789ABC")

_WHITESPACE = re.compile(r"\s+")

# --- Freigabeliste: was überhaupt an Adapter und Fahrzeug gesendet werden darf ---

# Adapter-Befehle (ELM327-Datenblatt); sie stellen nur den Adapter ein und senden
# nichts ans Fahrzeug. ATSP0 = Protokoll automatisch suchen.
AT_COMMANDS = frozenset(
    {"ATZ", "ATE0", "ATL0", "ATS0", "ATH0", "ATH1", "ATSP0", "ATRV", "ATDP", "ATDPN"}
)
# Lesende OBD-Anfragen nach SAE J1979: Mode 01 (aktuelle Daten), Mode 02 (Freeze
# Frame, mit oder ohne Frame-Nummer 00), Mode 03/07/0A (Fehlercodes), Mode 09 PID 02
# (FIN). Nur Großbuchstaben, keine Leerzeichen.
_READ_REQUEST = re.compile(r"01[0-9A-F]{2}|02[0-9A-F]{2}(00)?|03|07|0A|0902")
# Der einzige schreibende Befehl; nur innerhalb von ``Elm327.allow_clear()``.
CLEAR_COMMAND = "04"


class ForbiddenCommandError(Exception):
    """Ein Befehl außerhalb der Freigabeliste sollte gesendet werden.

    Das ist ein Programmierfehler, kein Adapterproblem: bewusst kein ``ElmError``,
    damit ihn kein Aufrufer als „Angabe nicht verfügbar“ abfängt. Gesendet wurde nichts.
    """


def is_read_only(cmd: str) -> bool:
    """True für Adapter-Befehle und lesende OBD-Anfragen der Freigabeliste."""
    return cmd in AT_COMMANDS or _READ_REQUEST.fullmatch(cmd) is not None


def _same_command(line: str, cmd: str) -> bool:
    """Echo-Vergleich ohne Rücksicht auf Groß-/Kleinschreibung und Leerraum."""
    return _WHITESPACE.sub("", line).upper() == _WHITESPACE.sub("", cmd).upper()


class ElmError(Exception):
    """Der Adapter hat einen Befehl abgelehnt oder keine Daten geliefert."""


class NoDataError(ElmError):
    """``NO DATA``: kein Steuergerät hat geantwortet (z. B. keine Fehlercodes)."""


class UnknownCommandError(ElmError):
    """``?``: der Adapter kennt den Befehl nicht oder unterstützt ihn nicht."""


class NoConnectionError(ElmError):
    """``UNABLE TO CONNECT``: der Adapter findet kein Steuergerät, mit dem er sprechen kann.

    Laut Datenblatt (ELM327DSJ, „Error Messages and Alerts“) hat er dann alle Protokolle
    durchprobiert; meist ist die Zündung aus. Am ersten echten Auto (W177) war genau das
    die Ursache, ``HINT`` sagt dem Nutzer daher, was zu tun ist.
    """

    HINT = (
        "Kein Steuergerät antwortet. Zündung einschalten (der Motor darf aus bleiben), "
        "bei Adaptern mit MS-/HS-CAN-Schalter HS-CAN wählen und erneut versuchen."
    )


@dataclass(frozen=True)
class ObdProtocol:
    """Fahrzeugprotokoll laut ``ATDPN``/``ATDP``."""

    number: str  # "1" bis "C", ohne das "A" für automatische Erkennung; sonst unbekannt
    name: str  # z. B. "ISO 15765-4 (CAN 11/500)"
    # Nur bei unbekannter Nummer: aus der Form der Header von ``0100`` geschlossen
    inferred: HeaderFormat | None = None

    @property
    def is_can(self) -> bool:
        if self.number in _KNOWN_PROTOCOLS:
            return self.number in _CAN_PROTOCOLS
        return self.inferred is not None and self.inferred.is_can


# Wartezeit auf den Prompt je Befehl (Sekunden). Der ELM327 wartet selbst bis zu 5 s,
# wenn ein Steuergerät ``7F xx 78`` (Antwort folgt) meldet, und beginnt bei jedem weiteren
# ``78`` neu (Datenblatt ELM327DSJ S. 45); 10 s lassen dafür Luft.
DEFAULT_TIMEOUT = 10.0
# Die erste OBD-Anfrage nach ``ATSP0`` startet die Protokollsuche (S. 36); bei K-Line
# dauert schon ein Verbindungsaufbau 2 bis 3 s (S. 33), und die Suche probiert mehrere
# Protokolle nacheinander.
SEARCH_TIMEOUT = 30.0

# Mindestabstand (Sekunden) zwischen dem Ende einer Antwort und der nächsten Anfrage, die
# den Adapter ans Fahrzeug senden lässt (alles außer ``AT…``). Es ist ohnehin immer nur
# eine Anfrage unterwegs: gesendet wird erst, wenn der Adapter mit dem Prompt ``>`` fertig
# gemeldet hat. Diese Pause ist eine zweite, harte Grenze dahinter (höchstens 20 Anfragen
# je Sekunde), falls ein Adapter den Prompt zu früh schickt. Normgerecht reicht null: nach
# ISO 15765-4 darf die nächste Anfrage sofort nach der Antwort folgen, bei K-Line hält der
# ELM327 die Mindestpause P3 selbst ein.
MIN_REQUEST_GAP = 0.05


class Elm327:
    def __init__(
        self,
        transport: Transport,
        timeout: float = DEFAULT_TIMEOUT,
        search_timeout: float = SEARCH_TIMEOUT,
        min_request_gap: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.transport = transport
        self.timeout = timeout
        self.search_timeout = search_timeout
        # None: der Modulwert, damit Tests ihn zentral abschalten können
        self.min_request_gap = MIN_REQUEST_GAP if min_request_gap is None else min_request_gap
        self._clock = clock
        self._sleep = sleep
        self._last_reply: float | None = None  # Ende der letzten Antwort (``clock``)
        self._clear_allowed = False

    @contextmanager
    def allow_clear(self) -> Iterator[None]:
        """Erlaubt Mode 04 für die Dauer des ``with``-Blocks (nur ``obd.clear_dtcs``)."""
        self._clear_allowed = True
        try:
            yield
        finally:
            self._clear_allowed = False

    @contextmanager
    def _searching(self) -> Iterator[None]:
        """Längere Wartezeit für Anfragen, die eine Protokollsuche auslösen können."""
        saved = self.timeout
        self.timeout = max(saved, self.search_timeout)
        try:
            yield
        finally:
            self.timeout = saved

    def _check(self, cmd: str) -> None:
        if is_read_only(cmd):
            return
        if cmd == CLEAR_COMMAND:
            if self._clear_allowed:
                return
            raise ForbiddenCommandError("04 (Fehlercodes löschen) nur über clear_dtcs")
        raise ForbiddenCommandError(f"nicht freigegebener Befehl {cmd!r}")

    def initialize(self) -> str:
        """Setzt den Adapter zurück und schaltet Echo, Zeilenvorschub und Leerzeichen ab.

        Liefert die Kennung aus der letzten Zeile der ``ATZ``-Antwort (z. B.
        ``ELM327 v1.5``); davor stehen bei manchen Adaptern Leerzeilen oder Müll.

        Antwortet der Adapter auf ``ATZ`` mit ``?``, wird es einmal wiederholt: Direkt
        nach dem Einstecken hat ein FORScan-ELMconfig-Adapter (CH340) das erste ``ATZ``
        so abgelehnt, das zweite beantwortet; vermutlich lag noch ein Störbyte im Puffer
        des Adapters.
        """
        try:
            reply = self.command("ATZ")
        except UnknownCommandError:
            reply = self.command("ATZ")
        lines = reply.splitlines()
        for cmd in ("ATE0", "ATL0", "ATS0", "ATH0", "ATSP0"):
            self.command(cmd)
        return lines[-1] if lines else ""

    def command(self, cmd: str) -> str:
        """Sendet ``cmd`` und liefert die bereinigte Antwort; wirft ``ElmError`` bei Fehlern.

        Entfernt werden Echo (auch in anderer Groß-/Kleinschreibung oder mit
        Leerzeichen, aber nur vor der eigentlichen Antwort), Leerzeilen, Statuszeilen
        (``SEARCHING...``, ``BUS INIT: OK``), Nullbytes und Zeilen aus reinem
        Zeichenmüll (manche Klone senden nach ``ATZ`` z. B. ein Byte ``FC``).
        """
        self._check(cmd)
        if not cmd.startswith("AT"):
            self._wait_for_gap()
        try:
            self.transport.write(cmd.encode("ascii") + b"\r")
            return self._read(cmd, self.timeout)
        finally:
            self._last_reply = self._clock()

    def _wait_for_gap(self) -> None:
        """Wartet, bis seit der letzten Antwort ``min_request_gap`` vergangen ist."""
        if self._last_reply is None:
            return
        remaining = self._last_reply + self.min_request_gap - self._clock()
        if remaining > 0:
            self._sleep(remaining)

    def read_more(self, cmd: str, timeout: float) -> str:
        """Liest eine weitere Antwort bis zum Prompt, ohne etwas zu senden.

        Für Steuergeräte, die erst ``7F <Mode> 78`` (Antwort folgt) melden und die
        eigentliche Antwort später schicken. ``cmd`` dient nur der Fehlermeldung und dem
        Entfernen eines Echos. Bereinigung und Fehler wie bei ``command``; nach
        ``timeout`` Sekunden ohne Prompt ``TransportTimeout``.
        """
        try:
            return self._read(cmd, timeout)
        finally:
            self._last_reply = self._clock()

    def _read(self, cmd: str, timeout: float) -> str:
        raw = self.transport.read_until(PROMPT, timeout)
        lines: list[str] = []
        echo_seen = False
        decoded = raw[: -len(PROMPT)].replace(b"\x00", b"").decode("ascii", errors="replace")
        for line in decoded.splitlines():
            line = _STATUS_PREFIX.sub("", line.strip())
            if _JUNK.match(line):
                continue  # Leerzeilen und Zeichenmüll
            # Echo (bis zum ersten ATE0 aktiv) steht vor der Antwort; manche Adapter
            # wiederholen den Befehl in anderer Schreibweise oder mit Leerzeichen.
            if not lines and not echo_seen and _same_command(line, cmd):
                echo_seen = True
                continue
            lines.append(line)
        text = "\n".join(lines)
        if text == "NO DATA":
            raise NoDataError(f"{cmd}: {text}")
        if text == "?":
            raise UnknownCommandError(f"{cmd}: {text}")
        for line in lines:
            # auch "BUS INIT: ...UNABLE TO CONNECT" bei K-Line
            if line.endswith("UNABLE TO CONNECT"):
                raise NoConnectionError(f"{cmd}: {text}")
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

    def query_with_headers(self, cmd: str) -> str | None:
        """Wie ``query``, aber mit Headern (``ATH1``); danach wieder ``ATH0``.

        Nur für lesende Anfragen gedacht, die wiederholt werden dürfen. Das Ergebnis
        zerlegt ``protocol.headers.parse_header_response``.
        """
        self.command("ATH1")
        try:
            return self.query(cmd)
        finally:
            try:
                self.command("ATH0")
            except ElmError as e:
                # Mit Headern würden alle weiteren Antworten falsch gelesen. Kein
                # ElmError, damit kein Aufrufer das als "Angabe fehlt" abfängt.
                raise TransportError(
                    f"Adapter lässt sich nicht auf ATH0 zurückstellen ({e}); Sitzung abgebrochen"
                ) from e

    def _protocol_number(self) -> str:
        """Nummer laut ``ATDPN`` ohne das "A" der automatischen Erkennung; "" bei ``?``."""
        try:
            number = self.command("ATDPN").strip().upper()
        except UnknownCommandError:
            return ""
        if len(number) == 2 and number.startswith("A"):
            number = number[1:]  # "A6": automatisch erkannt, Protokoll 6
        return number

    def _header_shape(self) -> HeaderFormat | None:
        """Format der Header in der Antwort auf ``0100`` (einmal mit ``ATH1``)."""
        try:
            response = self.query_with_headers("0100")
        except ElmError:
            return None
        return None if response is None else header_format(response)

    def protocol(self) -> ObdProtocol:
        """Lässt den Adapter das Fahrzeugprotokoll aushandeln und meldet es.

        Nach ``ATSP0`` sucht der ELM327 das Protokoll erst bei der ersten OBD-Anfrage;
        dafür dient ``0100`` (unterstützte PIDs), das jedes OBD-II-Fahrzeug beantwortet.
        Weil die Suche dauern kann, gilt dafür ``search_timeout`` statt ``timeout``.

        Meldet ``ATDPN`` danach keine bekannte Nummer (``0``, ``A0``, ``?`` oder Unsinn),
        wird nachgefasst: hatte ``0100`` keine Daten, einmal wiederholen; dann ``ATDPN``
        erneut (sobald verbunden, kennt der Adapter das Protokoll). Bleibt die Nummer
        unbekannt, hat aber ein Steuergerät geantwortet, wird ``0100`` einmal mit
        Headern gesendet und an deren Form erkannt, ob CAN 11 Bit, CAN 29 Bit oder ein
        älteres Protokoll vorliegt (``ObdProtocol.inferred``). Ohne Antwort bleibt es
        bei „kein CAN“.
        """
        with self._searching():
            answer = self.query("0100")
        number = self._protocol_number()
        inferred: HeaderFormat | None = None
        if number not in _KNOWN_PROTOCOLS:
            if answer is None:
                with self._searching():
                    answer = self.query("0100")
            number = self._protocol_number()
            if number not in _KNOWN_PROTOCOLS and answer is not None:
                inferred = self._header_shape()
        try:
            name = self.command("ATDP").strip().removeprefix("AUTO, ")
        except UnknownCommandError:
            name = ""
        if inferred is not None:
            name = f"{name or 'unbekannt'} (laut Headern {inferred})"
        return ObdProtocol(number, name, inferred)
