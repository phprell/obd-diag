"""Demo-Fahrzeug: ein simulierter ELM327 mit einem Beispielauto, ganz ohne Adapter.

``DemoTransport`` beantwortet die Befehle, die obd-diag sendet, wie ein ELM327 v1.5 an
einem Mercedes-Benz A 180 d (W177) mit laufendem Motor im Stand. Die Antworten stammen
aus den Mitschnitten der beiden Tests an diesem Auto (``tests/fixtures/traces/
mercedes_w177``, Diagnose bei laufendem Motor und Live-Daten im Leerlauf): vier
Steuergeräte, ein gespeicherter Code (U1218) mit Freeze Frame, ein offener Readiness-Test
(Abgassensor), FIN mit geschwärzter Seriennummer. Live-Werte laufen die aufgezeichneten
Runden der Reihe nach durch und beginnen danach von vorn.

Nicht aufgezeichnet, aber vom Auto als unterstützt gemeldet und von obd-diag lesbar sind
die PIDs 21, 30, 31, 41, 49 und 66; dafür stehen hier erfundene, plausible Beispielwerte.
Auch die Steuergeräte-Adressen (nur mit ``ATH1`` sichtbar) sind angenommen; die
Mitschnitte sind ohne Header entstanden.

Weil der simulierte Adapter auf Byte-Ebene antwortet, laufen im Demo-Modus Treiber,
Parser und Dienste genau wie am echten Auto, einschließlich Freigabeliste und
Mindestpause. Nur Anfragen, für die es keine Daten gibt, beantwortet er mit ``NO DATA``,
unbekannte Adapterbefehle mit ``?``. Löschen (Mode 04) beantwortet er nie: der Motor läuft,
also bricht ``clear_codes`` schon an der Vorbedingung „Drehzahl 0“ ab.

Für den Batterie- und Anlasstest (``engine_start=True``) steht der Motor zunächst, Zündung
an; ``CRANK_AT`` Sekunden nach der ersten Drehzahlabfrage dreht der Anlasser
``CRANK_SECONDS`` lang, danach läuft der Motor wie oben. Die Werte sind erfunden, aber an
die Mitschnitte angelehnt (Adapter misst etwa 0,75 V weniger als das Steuergerät): Ruhe
12,55 V, Einbruch auf 9,5 V laut Adapter (kurz 8,9 V beim Einschalten), Ladung 14,8 V.
"""

import itertools
import time
from collections.abc import Callable, Iterator, Sequence
from types import TracebackType
from typing import Self

from obd_diag.i18n import N_, tr
from obd_diag.transport.base import TransportError, TransportTimeout

# Portname, unter dem Kommandozeile und Oberfläche das Demo-Fahrzeug öffnen
DEMO_PORT = "demo"
# deutsch; zur Anzeige ``tr(DEMO_DESCRIPTION)``
DEMO_DESCRIPTION = N_("Demo-Fahrzeug ohne Adapter (Mercedes A 180 d)")
DEMO_NOTICE = N_(
    "Demo-Modus: Die Daten stammen von einem Beispielfahrzeug (Mercedes A 180 d), "
    "nicht von einem angeschlossenen Auto."
)

# Kennung auf ATZ/ATI; „Demo“ landet so auch in Sitzungen und Berichten.
ADAPTER_ID = "ELM327 v1.5 (Demo)"
PROTOCOL_NUMBER = "7"  # ISO 15765-4, CAN 29 Bit, 500 kBit/s
PROTOCOL_NAME = "ISO 15765-4 (CAN 29/500)"

# Angenommene Quelladressen der vier Steuergeräte (CAN-ID 18DAF1xx); das erste ist das
# Motorsteuergerät (antwortet auf 0A, 0142 und 0902), das zweite hat den Code gespeichert.
ECUS = (0x10, 0x11, 0x18, 0x1A)

# Eine Antwort: je Steuergerät (Index in ``ECUS``) die Nutzdaten als Hex-Text. Eine Folge
# solcher Antworten wird der Reihe nach und dann wieder von vorn ausgegeben.
Reply = tuple[str | None, ...]

_STATIC: dict[str, Reply] = {
    # Unterstützte PIDs, wie sie das Auto meldet (Mitschnitt live_idle.log)
    "0100": ("410098180011", "410098180001", "410098180001", "410098180001"),
    "0120": ("412080018001", "412080018001", "412080018001", "412080018000"),
    "0140": ("4140C0800000", "414000000001", "414000000001", None),
    "0160": (None, "416005190001", "416000000001", None),
    "0180": (None, "418009004000", "418008000000", None),
    # Diagnose bei laufendem Motor (diagnose_engine_running.log)
    "0101": ("4101000EEB20", "4101010C0000", "410100040000", "4101000C0200"),
    "03": ("4300", "4301D218", "4300", "4300"),
    "07": ("4700", "4700", "4700", "4700"),
    "0A": ("4A00", None, None, None),
    "020200": ("4202000000", "420200D218", "4202000000", "4202000000"),
    "020400": (None, "42040000", None, None),
    "020500": (None, "4205004D", None, None),
    "020C00": (None, "420C000000", None, None),
    "020D00": (None, "420D0000", None, None),
    # FIN WDD1770031J000000 (Seriennummer geschwärzt wie im Mitschnitt)
    "0902": ("490201574444313737303033314A303030303030", None, None, None),
    # Live-Daten im Leerlauf, gleich in jeder Runde
    "0105": ("410566", "410566", "410566", "410566"),
    "010D": ("410D00", "410D00", "410D00", "410D00"),
    # Erfundene Beispielwerte (nicht aufgezeichnet): Strecke mit Warnleuchte 0 km,
    # 40 Warmläufe und 3000 km seit dem Löschen, Fahrpedal 14,9 %, Luftmasse 10 g/s
    "0121": ("41210000", None, None, None),
    "0130": ("413028", None, None, None),
    "0131": ("41310BB8", None, None, None),
    "0149": ("414926", None, None, None),
    "0166": (None, "41660101400000", None, None),
    # Readiness dieser Fahrt (PID 41, nur das Motorsteuergerät meldet sie laut 0140):
    # Abgassensor und NMHC-Katalysator noch offen, NOx-Nachbehandlung ausgesetzt
    "0141": ("41410006E921", None, None, None),
}


def _rounds(*lines: str) -> tuple[Reply, ...]:
    return tuple(tuple(line.split()) for line in lines)


# Live-Daten im Leerlauf (live_idle.log): elf Runden je Befehl
_CYCLING: dict[str, tuple[Reply, ...]] = {
    "010C": _rounds(
        "410C0D80 410C0D80 410C0D8E 410C0D8E",
        "410C0D86 410C0D84 410C0D84 410C0D8A",
        "410C0D84 410C0D78 410C0D7C 410C0D88",
        "410C0D88 410C0D80 410C0D7C 410C0D82",
        "410C0D7A 410C0D78 410C0D7C 410C0D7C",
        "410C0D84 410C0D80 410C0D80 410C0D7C",
        "410C0D80 410C0D84 410C0D84 410C0D7C",
        "410C0D78 410C0D78 410C0D78 410C0D76",
        "410C0D76 410C0D74 410C0D74 410C0D74",
        "410C0D7C 410C0D74 410C0D70 410C0D7A",
        "410C0D6C 410C0D70 410C0D78 410C0D76",
    ),
    "0104": _rounds(
        "41045F 41045F 41045C 41045F",
        "41045D 41045D 41045D 41045D",
        "41045E 41045E 41045E 41045E",
        "41045D 41045D 41045D 41045D",
        "41045D 41045D 41045C 41045D",
        "41045C 41045C 41045C 41045C",
        "41045C 41045C 41045C 41045C",
        "41045B 41045B 41045B 41045B",
        "410459 410459 410459 410459",
        "41045A 41045A 410458 41045A",
        "410457 410457 410459 410457",
    ),
    "0142": _rounds(
        "414239DF",
        "414239BF",
        "414239BF",
        "414239C6",
        "414239D5",
        "414239E1",
        "414239F2",
        "414239EB",
        "414239CB",
        "414239BA",
        "414239DE",
    ),
}

# Bordspannung laut Adapter (ATRV) bei laufendem Motor
_VOLTAGES = ("13.9V", "14.0V")

# Batterietest (``engine_start``): Ablauf ab der ersten Drehzahlabfrage, Sekunden
CRANK_AT = 6.0
CRANK_SECONDS = 0.9
_INRUSH_SECONDS = 0.04
# Motor aus, Zündung an: 11,8 V laut Adapter, 12,55 V laut Motorsteuergerät, 18 °C
_OFF: dict[str, Reply] = {
    "010C": ("410C0000", "410C0000", "410C0000", "410C0000"),
    "010D": ("410D00", "410D00", "410D00", "410D00"),
    "0104": ("410400", "410400", "410400", "410400"),
    "0105": ("41053A", "41053A", "41053A", "41053A"),
    "0142": ("41423106",),
}
_OFF_VOLTAGES = ("11.8V", "11.8V", "11.7V")
# Anlasser dreht (250 1/min), Steuergerät meldet 10,24 V
_CRANKING: dict[str, Reply] = {
    **_OFF,
    "010C": ("410C03E8", "410C03E8", "410C03E8", "410C03E8"),
    "0142": ("41422800",),
}
_CRANK_VOLTAGES = ("9.6V", "9.5V", "9.6V", "9.7V")
_INRUSH_VOLTAGE = "8.9V"

# Adapterbefehle, die nur eine Einstellung ändern und mit OK antworten
_SETTINGS = frozenset(
    {"ATE0", "ATE1", "ATL0", "ATL1", "ATS0", "ATS1", "ATH0", "ATH1", "ATSP0", "ATD"}
)

_MAX_SINGLE_FRAME = 7  # Nutzdaten eines CAN-Einzel-Frames (ISO 15765-2)
_CONSECUTIVE = 7  # Nutzdaten je Folge-Frame
_FIRST = 6  # Nutzdaten im ersten Frame


def _bytes(hex_text: str) -> list[str]:
    return [hex_text[i : i + 2] for i in range(0, len(hex_text), 2)]


def _pad(frame: list[str]) -> list[str]:
    """CAN-Datenfeld auf 8 Bytes auffüllen, wie es die Steuergeräte senden."""
    return frame + ["00"] * (8 - len(frame))


class DemoTransport:
    """Simulierter ELM327 am Demo-Fahrzeug; erfüllt das ``Transport``-Protocol.

    Zustand wie beim echten Adapter: Echo nach ``ATZ`` an, Header, Leerzeichen und
    Zeilenvorschub nach ``AT…``-Einstellung, die erste Anfrage nach ``ATSP0`` meldet
    ``SEARCHING...``. Jede Instanz beginnt die Live-Runden von vorn.

    ``engine_start``: Motor steht und springt von selbst an (Batterietest, siehe Modul);
    ``clock`` misst dafür die Zeit.
    """

    def __init__(
        self, *, engine_start: bool = False, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._open = False
        self._pending: bytes | None = None
        self._echo = True
        self._headers = False
        self._spaces = True
        self._linefeeds = False
        self._searched = False
        self._cycles: dict[str, Iterator[Reply]] = {
            cmd: itertools.cycle(replies) for cmd, replies in _CYCLING.items()
        }
        self._voltages = itertools.cycle(_VOLTAGES)
        self._engine_start = engine_start
        self._clock = clock
        self._ignition_at: float | None = None  # erste Drehzahlabfrage (Batterietest)
        self._off_voltages = itertools.cycle(_OFF_VOLTAGES)
        self._crank_voltages = itertools.cycle(_CRANK_VOLTAGES)

    # --- Transport -----------------------------------------------------------

    def open(self) -> None:
        self._open = True

    def close(self) -> None:
        self._open = False
        self._pending = None

    def write(self, data: bytes) -> None:
        if not self._open:
            raise TransportError(tr("Demo-Fahrzeug: Verbindung nicht geöffnet"))
        text = data.decode("ascii", errors="replace")
        echo = text.rstrip("\r") + "\r" if self._echo else ""
        lines = self._answer("".join(text.split()).upper())
        eol = "\r\n" if self._linefeeds else "\r"
        body = "".join(line + eol for line in lines)
        self._pending = (echo + body + eol + ">").encode("ascii")

    def read_until(self, terminator: bytes, timeout: float) -> bytes:
        if self._pending is None or terminator not in self._pending:
            raise TransportTimeout(tr("Demo-Fahrzeug: keine Antwort ausstehend"))
        data, self._pending = self._pending, None
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

    # --- Adapter -------------------------------------------------------------

    def _answer(self, cmd: str) -> list[str]:
        if cmd.startswith("AT"):
            return self._at(cmd)
        prefix = []
        if not self._searched:
            # erste Anfrage nach ATSP0: der Adapter sucht das Protokoll
            self._searched = True
            prefix = ["SEARCHING..."]
        if len(cmd) == 4 and cmd.startswith("02"):
            cmd += "00"  # Freeze Frame ohne Frame-Nummer meint Frame 00
        reply = self._reply(cmd)
        if reply is None:
            return [*prefix, "NO DATA"]
        return prefix + self._format(reply)

    def _at(self, cmd: str) -> list[str]:
        if cmd in ("ATZ", "ATWS"):
            self._echo, self._headers, self._spaces, self._linefeeds = True, False, True, False
            self._searched = False
            return ["", ADAPTER_ID]
        if cmd == "ATI":
            return [ADAPTER_ID]
        if cmd == "AT@1":
            return ["obd-diag Demo"]
        if cmd == "ATRV":
            return [self._voltage()]
        if cmd == "ATDPN":
            return ["A" + (PROTOCOL_NUMBER if self._searched else "0")]
        if cmd == "ATDP":
            return ["AUTO, " + PROTOCOL_NAME if self._searched else "AUTO"]
        if cmd not in _SETTINGS:
            return ["?"]
        flag, value = cmd[2:-1], cmd[-1] == "1"
        if flag == "E":
            self._echo = value
        elif flag == "H":
            self._headers = value
        elif flag == "S":
            self._spaces = value
        elif flag == "L":
            self._linefeeds = value
        elif cmd == "ATSP0":
            self._searched = False
        return ["OK"]

    def _engine(self) -> str:
        """Zustand des Motors: ``off``, ``inrush``, ``cranking`` oder ``running``."""
        if not self._engine_start:
            return "running"
        if self._ignition_at is None:
            return "off"
        since = self._clock() - self._ignition_at - CRANK_AT
        if since < 0:
            return "off"
        if since < _INRUSH_SECONDS:
            return "inrush"
        return "cranking" if since < CRANK_SECONDS else "running"

    def _voltage(self) -> str:
        engine = self._engine()
        if engine == "off":
            return next(self._off_voltages)
        if engine == "inrush":
            return _INRUSH_VOLTAGE
        if engine == "cranking":
            return next(self._crank_voltages)
        return next(self._voltages)

    def _reply(self, cmd: str) -> Reply | None:
        if self._engine_start and cmd == "010C" and self._ignition_at is None:
            self._ignition_at = self._clock()
        engine = self._engine()
        if engine == "off" and cmd in _OFF:
            return _OFF[cmd]
        if engine in ("inrush", "cranking") and cmd in _CRANKING:
            return _CRANKING[cmd]
        if cmd in self._cycles:
            return next(self._cycles[cmd])
        return _STATIC.get(cmd)

    # --- Ausgabe wie der ELM327 (CAN, mit oder ohne Header) ------------------

    def _format(self, reply: Reply) -> list[str]:
        lines: list[str] = []
        for ecu, payload in zip(ECUS, reply, strict=False):
            if payload is not None:
                lines.extend(self._frames(ecu, _bytes(payload)))
        return lines

    def _frames(self, ecu: int, data: list[str]) -> list[str]:
        if self._headers:
            header = ["18", "DA", "F1", f"{ecu:02X}"]
            return [self._join(header + _pad(frame)) for frame in _isotp(data)]
        if len(data) <= _MAX_SINGLE_FRAME:
            return [self._join(data)]
        # Ohne Header: Längenzeile, dann die Frames mit laufender Nummer (Datenblatt S. 45)
        lines = [f"{len(data):03X}"]
        chunks = [data[:_FIRST], *_chunks(data[_FIRST:], _CONSECUTIVE)]
        for n, chunk in enumerate(chunks):
            frame = chunk if n == 0 else chunk + ["00"] * (_CONSECUTIVE - len(chunk))
            lines.append(f"{n % 16:X}:" + (" " if self._spaces else "") + self._join(frame))
        return lines

    def _join(self, data: Sequence[str]) -> str:
        return (" " if self._spaces else "").join(data)


def _chunks(data: list[str], size: int) -> list[list[str]]:
    return [data[i : i + size] for i in range(0, len(data), size)]


def _isotp(data: list[str]) -> list[list[str]]:
    """CAN-Datenfelder mit PCI-Byte nach ISO 15765-2 (ohne Auffüllung)."""
    if len(data) <= _MAX_SINGLE_FRAME:
        return [[f"{len(data):02X}", *data]]
    length = len(data)
    frames = [[f"{0x10 | length >> 8:02X}", f"{length & 0xFF:02X}", *data[:_FIRST]]]
    for n, chunk in enumerate(_chunks(data[_FIRST:], _CONSECUTIVE), 1):
        frames.append([f"{0x20 | n % 16:02X}", *chunk])
    return frames
