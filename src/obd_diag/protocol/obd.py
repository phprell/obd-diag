"""OBD-II-Dienste über den ELM327.

Bis auf ``clear_dtcs`` (Mode 04) nur lesend.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from obd_diag.i18n import N_, tr
from obd_diag.protocol.dtc_decode import (
    RESPONSE_PENDING,
    NegativeDtcResponse,
    decode_dtc,
    parse_dtc_messages,
)
from obd_diag.protocol.elm327 import Elm327, ElmError, NoDataError
from obd_diag.protocol.frames import FrameSequenceError, split_messages
from obd_diag.protocol.headers import EcuMessage, parse_header_response
from obd_diag.transport import TransportError, TransportTimeout

log = logging.getLogger(__name__)

# Modes, die Fehlercodes liefern: gespeichert, ausstehend, permanent.
DTC_MODES = (0x03, 0x07, 0x0A)


def read_with_headers(elm: Elm327, cmd: str, reason: Exception) -> list[EcuMessage]:
    """Wiederholt die lesende Anfrage ``cmd`` mit Headern und zerlegt je Steuergerät.

    Für Antworten, deren Frames sich ohne Header nicht zuordnen lassen (``reason``).
    Reihenfolge: nach Steuergeräte-Adresse, dann Eingang (siehe ``protocol.headers``).
    Wirft ``ElmError``, wenn auch das nicht gelingt oder diesmal keine Antwort kommt.
    """
    log.info("%s: %s; wiederhole mit Headern (ATH1)", cmd, reason)
    response = elm.query_with_headers(cmd)
    if response is None:
        raise ElmError(
            tr("{cmd}: {reason}; mit Headern wiederholt: NO DATA").format(cmd=cmd, reason=reason)
        )
    try:
        return parse_header_response(response)
    except ValueError as e:
        raise ElmError(
            tr("{cmd}: {reason}; auch mit Headern nicht lesbar: {error}").format(
                cmd=cmd, reason=reason, error=e
            )
        ) from e


# Wie lange auf die endgültige Antwort nach ``7F <Mode> 78`` gewartet wird (Sekunden).
# SAE J1979 bzw. Datenblatt ELM327DSJ S. 45: bis zu 5 s, nach jedem weiteren 78 neu.
DTC_PENDING_TIMEOUT = 5.0


def _open_pending(messages: list[bytes], mode: int) -> int:
    """Wie viele ``7F <Mode> 78`` noch keine spätere positive Antwort gefunden haben.

    Ohne Header lässt sich eine Antwort keinem Steuergerät zuordnen; gezählt wird daher
    nach Reihenfolge: jede positive Antwort nach einem ``78`` gilt als dessen Antwort.
    """
    sid = mode + 0x40
    pending = 0
    for message in messages:
        if message[:3] == bytes([0x7F, mode, RESPONSE_PENDING]):
            pending += 1
        elif pending and message[:1] == bytes([sid]):
            pending -= 1
    return pending


def read_dtcs(
    elm: Elm327, mode: int, *, can: bool, pending_timeout: float = DTC_PENDING_TIMEOUT
) -> list[str]:
    """Fehlercodes aller Steuergeräte für Mode 03, 07 oder 0A.

    ``NO DATA`` heißt: kein Code gespeichert, also eine leere Liste. Sind Frames
    mehrerer Steuergeräte vermischt (ohne Header nicht zuzuordnen), wird die Anfrage
    einmal mit Headern (``ATH1``) wiederholt; die Codes kommen dann nach
    Steuergeräte-Adresse geordnet (z. B. 7E8 vor 7E9), sonst in Eingangsreihenfolge.

    Meldet ein Steuergerät ``7F <Mode> 78`` (Antwort folgt) und steht die Antwort nicht
    schon dabei, wird ohne erneutes Senden bis ``pending_timeout`` Sekunden
    weitergelesen. Kommt sie nicht, oder lehnt ein Steuergerät aus anderem Grund ab als
    „Mode nicht unterstützt“, gibt es ``NegativeResponseError`` statt einer leeren Liste:
    der Fehlerspeicher ist dann unbekannt, nicht leer.
    """
    if mode not in DTC_MODES:
        raise ValueError(tr("Mode {mode:02X} liefert keine Fehlercodes").format(mode=mode))
    cmd = f"{mode:02X}"
    response = elm.query(cmd)
    if response is None:
        return []
    try:
        messages = split_messages(response)
    except FrameSequenceError as e:
        # Vermischte mehrteilige Antworten gibt es nur bei CAN; dort steht das Zählbyte.
        headed = read_with_headers(elm, cmd, e)
        label = tr("{cmd} (mit Headern)").format(cmd=cmd)
        return _parse_dtcs(label, [m.data for m in headed], mode, can=True)
    except ValueError as e:
        raise ElmError(f"{cmd}: {e}") from e
    deadline = time.monotonic() + pending_timeout
    while _open_pending(messages, mode):
        remaining = deadline - time.monotonic()
        try:
            if remaining <= 0:
                raise TimeoutError
            more = elm.read_more(cmd, remaining)
        except (TimeoutError, TransportTimeout, NoDataError) as e:
            raise NegativeResponseError(mode, RESPONSE_PENDING) from e
        messages += _messages(cmd, more)
    final = [m for m in messages if m[:3] != bytes([0x7F, mode, RESPONSE_PENDING])]
    return _parse_dtcs(cmd, final, mode, can=can)


def _parse_dtcs(cmd: str, messages: list[bytes], mode: int, *, can: bool) -> list[str]:
    try:
        return parse_dtc_messages(messages, mode, can=can)
    except NegativeDtcResponse as e:
        raise NegativeResponseError(e.mode, e.nrc) from e
    except ValueError as e:
        raise ElmError(f"{cmd}: {e}") from e


# Gründe negativer Antworten (``7F <Mode> <NRC>``, ISO 14229 bzw. ISO 15031-5)
_NRC_TEXT = {
    0x10: N_("allgemein abgelehnt"),
    0x11: N_("Dienst nicht unterstützt"),
    0x12: N_("Unterfunktion nicht unterstützt"),
    0x21: N_("Steuergerät beschäftigt"),
    0x22: N_("Bedingungen nicht erfüllt"),
    0x31: N_("Anfrage außerhalb des gültigen Bereichs"),
    0x33: N_("Zugriff verweigert"),
    0x78: N_("Antwort angekündigt, aber nicht gekommen"),
}
_RESPONSE_PENDING = 0x78  # Steuergerät arbeitet noch, die eigentliche Antwort folgt


class NegativeResponseError(ElmError):
    """Ein Steuergerät hat mit ``7F <Mode> <NRC>`` abgelehnt."""

    def __init__(self, mode: int, nrc: int) -> None:
        self.mode = mode
        self.nrc = nrc
        reason = tr(_NRC_TEXT.get(nrc, N_("unbekannter Grund")))
        super().__init__(
            tr("{reason} (Antwort 7F {mode:02X} {nrc:02X})").format(
                reason=reason, mode=mode, nrc=nrc
            )
        )


def _messages(cmd: str, response: str) -> list[bytes]:
    try:
        return split_messages(response)
    except ValueError as e:
        raise ElmError(f"{cmd}: {e}") from e


# Höchstens so lange auf die endgültige Antwort nach ``7F 04 78`` warten (Sekunden)
CLEAR_PENDING_TIMEOUT = 10.0


def _clear_answers(cmd: str, response: str) -> tuple[bool, bool]:
    """(bestätigt, wartet noch) für eine Antwort auf Mode 04; wirft bei Ablehnung."""
    confirmed = pending = False
    for message in _messages(cmd, response):
        if len(message) >= 3 and message[0] == 0x7F and message[1] == 0x04:
            if message[2] == _RESPONSE_PENDING:
                pending = True
                continue
            raise NegativeResponseError(0x04, message[2])
        if message[:1] != b"\x44":
            raise ElmError(
                tr("04: unerwartete Antwort {data}").format(data=repr(message.hex(" ").upper()))
            )
        confirmed = True
        pending = False  # die endgültige Antwort folgt auf die Zwischenmeldung
    return confirmed, pending


def clear_dtcs(elm: Elm327, *, pending_timeout: float = CLEAR_PENDING_TIMEOUT) -> None:
    """Mode 04: gespeicherte und ausstehende Codes, Freeze Frame und Readiness löschen.

    Schreibender Befehl! Vorbedingungen prüfen und sichern muss der Aufrufer (siehe
    ``services.clear``). Erfolg heißt: mindestens ein Steuergerät bestätigt mit ``44``
    und keines lehnt ab. Wirft ``NegativeResponseError`` bei ``7F 04 xx``,
    ``NoDataError`` ohne Antwort und ``ElmError`` bei unerwarteter Antwort.

    ``7F 04 78`` (ISO 14229: Anfrage erhalten, Antwort folgt) ist keine Ablehnung: steht
    danach noch keine endgültige Antwort, wird ohne erneutes Senden weitergelesen, bis
    ``44`` oder ``7F 04 xx`` kommt oder ``pending_timeout`` Sekunden (ab dem Senden)
    vergangen sind. Ohne Bestätigung bis dahin ``ElmError`` („nicht bestätigt“); ist
    bereits ein anderes Steuergerät bestätigt, gilt das Löschen als bestätigt (mit
    Log-Warnung). Mode 04 wird nie wiederholt.
    """
    deadline = time.monotonic() + pending_timeout
    with elm.allow_clear():
        response = elm.command("04")
    confirmed, pending = _clear_answers("04", response)
    while pending:
        remaining = deadline - time.monotonic()
        try:
            if remaining <= 0:
                raise TimeoutError
            more = elm.read_more("04", remaining)
        except (TimeoutError, TransportError, NoDataError) as e:
            if confirmed:
                log.warning("04: ein Steuergerät meldet weiter 7F 04 78 (%s)", str(e) or "Zeit um")
                return
            raise ElmError(
                tr(
                    "04: nicht bestätigt (nur 7F 04 78, Antwort folgt; nach {seconds:g} s "
                    "keine endgültige Antwort)"
                ).format(seconds=pending_timeout)
            ) from e
        response += "\n" + more
        now_confirmed, pending = _clear_answers("04", more)
        confirmed = confirmed or now_confirmed
    if not confirmed:
        raise ElmError(tr("04: keine Bestätigung ({response})").format(response=repr(response)))


def read_pid(elm: Elm327, pid: int) -> bytes | None:
    """Mode 01: Datenbytes des ersten Steuergeräts, das ``pid`` beantwortet.

    ``None``, wenn kein Steuergerät antwortet (``NO DATA``) oder alle ablehnen.
    """
    cmd = f"01{pid:02X}"
    response = elm.query(cmd)
    if response is None:
        return None
    for message in _messages(cmd, response):
        if len(message) >= 2 and message[0] == 0x41 and message[1] == pid:
            return message[2:]
    return None


def decode_rpm(data: bytes) -> float:
    """PID 0C: Motordrehzahl in 1/min, ((A*256)+B)/4."""
    if len(data) < 2:
        raise ValueError(
            tr("PID 0C braucht zwei Datenbytes, nicht {count}").format(count=len(data))
        )
    return (data[0] * 256 + data[1]) / 4


def read_rpm(elm: Elm327) -> float | None:
    """Motordrehzahl in 1/min oder ``None``, wenn sie nicht lesbar ist."""
    data = read_pid(elm, 0x0C)
    if data is None or len(data) < 2:
        return None
    return decode_rpm(data)


def read_rpms(elm: Elm327) -> list[float | None]:
    """Motordrehzahl je Antwort auf ``010C``, in der Reihenfolge der Antworten.

    Antworten mehrere Steuergeräte (z. B. Motor und Getriebe), steht jedes einzeln
    darin; ``None`` für eine Antwort, die keine gültige Drehzahl ist (Ablehnung,
    zu kurz, andere PID). Leer bei ``NO DATA``.
    """
    response = elm.query("010C")
    if response is None:
        return []
    rpms: list[float | None] = []
    for message in _messages("010C", response):
        valid = len(message) >= 4 and message[0] == 0x41 and message[1] == 0x0C
        rpms.append(decode_rpm(message[2:]) if valid else None)
    return rpms


# PIDs im Freeze Frame: Schlüssel in ``FreezeFrame.values``, Datenbytes, Umrechnung
_FREEZE_PIDS: dict[int, tuple[str, int, Callable[[bytes], float]]] = {
    0x04: ("engine_load_pct", 1, lambda d: round(d[0] * 100 / 255, 1)),
    0x05: ("coolant_temp_c", 1, lambda d: d[0] - 40),
    0x0C: ("rpm", 2, decode_rpm),
    0x0D: ("speed_kmh", 1, lambda d: d[0]),
}


@dataclass(frozen=True)
class FreezeFrame:
    """Momentaufnahme (Mode 02, Frame 00) beim Setzen eines Fehlercodes.

    ``raw`` enthält die Antworten wie empfangen (Befehl -> Hex-Text), ``values`` die
    daraus dekodierten Werte. Unbeantwortete PIDs fehlen in beiden. Die Schlüssel von
    ``raw`` zeigen das verwendete Anfrageformat: ``020C00`` (SAE J1979, mit
    Frame-Nummer) oder ``020C`` (ohne, Rückfallebene für manche Steuergeräte).
    """

    dtc: str | None = None  # PID 02: Code, der den Freeze Frame ausgelöst hat
    raw: dict[str, str] = field(default_factory=dict)
    values: dict[str, float] = field(default_factory=dict)


_FREEZE_DTC_SIZE = 2  # PID 02: zwei Bytes des auslösenden Codes


def _freeze_cmd(pid: int, frame_byte: bool) -> str:
    return f"02{pid:02X}00" if frame_byte else f"02{pid:02X}"


def _freeze_payload(message: bytes, pid: int, size: int, frame_byte: bool) -> bytes | None:
    """Datenbytes einer Antwort ``42 <pid> 00 …``; ohne Frame-Byte in der Anfrage auch
    ``42 <pid> …`` (nur bei genau ``size`` Datenbytes, sonst mehrdeutig)."""
    if len(message) < 2 or message[0] != 0x42 or message[1] != pid:
        return None
    if len(message) >= 3 and message[2] == 0x00 and (frame_byte or len(message) >= 3 + size):
        return message[3:]
    if not frame_byte and len(message) == 2 + size:
        return message[2:]
    return None


def _freeze_data(
    elm: Elm327, pid: int, size: int, frame_byte: bool
) -> tuple[str, str | None, bytes | None, bool]:
    """Befehl, Antwort, Datenbytes und „Format abgelehnt“ (``7F 02 12``) zu einem PID.

    Antwort ``None`` bei ``NO DATA``, Daten ``None``, wenn keine passende Antwort kam.
    Bei PID 02 zählt die erste Antwort mit einem Code (``00 00`` heißt „kein Freeze
    Frame gespeichert“, das melden z. B. beim Mercedes W177 drei von vier
    Steuergeräten). Ohne Header ist die Reihenfolge der Antworten zufällig und keine
    Antwort einem Steuergerät zuzuordnen; melden bei einem anderen PID mehrere
    Steuergeräte verschiedene Werte, ist der Wert daher unbekannt (Daten ``None``).
    """
    cmd = _freeze_cmd(pid, frame_byte)
    response = elm.query(cmd)
    if response is None:
        return cmd, None, None, False
    rejected = False
    found: list[bytes] = []
    for message in _messages(cmd, response):
        data = _freeze_payload(message, pid, size, frame_byte)
        if data is not None:
            if pid == 0x02 and any(data[:_FREEZE_DTC_SIZE]):
                return cmd, response, data, False
            found.append(data)
        elif message[:3] == b"\x7f\x02\x12":
            rejected = True  # Unterfunktion/Format nicht unterstützt
    if pid != 0x02 and len({data[:size] for data in found}) > 1:
        log.warning("%s: Steuergeräte melden verschiedene Werte, verworfen", cmd)
        return cmd, response, None, False
    if found:
        return cmd, response, found[0], False
    return cmd, response, None, rejected


def _read_frame(
    elm: Elm327, frame_byte: bool, first: tuple[str, str | None, bytes | None, bool]
) -> FreezeFrame:
    dtc: str | None = None
    raw: dict[str, str] = {}
    values: dict[str, float] = {}
    cmd, response, data, _ = first
    if data is not None and response is not None:
        raw[cmd] = response
        if len(data) >= 2 and (data[0] or data[1]):
            dtc = decode_dtc(data[0], data[1])
    for pid, (name, size, decode) in _FREEZE_PIDS.items():
        cmd, response, data, _ = _freeze_data(elm, pid, size, frame_byte)
        if data is None or response is None:
            continue  # z. B. 7F 02 12: dieser PID ist nicht gespeichert
        raw[cmd] = response
        if len(data) >= size:
            values[name] = decode(data)
    return FreezeFrame(dtc, raw, values)


def read_freeze_frame(elm: Elm327) -> FreezeFrame:
    """Liest Frame 00 von Mode 02: auslösender Code und einige häufige Werte.

    Angefragt wird nach SAE J1979 mit Frame-Nummer (``02 <PID> 00``). Antwortet das
    Fahrzeug auf ``020200`` mit ``NO DATA`` oder ``7F 02 12`` (Format nicht
    unterstützt), wird ``0202`` ohne Frame-Nummer versucht, wie es python-OBD sendet
    und manche Steuergeräte bzw. Adapter erwarten. Liefert das eine Antwort, folgt der
    ganze Freeze Frame in diesem Format; sonst (auch bei ``?`` oder unbrauchbarer
    Antwort) bleibt es beim J1979-Format für die übrigen PIDs.
    """
    first = _freeze_data(elm, 0x02, _FREEZE_DTC_SIZE, frame_byte=True)
    _, response, data, rejected = first
    if data is None and (response is None or rejected):
        try:
            short = _freeze_data(elm, 0x02, _FREEZE_DTC_SIZE, frame_byte=False)
        except ElmError as e:
            log.info("0202: %s; bleibe beim Format mit Frame-Nummer", e)
        else:
            if short[2] is not None:
                return _read_frame(elm, False, short)
    return _read_frame(elm, True, first)
