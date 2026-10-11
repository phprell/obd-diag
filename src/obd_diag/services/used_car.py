"""Gebrauchtwagen-Check: Wurden die Fehlercodes vor kurzem gelöscht? Nur lesend.

Beim Löschen (Mode 04) setzt jedes Steuergerät nach SAE J1979 diese Zähler (Mode 01)
auf null, und sie laufen danach mit dem Fahren wieder hoch:

- PID ``30``: Warmläufe seit dem Löschen (0 bis 255; ein Warmlauf: Kühlmittel um
  mindestens 22 °C gestiegen und mindestens 70 °C erreicht)
- PID ``31``: Strecke seit dem Löschen in km (0 bis 65535)
- PID ``4E``: Zeit seit dem Löschen in Minuten (0 bis 65535)

Dazu zwei Zähler zur Motorkontrollleuchte (MIL), die beim Löschen ebenfalls auf null
gehen: PID ``21`` (Strecke mit Warnleuchte an, km) und ``4D`` (Zeit mit Warnleuchte an,
Minuten). Permanente Codes (Mode 0A) lassen sich gar nicht löschen; sie kommen aus dem
Scan, Warnleuchte und offene Tests aus der Readiness (PID 01).

Gefragt wird jede PID einmal, ohne vorher die unterstützten PIDs zu lesen (``NO DATA``
heißt: nicht gemeldet). Antworten mehrere Steuergeräte, zählt bei den Zählern seit dem
Löschen der kleinste Wert und bei den Warnleuchten-Zählern der größte: Ohne Header kommen
die Antworten in wechselnder Reihenfolge (W177), und für die Frage „kürzlich gelöscht?“
zählt das Steuergerät, das es am deutlichsten zeigt.

Die Grenzen für „kürzlich“ (``RECENT_*``) sind eine Faustregel, keine Norm: Nach dem
Löschen brauchen die Readiness-Tests meist einige Fahrten über ein paar hundert Kilometer,
bis sie wieder abgeschlossen sind. Auch ein Batterie- oder Steuergerätewechsel und jede
Werkstatt, die Codes löscht, setzen die Zähler zurück; ein Befund ist also ein Grund
nachzufragen, kein Beweis.
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from obd_diag.i18n import N_, language, tr, trn
from obd_diag.protocol.elm327 import Elm327, ElmError
from obd_diag.protocol.frames import split_messages
from obd_diag.protocol.pids import PIDS, PidSpec
from obd_diag.services.diagnostics import DtcKind, ScanResult, scan
from obd_diag.services.readiness import MonitorState, ReadinessStatus, read_readiness

# Faustregel für „vor kurzem gelöscht“: weniger als so viele Warmläufe, Kilometer oder
# Minuten seit dem Löschen.
RECENT_WARMUPS = 10
RECENT_DISTANCE_KM = 200
RECENT_TIME_MIN = 300

# Überschrift und Hinweis, gleich in Kommandozeile, Bericht und Oberfläche; deutsch,
# zur Ausgabe mit ``tr`` übersetzen
TITLE = N_("Gebrauchtwagen-Check")
NOTE = N_(
    "Faustregel, kein Beweis: Auch eine Werkstatt, ein Batteriewechsel oder ein "
    "Steuergerätetausch setzt die Zähler zurück. Bei einem Befund den Verkäufer fragen, "
    "wann und warum die Fehlercodes gelöscht wurden."
)


@dataclass(frozen=True)
class ClearCounters:
    """Zähler seit dem letzten Löschen der Fehlercodes; ``None``: nicht gemeldet."""

    warmups: int | None = None  # PID 30
    distance_km: int | None = None  # PID 31
    time_min: int | None = None  # PID 4E
    mil_distance_km: int | None = None  # PID 21
    mil_time_min: int | None = None  # PID 4D

    @property
    def available(self) -> bool:
        """Mindestens ein Zähler seit dem Löschen (30, 31, 4E) ist bekannt."""
        return any(v is not None for v in (self.warmups, self.distance_km, self.time_min))


# Feld in ``ClearCounters``, Schlüssel in ``PIDS``, kleinster (True) oder größter Wert
_FIELDS: tuple[tuple[str, str, bool], ...] = (
    ("warmups", "warmups_since_clear", True),
    ("distance_km", "distance_since_clear", True),
    ("time_min", "time_since_clear", True),
    ("mil_distance_km", "distance_with_mil", False),
    ("mil_time_min", "time_with_mil", False),
)


def _all_values(elm: Elm327, spec: PidSpec) -> list[float]:
    """Gültige Werte aller Steuergeräte, die auf ``01<pid>`` antworten."""
    cmd = f"01{spec.pid:02X}"
    response = elm.query(cmd)
    if response is None:
        return []
    values: list[float] = []
    for line in response.splitlines():
        try:
            messages = split_messages(line)
        except ValueError:
            continue  # Zeile ohne Hex-Daten; die übrigen zählen trotzdem
        for m in messages:
            if m[:2] == bytes([0x41, spec.pid]) and len(m) >= 2 + spec.size:
                value = spec.decode(m[2 : 2 + spec.size])
                if value is not None:
                    values.append(value)
    return values


def read_clear_counters(elm: Elm327) -> ClearCounters:
    """Liest PID 30, 31, 4E, 21 und 4D (je eine Anfrage, in dieser Reihenfolge).

    Ein Adapterfehler (``ElmError``) lässt nur diesen Zähler leer; Verbindungsfehler
    (``TransportError``) gehen an den Aufrufer. Nur nach ausgehandeltem Protokoll
    aufrufen.
    """
    found: dict[str, int | None] = {}
    for field, key, smallest in _FIELDS:
        try:
            values = _all_values(elm, PIDS[key])
        except ElmError:
            values = []
        pick: Callable[[list[float]], float] = min if smallest else max
        found[field] = round(pick(values)) if values else None
    return ClearCounters(**found)


@dataclass(frozen=True)
class UsedCarReading:
    """Was der Check braucht: Scan (permanente Codes), Readiness, Zähler."""

    scan: ScanResult
    readiness: ReadinessStatus | None
    counters: ClearCounters


def read_used_car(elm: Elm327) -> UsedCarReading:
    """Nur der Gebrauchtwagen-Check (``obd-diag used-car``): Scan, ``0101``, Zähler.

    Die vollständige Diagnose (``run_diagnosis``) liest dasselbe mit; ohne Katalog,
    denn der Check zeigt nur die Codes selbst.
    """
    result = scan(elm, None)
    try:
        readiness: ReadinessStatus | None = read_readiness(elm)
    except ElmError:
        readiness = None
    return UsedCarReading(result, readiness, read_clear_counters(elm))


class Verdict(StrEnum):
    INCONSPICUOUS = "inconspicuous"  # Zähler lesbar, nichts gefunden
    SUSPICIOUS = "suspicious"  # mindestens ein Befund
    UNKNOWN = "unknown"  # keine Zähler, nichts gefunden


VERDICT_LABELS = {
    Verdict.INCONSPICUOUS: N_("Unauffällig"),
    Verdict.SUSPICIOUS: N_("Auffällig"),
    Verdict.UNKNOWN: N_("Nicht beurteilbar"),
}


class Level(StrEnum):
    WARNING = "warning"  # Befund: nachfragen
    INFO = "info"
    OK = "ok"


@dataclass(frozen=True)
class Finding:
    level: Level
    text: str  # in der beim Bewerten eingestellten Sprache


@dataclass(frozen=True)
class UsedCarCheck:
    verdict: Verdict
    recently_cleared: bool
    findings: tuple[Finding, ...]

    @property
    def label(self) -> str:
        return tr(VERDICT_LABELS[self.verdict])


def _number(value: int) -> str:
    text = f"{value:,}"
    return text.replace(",", ".") if language() == "de" else text


def _duration(minutes: int) -> str:
    if minutes < 120:
        return trn("{n} Minute", "{n} Minuten", minutes).format(n=_number(minutes))
    hours = minutes // 60
    return trn("{n} Stunde", "{n} Stunden", hours).format(n=_number(hours))


def counter_rows(counters: ClearCounters) -> list[tuple[str, str]]:
    """Bezeichnung und Wert je Zähler in der eingestellten Sprache, auch „nicht gemeldet“."""
    missing = tr("nicht gemeldet")
    warmups = counters.warmups
    warmups_text = (
        missing
        if warmups is None
        # 255 ist der größte Wert, den PID 30 darstellen kann
        else (tr("{n} oder mehr") if warmups >= 255 else "{n}").format(n=_number(warmups))
    )

    def km(value: int | None) -> str:
        return missing if value is None else f"{_number(value)} km"

    def minutes(value: int | None) -> str:
        return missing if value is None else _duration(value)

    return [
        (tr("Warmläufe seit dem Löschen"), warmups_text),
        (tr("Strecke seit dem Löschen"), km(counters.distance_km)),
        (tr("Zeit seit dem Löschen"), minutes(counters.time_min)),
        (tr("Strecke mit Warnleuchte an"), km(counters.mil_distance_km)),
        (tr("Zeit mit Warnleuchte an"), minutes(counters.mil_time_min)),
    ]


def assess(
    counters: ClearCounters | None,
    scan: ScanResult,
    readiness: ReadinessStatus | None,
) -> UsedCarCheck:
    """Bewertet Zähler, permanente Codes, Warnleuchte und offene Tests.

    Texte in der eingestellten Sprache; bei einem Sprachwechsel neu aufrufen.
    ``counters`` ist ``None`` bei Sitzungen, in denen die Zähler nicht gelesen wurden.
    """
    c = counters or ClearCounters()
    findings: list[Finding] = []

    recent: list[str] = []
    if c.warmups is not None and c.warmups < RECENT_WARMUPS:
        recent.append(trn("{n} Warmlauf", "{n} Warmläufe", c.warmups).format(n=c.warmups))
    if c.distance_km is not None and c.distance_km < RECENT_DISTANCE_KM:
        recent.append(f"{_number(c.distance_km)} km")
    if c.time_min is not None and c.time_min < RECENT_TIME_MIN:
        recent.append(_duration(c.time_min))
    if recent:
        findings.append(
            Finding(
                Level.WARNING,
                tr("Fehlercodes vor kurzem gelöscht: erst {since} seit dem Löschen.").format(
                    since=", ".join(recent)
                ),
            )
        )
    elif c.available:
        since = [
            trn("{n} Warmlauf", "{n} Warmläufe", c.warmups).format(n=_number(c.warmups))
            if c.warmups is not None
            else None,
            f"{_number(c.distance_km)} km" if c.distance_km is not None else None,
            _duration(c.time_min) if c.time_min is not None else None,
        ]
        findings.append(
            Finding(
                Level.OK,
                tr("Nicht vor kurzem gelöscht: {since} seit dem letzten Löschen.").format(
                    since=", ".join(s for s in since if s is not None)
                ),
            )
        )
    else:
        findings.append(
            Finding(
                Level.INFO,
                tr(
                    "Das Fahrzeug meldet nicht, wie lange das Löschen der Fehlercodes her ist "
                    "(PID 30, 31, 4E); ob kürzlich gelöscht wurde, lässt sich nicht sagen."
                ),
            )
        )

    if readiness is not None:
        incomplete = [tr(m.name) for m in readiness.monitors if m.state is MonitorState.INCOMPLETE]
        if incomplete:
            names = ", ".join(incomplete)
            text = (
                trn(
                    "Passend dazu ist {n} Readiness-Test nicht abgeschlossen: {names}.",
                    "Passend dazu sind {n} Readiness-Tests nicht abgeschlossen: {names}.",
                    len(incomplete),
                )
                if recent
                else trn(
                    "{n} Readiness-Test nicht abgeschlossen: {names}. Das kann auch an den "
                    "Fahrbedingungen liegen.",
                    "{n} Readiness-Tests nicht abgeschlossen: {names}. Das kann auch an den "
                    "Fahrbedingungen liegen.",
                    len(incomplete),
                )
            )
            findings.append(Finding(Level.INFO, text.format(n=len(incomplete), names=names)))

    mil_on = readiness is not None and readiness.mil_on
    mil_was_on = bool(c.mil_distance_km) or bool(c.mil_time_min)
    if mil_on or mil_was_on:
        since = [
            f"{_number(c.mil_distance_km)} km" if c.mil_distance_km else None,
            _duration(c.mil_time_min) if c.mil_time_min else None,
        ]
        shown = ", ".join(s for s in since if s is not None)
        if mil_on:
            text = tr("Die Motorkontrollleuchte ist an.")
            if shown:
                text += " " + tr("Gefahren mit Warnleuchte: {since}.").format(since=shown)
        else:
            text = tr(
                "Die Motorkontrollleuchte war seit dem letzten Löschen an: {since} mit "
                "Warnleuchte, jetzt ist sie aus."
            ).format(since=shown)
        findings.append(Finding(Level.WARNING, text))
    elif readiness is not None or c.mil_distance_km is not None or c.mil_time_min is not None:
        findings.append(Finding(Level.OK, tr("Motorkontrollleuchte aus.")))

    permanent = sorted({code.code for code in scan.codes if code.kind is DtcKind.PERMANENT})
    if permanent:
        findings.append(
            Finding(
                Level.WARNING,
                trn(
                    "Permanenter Fehlercode: {codes}. Er lässt sich nicht löschen; das "
                    "Steuergerät entfernt ihn erst, wenn es den Fehler nicht mehr feststellt.",
                    "Permanente Fehlercodes: {codes}. Sie lassen sich nicht löschen; das "
                    "Steuergerät entfernt sie erst, wenn es den Fehler nicht mehr feststellt.",
                    len(permanent),
                ).format(codes=", ".join(permanent)),
            )
        )
    else:
        findings.append(Finding(Level.OK, tr("Keine permanenten Fehlercodes gemeldet.")))

    if any(f.level is Level.WARNING for f in findings):
        verdict = Verdict.SUSPICIOUS
    elif c.available:
        verdict = Verdict.INCONSPICUOUS
    else:
        verdict = Verdict.UNKNOWN
    return UsedCarCheck(verdict=verdict, recently_cleared=bool(recent), findings=tuple(findings))
