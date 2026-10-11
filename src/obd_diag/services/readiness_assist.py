"""Readiness-Assistent: welche Tests noch offen sind und welche Fahrt sie abschließt.

Grundlage sind zwei Mode-01-PIDs nach SAE J1979 (Wikipedia „OBD-II PIDs“, Service 01,
PID 01 und PID 41, abgerufen 2026-10):

- PID 01 meldet den Stand **seit dem Löschen** der Fehlercodes
  (``services/readiness.py``).
- PID 41 meldet den Stand **in diesem Fahrzyklus**, also seit dem Einschalten der Zündung
  bzw. dem Motorstart. Byte A ist reserviert, die Bytes B bis D sind aufgebaut wie bei
  PID 01, nur heißt das erste Bit je Monitor nicht „unterstützt“, sondern „in diesem
  Zyklus freigegeben“ (0 = ausgesetzt oder nicht vorhanden), das zweite „in diesem Zyklus
  nicht abgeschlossen“. Ein nicht freigegebener Monitor meldet dort immer 0.

Welche Belegung Byte C/D hat (Otto oder Diesel), kommt aus der zusammengefassten
Antwort auf PID 01 (``combine_readiness``), nicht aus Byte B von PID 41: dessen Bit 3 ist
nicht in allen Ausgaben der Norm als Motorart belegt. Antworten mehrere Steuergeräte,
zählt je Monitor der schlechteste Stand (noch offen vor abgeschlossen vor ausgesetzt);
das hängt nicht von der Reihenfolge der Antworten ab (ohne Header wechselt sie am W177
bei jeder Anfrage).

Die Fahrhinweise sind allgemeine Erfahrungswerte, keine Norm: J1979 legt nur fest, wie
der Status gemeldet wird. Wann ein Monitor läuft (Temperaturen, Geschwindigkeit, Dauer,
Tankfüllung), bestimmt jeder Hersteller selbst, und oft braucht es mehrere Fahrten.
Gesendet werden nur lesende Anfragen: ``initialize``, ``protocol``, ``0101``, ``0141``.
"""

import dataclasses
import logging
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from obd_diag.i18n import N_, tr
from obd_diag.protocol.elm327 import Elm327, ElmError
from obd_diag.protocol.frames import split_messages
from obd_diag.services.readiness import (
    CONTINUOUS_MONITORS,
    MonitorState,
    ReadinessStatus,
    read_readiness,
    specific_monitors,
)

log = logging.getLogger(__name__)


class CycleState(StrEnum):
    COMPLETE = "complete"  # in dieser Fahrt abgeschlossen
    INCOMPLETE = "incomplete"  # freigegeben, aber noch nicht abgeschlossen
    DISABLED = "disabled"  # in dieser Fahrt ausgesetzt (oder beim Steuergerät nicht vorhanden)


# deutsch; zur Anzeige ``tr``
CYCLE_STATE_LABELS = {
    CycleState.COMPLETE: N_("abgeschlossen"),
    CycleState.INCOMPLETE: N_("noch offen"),
    CycleState.DISABLED: N_("ausgesetzt"),
}
CYCLE_STATE_TEXTS = {
    CycleState.COMPLETE: N_(
        "In dieser Fahrt abgeschlossen. Steht er seit dem Löschen noch offen, nach der "
        "nächsten Fahrt erneut prüfen."
    ),
    CycleState.INCOMPLETE: N_("In dieser Fahrt noch nicht abgeschlossen."),
    CycleState.DISABLED: N_(
        "In dieser Fahrt ausgesetzt: Die Bedingungen zum Start waren nicht erfüllt (z. B. "
        "Temperatur oder Tankfüllung). In einer späteren Fahrt erneut versuchen."
    ),
}

# Typische Fahrt je Monitor (Schlüssel wie in ``services/readiness.py``)
HINTS = {
    "misfire": N_(
        "Läuft bei normaler Fahrt laufend mit; meist reichen einige Minuten mit "
        "betriebswarmem Motor."
    ),
    "fuel_system": N_(
        "Läuft laufend mit; einige Minuten Fahrt mit betriebswarmem Motor, auch im Leerlauf."
    ),
    "components": N_("Läuft laufend mit; normale Fahrt genügt."),
    "catalyst": N_(
        "Mit betriebswarmem Motor einige Minuten gleichmäßig fahren, etwa 60 bis 90 km/h, "
        "ohne stark zu beschleunigen."
    ),
    "heated_catalyst": N_("Kaltstart nach mehreren Stunden Standzeit, dann einige Minuten fahren."),
    "evap": N_(
        "Braucht meist einen Kaltstart nach einer Nacht Standzeit, den Tank zwischen einem "
        "Viertel und drei Vierteln voll und mäßige Außentemperatur; danach fahren mit "
        "einigen Leerlaufphasen. Kann mehrere Tage dauern."
    ),
    "secondary_air": N_(
        "Läuft kurz nach einem Kaltstart: Motor nach mehreren Stunden Standzeit starten und "
        "die ersten Minuten fahren oder laufen lassen."
    ),
    "ac_refrigerant": N_("Klimaanlage bei passender Außentemperatur einige Minuten einschalten."),
    "oxygen_sensor": N_(
        "Mit betriebswarmem Motor einige Minuten gleichmäßig fahren (etwa 60 bis 90 km/h), "
        "danach Gas wegnehmen, ohne zu bremsen, und kurz im Leerlauf stehen."
    ),
    "oxygen_sensor_heater": N_(
        "Läuft meist in den ersten Minuten nach einem Kaltstart; Motor nach mehreren "
        "Stunden Standzeit starten und losfahren."
    ),
    "egr": N_(
        "Mit betriebswarmem Motor mehrmals gleichmäßig beschleunigen und danach Gas "
        "wegnehmen, ohne zu bremsen (Schubphasen), bei mittlerer Geschwindigkeit."
    ),
    "nmhc_catalyst": N_(
        "Längere gleichmäßige Fahrt mit betriebswarmem Motor, etwa 15 bis 20 Minuten über "
        "Land oder Autobahn, damit der Katalysator heiß wird."
    ),
    "nox_scr": N_(
        "Längere Fahrt mit heißem Abgassystem, etwa 20 Minuten über Land oder Autobahn; "
        "genug AdBlue muss im Tank sein."
    ),
    "boost_pressure": N_(
        "Mit betriebswarmem Motor mehrmals zügig beschleunigen, etwa im dritten oder "
        "vierten Gang, dann wieder gleichmäßig fahren."
    ),
    "exhaust_gas_sensor": N_(
        "Längere Fahrt mit heißem Abgassystem, etwa 20 Minuten über Land oder Autobahn bei "
        "gleichmäßiger Geschwindigkeit, dazwischen Schubphasen (Gas wegnehmen, ohne zu "
        "bremsen). Beim Diesel oft erst nach mehreren solchen Fahrten."
    ),
    "pm_filter": N_(
        "Längere Fahrt mit etwas Last, etwa 20 bis 30 Minuten Autobahn. Der Test läuft oft "
        "während oder nach einer Regeneration des Partikelfilters."
    ),
}

# Für Schlüssel ohne eigenen Hinweis (z. B. aus einer fremden Sitzungsdatei)
DEFAULT_HINT = N_("Normale Fahrt mit betriebswarmem Motor, am besten über mehrere Tage.")

GENERAL_NOTE = N_(
    "Die genauen Bedingungen legt jeder Hersteller selbst fest; die Hinweise beschreiben "
    "typische Fahrten, keine Garantie. Oft braucht es mehrere Fahrten, darunter einen "
    "Kaltstart nach mehreren Stunden Standzeit."
)
CODES_NOTE = N_(
    "Es sind Fehlercodes gespeichert oder die Motorkontrollleuchte ist an. Betrifft ein "
    "Fehler Teile, die ein Test braucht, läuft dieser Test oft erst, wenn die Ursache "
    "behoben ist."
)
CYCLE_NOTE = N_(
    "Der Stand dieser Fahrt gilt seit dem Einschalten der Zündung und beginnt beim nächsten "
    "Start von vorn: am besten am Ende der Fahrt bei laufendem Motor prüfen."
)
NO_CYCLE_NOTE = N_(
    "Das Fahrzeug meldet den Stand dieser Fahrt (PID 41) nicht; gezeigt ist nur der Stand "
    "seit dem Löschen."
)


@dataclass(frozen=True)
class CycleMonitor:
    key: str
    name: str  # deutsch; zur Anzeige ``tr(name)``
    state: CycleState


@dataclass(frozen=True)
class DriveCycleStatus:
    """Antwort auf PID 41: Stand der Monitore in diesem Fahrzyklus."""

    monitors: tuple[CycleMonitor, ...]

    def state(self, key: str) -> CycleState | None:
        for m in self.monitors:
            if m.key == key:
                return m.state
        return None


def _cycle_state(enabled: int, incomplete: int, bit: int) -> CycleState:
    if not enabled >> bit & 1:
        return CycleState.DISABLED
    return CycleState.INCOMPLETE if incomplete >> bit & 1 else CycleState.COMPLETE


def decode_drive_cycle(data: bytes, compression_ignition: bool) -> DriveCycleStatus:
    """Dekodiert die vier Datenbytes A-D der Antwort auf ``0141``.

    ``compression_ignition`` (aus PID 01) wählt die Belegung von Byte C/D. Weitere Bytes
    werden ignoriert; ``ValueError`` bei weniger als vier.
    """
    if len(data) < 4:
        raise ValueError(
            tr("PID 41 braucht vier Datenbytes, nicht {count}").format(count=len(data))
        )
    _, b, c, d = data[:4]
    monitors = [
        CycleMonitor(key, name, _cycle_state(b, b >> 4, bit))
        for bit, key, name in CONTINUOUS_MONITORS
    ]
    monitors += [
        CycleMonitor(key, name, _cycle_state(c, d, bit))
        for bit, key, name in specific_monitors(compression_ignition)
    ]
    return DriveCycleStatus(tuple(monitors))


_RANK = {CycleState.DISABLED: 0, CycleState.COMPLETE: 1, CycleState.INCOMPLETE: 2}


def combine_drive_cycle(statuses: list[DriveCycleStatus]) -> DriveCycleStatus:
    """Je Monitor der schlechteste Stand aller Steuergeräte (unabhängig von der Reihenfolge).

    Alle Antworten müssen mit derselben Motorart dekodiert sein.
    """
    if not statuses:
        raise ValueError(tr("keine Antwort auf PID 41"))
    monitors = tuple(
        CycleMonitor(
            m.key, m.name, max((s.monitors[i].state for s in statuses), key=_RANK.__getitem__)
        )
        for i, m in enumerate(statuses[0].monitors)
    )
    return DriveCycleStatus(monitors)


def read_drive_cycle(elm: Elm327, compression_ignition: bool) -> DriveCycleStatus | None:
    """``0141``; ``None``, wenn kein Steuergerät PID 41 beantwortet.

    Negative oder zu kurze Antworten zählen als nicht beantwortet, mehrere Steuergeräte
    werden mit ``combine_drive_cycle`` zusammengefasst. ``ElmError`` bei Antworten, die
    keine Hex-Daten sind.
    """
    response = elm.query("0141")
    if response is None:
        return None
    try:
        messages = split_messages(response)
    except ValueError as e:
        raise ElmError(f"0141: {e}") from e
    statuses = [
        decode_drive_cycle(m[2:], compression_ignition)
        for m in messages
        if len(m) >= 6 and m[:2] == b"\x41\x41"
    ]
    return combine_drive_cycle(statuses) if statuses else None


@dataclass(frozen=True)
class Advice:
    """Ein seit dem Löschen offener Test mit Stand dieser Fahrt und Fahrhinweis."""

    key: str
    name: str  # deutsch; zur Anzeige ``tr``
    cycle: CycleState | None  # None: PID 41 nicht gemeldet
    hint: str  # deutsch; zur Anzeige ``tr``


def advise(readiness: ReadinessStatus, cycle: DriveCycleStatus | None) -> list[Advice]:
    """Die seit dem Löschen offenen Tests in der Reihenfolge von PID 01."""
    return [
        Advice(
            m.key,
            m.name,
            None if cycle is None else cycle.state(m.key),
            HINTS.get(m.key, DEFAULT_HINT),
        )
        for m in readiness.monitors
        if m.state is MonitorState.INCOMPLETE
    ]


def notes(
    readiness: ReadinessStatus, cycle: DriveCycleStatus | None, *, cycle_read: bool = True
) -> list[str]:
    """Allgemeine Hinweise (deutsch, zur Anzeige ``tr``); leer, wenn nichts offen ist.

    ``cycle_read``: PID 41 wurde abgefragt (``cycle`` ist ``None`` also, weil das
    Fahrzeug nicht antwortet, nicht weil niemand gefragt hat).
    """
    if readiness.all_complete:
        return []
    found = []
    if readiness.mil_on or readiness.dtc_count:
        found.append(CODES_NOTE)
    if cycle is not None:
        found.append(CYCLE_NOTE)
    elif cycle_read:
        found.append(NO_CYCLE_NOTE)
    found.append(GENERAL_NOTE)
    return found


@dataclass(frozen=True)
class ReadinessCheck:
    """Ergebnis von ``check_readiness``."""

    created: datetime
    adapter: str
    protocol: str
    readiness: ReadinessStatus | None  # None: PID 01 nicht beantwortet
    drive_cycle: DriveCycleStatus | None  # None: PID 41 nicht beantwortet

    @property
    def advice(self) -> list[Advice]:
        return [] if self.readiness is None else advise(self.readiness, self.drive_cycle)

    @property
    def notes(self) -> list[str]:
        return [] if self.readiness is None else notes(self.readiness, self.drive_cycle)


def check_readiness(elm: Elm327) -> ReadinessCheck:
    """Adapter initialisieren, Protokoll aushandeln, PID 01 und PID 41 lesen; nur lesend.

    Befehle: ``initialize`` (ATZ ... ATSP0), ``protocol`` (0100, ATDPN, ATDP), ``0101``
    und, wenn PID 01 beantwortet wurde, ``0141``. Ein ``ElmError`` bei PID 01 oder 41
    lässt nur diesen Teil leer; Verbindungsfehler (``TransportError``) gehen an den
    Aufrufer.
    """
    created = datetime.now().astimezone()
    adapter = elm.initialize()
    protocol = elm.protocol().name
    readiness = drive_cycle = None
    try:
        readiness = read_readiness(elm)
    except ElmError as e:
        log.warning("Readiness nicht lesbar: %s", e)
    if readiness is not None:
        try:
            drive_cycle = read_drive_cycle(elm, readiness.compression_ignition)
        except ElmError as e:
            log.warning("PID 41 nicht lesbar: %s", e)
    return ReadinessCheck(created, adapter, protocol, readiness, drive_cycle)


def check_to_dict(check: ReadinessCheck) -> dict[str, Any]:
    """JSON-taugliche Form (``obd-diag readiness --json``); Texte in der eingestellten
    Sprache, Schlüssel und Zustände wie im Code."""
    readiness: dict[str, Any] | None = None
    if check.readiness is not None:
        readiness = dataclasses.asdict(check.readiness)
        readiness["all_complete"] = check.readiness.all_complete
    return {
        "created": check.created.isoformat(),
        "adapter": check.adapter,
        "protocol": check.protocol,
        "readiness": readiness,
        "drive_cycle": None if check.drive_cycle is None else dataclasses.asdict(check.drive_cycle),
        "advice": [
            {
                "key": a.key,
                "name": tr(a.name),
                "drive_cycle": None if a.cycle is None else a.cycle.value,
                "hint": tr(a.hint),
            }
            for a in check.advice
        ],
        "notes": [tr(n) for n in check.notes],
    }
