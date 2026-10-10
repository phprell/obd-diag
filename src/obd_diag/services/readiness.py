"""Readiness-Monitore (Mode 01, PID 01): Status der Eigendiagnosen fürs Abgas-System.

Aufbau der vier Datenbytes A-D nach SAE J1979 (ISO 15031-5), Tabelle zu Mode 01
PID 01 wie in Wikipedia „OBD-II PIDs“ (Abschnitt „Service 01 PID 01“, abgerufen
2026-10)::

    A7      MIL (Kontrollleuchte) an
    A6-A0   Zahl der gespeicherten Fehlercodes (dieses Steuergeräts)
    B3      0 = Ottomotor (spark ignition), 1 = Diesel (compression ignition)
    B0/B4   Verbrennungsaussetzer   unterstützt / nicht abgeschlossen
    B1/B5   Kraftstoffsystem        unterstützt / nicht abgeschlossen
    B2/B6   Komponenten             unterstützt / nicht abgeschlossen
    B7      reserviert
    C       Bit n: Monitor n unterstützt
    D       Bit n: Monitor n nicht abgeschlossen

    Bit  Otto (B3 = 0)              Diesel (B3 = 1)
    0    Katalysator                NMHC-Katalysator
    1    Katalysatorheizung         NOx-Nachbehandlung / SCR
    2    Tankentlüftung (EVAP)      reserviert
    3    Sekundärluftsystem         Ladedruck
    4    Klimaanlagen-Kältemittel   reserviert
    5    Lambdasonde                Abgassensor
    6    Lambdasondenheizung        Partikelfilter
    7    AGR und/oder VVT           AGR und/oder VVT

Bit C4/D4 (Otto: Kältemittel der Klimaanlage) ist in neueren J1979-Ausgaben
reserviert, wird aber weiter dekodiert, weil ältere Fahrzeuge es setzen. Reservierte
Bits werden ignoriert. Nicht unterstützte Monitore erscheinen mit ``NOT_SUPPORTED``.

Bezug zur Abgasuntersuchung (AU): Das Ergebnis heißt bewusst „alle Tests
abgeschlossen“ und nicht „AU-bereit“. Nach der AU-Richtlinie (Verkehrsblatt 19/2017,
Leitfaden 5.01, gültig ab 01.01.2018) besteht die AU bei allen OBD-Fahrzeugen aus
einer Funktionsprüfung OBD und einer Endrohrmessung; die Bewertung der
Prüfbereitschaftstests entfällt, wenn alle unterstützten Tests durchgeführt sind
(Hella Gutmann, „Informationen zum Leitfaden 5.01“, BD0059, 12/2017). Schon vorher
galt: Ist der Readiness-Code nicht gesetzt, wird stattdessen gemessen; das allein ist
kein erheblicher Mangel. Eine zitierfähige, allgemeine Liste tolerierter offener
Monitore gibt es nicht (Stand 2026-10-07), die Behandlung hängt von Fahrzeug und
Prüfablauf ab. Deshalb gibt es hier keine AU-Bewertung.
"""

from dataclasses import dataclass
from enum import StrEnum

from obd_diag.i18n import N_, tr
from obd_diag.protocol.elm327 import Elm327, ElmError
from obd_diag.protocol.frames import split_messages

# Bezeichnung des Gesamtergebnisses und Hinweis dazu, gleich in Kommandozeile, Bericht
# und Oberfläche; deutsch, zur Ausgabe mit ``tr`` übersetzen
ALL_COMPLETE_LABEL = N_("Alle Tests abgeschlossen")
AU_NOTE = N_(
    "Das ist keine AU-Bewertung: Zur Abgasuntersuchung gehört immer auch eine "
    "Abgasmessung am Endrohr, und ob einzelne offene Tests toleriert werden, hängt vom "
    "Fahrzeug und vom Prüfablauf ab."
)


class MonitorState(StrEnum):
    COMPLETE = "complete"  # Test abgeschlossen
    INCOMPLETE = "incomplete"  # Test noch nicht gelaufen, z. B. nach dem Löschen
    NOT_SUPPORTED = "not_supported"


@dataclass(frozen=True)
class Monitor:
    key: str  # stabil, z. B. "misfire", "catalyst", "egr"
    name: str  # deutsch, z. B. "Verbrennungsaussetzer"; zur Anzeige ``tr(name)``
    state: MonitorState


@dataclass(frozen=True)
class ReadinessStatus:
    mil_on: bool
    dtc_count: int  # vom Steuergerät gemeldete Zahl gespeicherter Codes
    compression_ignition: bool  # True: Diesel-Monitore, False: Otto-Monitore
    monitors: tuple[Monitor, ...]

    @property
    def all_complete(self) -> bool:
        """Alle unterstützten Monitore abgeschlossen.

        Das ist kein AU-Ergebnis: Seit der AU-Richtlinie 2018 (Leitfaden 5.01) gehört zur
        AU immer auch die Endrohrmessung; offene Monitore führen nicht pauschal zum
        Nichtbestehen, und ob einzelne toleriert werden, hängt von Fahrzeug und
        Prüfablauf ab (siehe README, Abschnitt Readiness).
        """
        return all(m.state is not MonitorState.INCOMPLETE for m in self.monitors)

    @property
    def ready(self) -> bool:
        """Alter Name für ``all_complete`` (bleibt zur Kompatibilität)."""
        return self.all_complete


# Kontinuierliche Monitore in Byte B: (Bit, Schlüssel, Name); "nicht abgeschlossen" = Bit + 4
_CONTINUOUS = (
    (0, "misfire", N_("Verbrennungsaussetzer")),
    (1, "fuel_system", N_("Kraftstoffsystem")),
    (2, "components", N_("Komponenten")),
)

# Nicht kontinuierliche Monitore in Byte C (unterstützt) und D (nicht abgeschlossen)
_SPARK = (
    (0, "catalyst", N_("Katalysator")),
    (1, "heated_catalyst", N_("Katalysatorheizung")),
    (2, "evap", N_("Tankentlüftung")),
    (3, "secondary_air", N_("Sekundärluftsystem")),
    (4, "ac_refrigerant", N_("Klimaanlage (Kältemittel)")),
    (5, "oxygen_sensor", N_("Lambdasonde")),
    (6, "oxygen_sensor_heater", N_("Lambdasondenheizung")),
    (7, "egr", N_("Abgasrückführung")),  # AGR und/oder variable Ventilsteuerung (VVT)
)
_COMPRESSION = (
    (0, "nmhc_catalyst", N_("NMHC-Katalysator")),
    (1, "nox_scr", N_("NOx-Nachbehandlung (SCR)")),
    (3, "boost_pressure", N_("Ladedruck")),
    (5, "exhaust_gas_sensor", N_("Abgassensor")),
    (6, "pm_filter", N_("Partikelfilter")),
    (7, "egr", N_("Abgasrückführung")),  # AGR und/oder VVT
)


def _state(supported: int, incomplete: int, bit: int) -> MonitorState:
    if not supported >> bit & 1:
        return MonitorState.NOT_SUPPORTED
    return MonitorState.INCOMPLETE if incomplete >> bit & 1 else MonitorState.COMPLETE


def decode_readiness(data: bytes) -> ReadinessStatus:
    """Dekodiert die vier Datenbytes A-D der Antwort auf ``0101``.

    Weitere Bytes werden ignoriert; ``ValueError`` bei weniger als vier.
    """
    if len(data) < 4:
        raise ValueError(
            tr("PID 01 braucht vier Datenbytes, nicht {count}").format(count=len(data))
        )
    a, b, c, d = data[:4]
    diesel = bool(b & 0x08)
    monitors = [Monitor(key, name, _state(b, b >> 4, bit)) for bit, key, name in _CONTINUOUS]
    monitors += [
        Monitor(key, name, _state(c, d, bit))
        for bit, key, name in (_COMPRESSION if diesel else _SPARK)
    ]
    return ReadinessStatus(
        mil_on=bool(a & 0x80),
        dtc_count=a & 0x7F,
        compression_ignition=diesel,
        monitors=tuple(monitors),
    )


_RANK = {MonitorState.NOT_SUPPORTED: 0, MonitorState.COMPLETE: 1, MonitorState.INCOMPLETE: 2}


def combine_readiness(statuses: list[ReadinessStatus]) -> ReadinessStatus:
    """Fasst die Antworten mehrerer Steuergeräte zu einem Gesamtstand zusammen.

    Maßgeblich für die Motorart ist das erste Steuergerät (in der Regel das
    Motorsteuergerät); Steuergeräte mit anderer Motorart-Kennung werden für die
    Monitore ignoriert, weil ihre Bits C/D dann anders belegt sind. MIL: an, wenn ein
    Steuergerät sie meldet; Codezahl: Summe (Mode 03 liefert ja auch die Codes aller
    Steuergeräte). Je Monitor zählt der schlechteste Stand: nicht abgeschlossen vor
    abgeschlossen vor nicht unterstützt.
    """
    if not statuses:
        raise ValueError(tr("keine Readiness-Antwort"))
    first = statuses[0]
    same = [s for s in statuses if s.compression_ignition == first.compression_ignition]
    monitors = []
    for i, monitor in enumerate(first.monitors):
        state = max((s.monitors[i].state for s in same), key=_RANK.__getitem__)
        monitors.append(Monitor(monitor.key, monitor.name, state))
    return ReadinessStatus(
        mil_on=any(s.mil_on for s in statuses),
        dtc_count=sum(s.dtc_count for s in statuses),
        compression_ignition=first.compression_ignition,
        monitors=tuple(monitors),
    )


def read_readiness(elm: Elm327) -> ReadinessStatus | None:
    """``None``, wenn das Steuergerät PID 01 nicht beantwortet.

    Antworten mehrere Steuergeräte (z. B. Motor und Getriebe), werden sie mit
    ``combine_readiness`` zusammengefasst. Negative oder zu kurze Antworten zählen als
    nicht beantwortet. ``ElmError`` bei Antworten, die keine Hex-Daten sind.
    """
    response = elm.query("0101")
    if response is None:
        return None
    try:
        messages = split_messages(response)
    except ValueError as e:
        raise ElmError(f"0101: {e}") from e
    statuses = [decode_readiness(m[2:]) for m in messages if len(m) >= 6 and m[:2] == b"\x41\x01"]
    return combine_readiness(statuses) if statuses else None
