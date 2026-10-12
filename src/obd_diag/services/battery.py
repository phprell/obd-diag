"""Batterie- und Anlasstest: Ruhespannung, Einbruch beim Anlassen, Ladespannung; nur lesend.

Ablauf (``run_battery_test``), der Nutzer startet den Motor selbst:

1. Vorbereitung: ``initialize``, ``protocol``; Drehzahl (``010C``) muss 0 sein (Motor aus,
   Zündung an), Kühlmitteltemperatur (``0105``) für die Grenze beim Anlassen.
2. Ruhe (``REST_SECONDS``): Bordspannung laut Adapter (``ATRV``) in schneller Folge, am
   Anfang und Ende die Steuergerätespannung (PID 42).
3. Anlassen: Aufforderung, den Motor zu starten; bis ``START_TIMEOUT`` wird ``ATRV`` so
   schnell gelesen, wie der Adapter antwortet (höchstens alle ``SAMPLE_INTERVAL``), und
   außerhalb des Einbruchs alle ``RPM_INTERVAL`` die Drehzahl. Ein Einbruch um mehr als
   ``CRANK_DROP`` unter die Ruhespannung gilt als Anlassen, ab ``RUNNING_RPM`` läuft der
   Motor.
4. Laden: nach ``SETTLE_SECONDS`` Einschwingen ``CHARGE_SECONDS`` lang ``ATRV`` und PID 42.

Warum ``ATRV`` für den Einbruch: Adapter-Befehle erreichen das Fahrzeug nicht, für sie gilt
die Mindestpause von 50 ms zwischen Anfragen ans Fahrzeug nicht (``Elm327.command``). Am
FORScan-Adapter (ELM327-Nachbau) dauerte ``ATRV`` etwa 15 ms, PID 42 mit Pause über
100 ms; ein Anlassvorgang dauert oft unter einer Sekunde. Der Adapter misst aber an Pin 16
hinter einer Schutzdiode: Am Mercedes A 180 d (W177) zeigte er in Ruhe und bei laufendem
Motor stets etwa 0,8 V weniger als das Motorsteuergerät. Die Differenz in Ruhe
(``offset``) wird deshalb auf alle ``ATRV``-Werte aufgeschlagen, sofern das Steuergerät
PID 42 liefert. Ruhe- und Ladespannung kommen bevorzugt direkt aus PID 42.

Bricht die Spannung so tief ein, dass der Adapter neu startet (``LV RESET`` oder seine
Kennung statt einer Spannung), wird er neu initialisiert und das im Ergebnis vermerkt.

Gesendet werden nur ``initialize``/``protocol`` (AT-Befehle, ``0100``), ``ATRV``,
``010C``, ``0105`` und ``0142``; alles lesend.

Schwellen (Bewertung in ``rest_verdict``, ``crank_verdict``, ``charge_verdict``):

- Ruhe: Eine 12-V-Bleibatterie hat in Ruhe etwa 12,6 V voll, 12,4 V zu drei Vierteln,
  12,2 V halb geladen, unter 12,0 V ist sie weitgehend leer (übliche Ladezustandstabelle,
  z. B. Battery University BU-903). Mit Zündung an ziehen Steuergeräte Strom, die Spannung
  liegt etwas tiefer; daher gut ab ``REST_GOOD`` (12,2 V), darunter bis ``LOW_VOLTAGE``
  (11,8 V, wie die Warnung der Diagnose) schwach, darunter entladen. Nach einer Fahrt
  liegt die Spannung durch Oberflächenladung eine Weile höher.
- Anlassen: Beim klassischen Belastungstest (halber Kälteprüfstrom, 15 s) darf die Spannung
  bei 21 °C nicht unter 9,6 V fallen, bei Kälte etwas tiefer (Tabelle ``CRANK_LIMITS``, wie
  in Werkstatthandbüchern nach BCI). Das gilt hier sinngemäß für den Tiefstwert beim
  Anlassen, ohne die ersten ``INRUSH_SKIP`` Sekunden (Einschaltstrom des Anlassers). Die
  Kühlmitteltemperatur steht für die Batterietemperatur; bei warmem Motor ist sie höher
  und die Grenze damit die strengste. Bis ``CRANK_MARGIN`` darüber: knapp.
- Laden im Leerlauf: ``CHARGE_GOOD`` bis ``CHARGE_MAX`` gut; darüber lädt der Regler zu
  hoch. Zwischen ``CHARGE_MIN`` und ``CHARGE_GOOD`` niedrig: bei Fahrzeugen mit
  Batteriemanagement (Start-Stopp, geregelte Lichtmaschine) oft gewollt. Unter
  ``CHARGE_MIN`` (unter der Ruhespannung einer vollen Batterie) lädt die Lichtmaschine nicht.

Die Schwellen sind Richtwerte für 12-V-Bleibatterien (auch AGM/EFB), keine Herstellerangabe.
"""

import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from obd_diag.i18n import N_, decimal, tr
from obd_diag.protocol.elm327 import Elm327, ElmError
from obd_diag.protocol.obd import read_rpms
from obd_diag.protocol.pids import PIDS, read_values
from obd_diag.services.diagnostics import LOW_VOLTAGE

# --- Ablauf --------------------------------------------------------------------------
REST_SECONDS = 3.0  # Ruhespannung so lange messen
START_TIMEOUT = 30.0  # so lange auf das Anlassen warten
SAMPLE_INTERVAL = 0.02  # höchstens 50 ATRV-Messungen je Sekunde
RPM_INTERVAL = 0.5  # Drehzahl beim Warten auf den Start so oft lesen
SETTLE_SECONDS = 5.0  # nach dem Start warten, bis die Ladespannung steht
CHARGE_SECONDS = 5.0  # Ladespannung so lange messen
CHARGE_INTERVAL = 0.5  # Abstand der Messungen im Leerlauf
PROGRESS_INTERVAL = 0.2  # Zwischenstände höchstens so oft melden (außer Phasenwechsel)

# --- Erkennung -----------------------------------------------------------------------
CRANK_DROP = 1.0  # V unter der Ruhespannung: der Anlasser läuft
RUNNING_RPM = 500  # 1/min; Anlasser dreht etwa 150 bis 300, Leerlauf ab etwa 600
INRUSH_SKIP = 0.1  # s; Einschaltstrom des Anlassers nicht bewerten
MAX_OFFSET = 1.5  # V; größere Abweichung ATRV/PID 42 nicht als Diodenverlust deuten
ENGINE_RUNNING_VOLTAGE = 13.3  # V in Ruhe: so hoch nur bei laufendem Motor (Ladung)
ECU_VOLTAGE_RANGE = (5.0, 30.0)  # PID 42 nur in diesem Bereich glauben (wie die Diagnose)

# --- Bewertung -----------------------------------------------------------------------
REST_GOOD = 12.2
REST_LOW = LOW_VOLTAGE
# Mindestspannung beim Belastungstest je Temperatur (°C ab, Volt), aus der in
# Werkstatthandbüchern verbreiteten Tabelle (70/60/50/40/30/20/10/0 °F).
CRANK_LIMITS = (
    (21.0, 9.6),
    (16.0, 9.5),
    (10.0, 9.4),
    (4.0, 9.3),
    (-1.0, 9.1),
    (-7.0, 8.9),
    (-12.0, 8.7),
    (-18.0, 8.5),
)
CRANK_MARGIN = 0.4  # V über der Grenze: darunter „knapp“
CHARGE_MIN = 12.8
CHARGE_GOOD = 13.5
CHARGE_MAX = 15.0


class Phase(StrEnum):
    PREPARE = "prepare"  # Adapter, Protokoll, Motor aus?
    REST = "rest"  # Ruhespannung
    START = "start"  # Aufforderung: Motor starten
    CRANKING = "cranking"  # Spannung eingebrochen, Anlasser läuft
    RUNNING = "running"  # Motor läuft, Ladespannung


class Rating(StrEnum):
    GOOD = "good"
    WEAK = "weak"
    BAD = "bad"
    UNKNOWN = "unknown"


class BatteryTestRefused(Exception):
    """Der Test kann nicht laufen (Motor läuft, keine Spannung lesbar); nichts gemessen."""


class BatteryTestCancelled(Exception):
    """Vom Nutzer abgebrochen (``should_stop``)."""


@dataclass(frozen=True)
class BatteryProgress:
    phase: Phase
    elapsed: float  # Sekunden seit Testbeginn
    voltage: float | None  # letzte Bordspannung (ATRV, korrigiert)
    rpm: float | None  # letzte Drehzahl
    remaining: float | None  # Sekunden bis zum Ende der Phase, None ohne festes Ende


@dataclass(frozen=True)
class BatteryTestResult:
    adapter: str  # Kennung aus ATZ
    rest_adapter: float | None  # Median ATRV in Ruhe
    rest_ecu: float | None  # Median PID 42 in Ruhe
    offset: float  # auf ATRV aufgeschlagen (PID 42 minus ATRV in Ruhe), 0 ohne Bezug
    coolant_temp: float | None  # °C vor dem Start
    crank_detected: bool
    crank_minimum: float | None  # Tiefstwert beim Anlassen ohne Einschaltspitze (korrigiert)
    crank_lowest: float | None  # absoluter Tiefstwert (korrigiert)
    crank_seconds: float | None  # Dauer des Einbruchs
    started: bool  # Motor lief danach (Drehzahl ab RUNNING_RPM)
    charge_adapter: float | None  # Median ATRV im Leerlauf (korrigiert)
    charge_ecu: float | None  # Median PID 42 im Leerlauf
    adapter_reset: bool  # Adapter startete beim Anlassen neu
    curve: tuple[tuple[float, float], ...]  # (Sekunden, Volt korrigiert) aller ATRV-Werte

    @property
    def rest_voltage(self) -> float | None:
        """Ruhespannung: Steuergerät, sonst Adapter."""
        return self.rest_ecu if self.rest_ecu is not None else self.rest_adapter

    @property
    def charge_voltage(self) -> float | None:
        """Ladespannung: Steuergerät, sonst Adapter (korrigiert)."""
        return self.charge_ecu if self.charge_ecu is not None else self.charge_adapter


def crank_limit(coolant_temp: float | None) -> float:
    """Mindestspannung beim Anlassen für die Temperatur; ohne Angabe die strengste."""
    if coolant_temp is None:
        return CRANK_LIMITS[0][1]
    for since, limit in CRANK_LIMITS:
        if coolant_temp >= since:
            return limit
    return CRANK_LIMITS[-1][1]


# --- Messen --------------------------------------------------------------------------


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 3) if values else None


def _read_atrv(elm: Elm327) -> tuple[float | None, bool]:
    """Spannung laut Adapter und ob er dabei neu gestartet ist (``LV RESET``/Kennung)."""
    try:
        reply = elm.command("ATRV")
    except ElmError as e:
        return None, "LV RESET" in str(e)
    try:
        return float(reply.strip().rstrip("Vv")), False
    except ValueError:
        return None, "ELM327" in reply.upper() or "LV RESET" in reply.upper()


def _read_ecu_voltage(elm: Elm327) -> float | None:
    try:
        value = read_values(elm, [PIDS["control_voltage"]])["control_voltage"]
    except (ElmError, ValueError):
        return None
    low, high = ECU_VOLTAGE_RANGE
    return value if value is not None and low <= value <= high else None


def _read_rpm(elm: Elm327) -> float | None:
    """Höchste gültige Drehzahl aller antwortenden Steuergeräte; None, wenn keine."""
    try:
        rpms = [r for r in read_rpms(elm) if r is not None]
    except (ElmError, ValueError):
        return None
    return max(rpms) if rpms else None


def _read_coolant(elm: Elm327) -> float | None:
    try:
        return read_values(elm, [PIDS["coolant_temp"]])["coolant_temp"]
    except (ElmError, ValueError):
        return None


class _Run:
    """Zustand eines Testlaufs: Uhr, Abbruch, Zwischenstände, Spannungskurve."""

    def __init__(
        self,
        elm: Elm327,
        on_progress: Callable[[BatteryProgress], None],
        should_stop: Callable[[], bool],
        clock: Callable[[], float],
        sleep: Callable[[float], None],
    ) -> None:
        self.elm = elm
        self.on_progress = on_progress
        self.should_stop = should_stop
        self.clock = clock
        self.sleep = sleep
        self.start = clock()
        self.phase = Phase.PREPARE
        self.voltage: float | None = None
        self.rpm: float | None = None
        self.offset = 0.0
        self.curve: list[tuple[float, float]] = []
        self.reset = False
        self._reported: float | None = None

    def elapsed(self) -> float:
        return self.clock() - self.start

    def check_stop(self) -> None:
        if self.should_stop():
            raise BatteryTestCancelled(tr("Batterietest abgebrochen"))

    def report(self, phase: Phase, deadline: float | None = None, *, force: bool = False) -> None:
        now = self.clock()
        changed = phase is not self.phase
        self.phase = phase
        if not (changed or force) and (
            self._reported is not None and now - self._reported < PROGRESS_INTERVAL
        ):
            return
        self._reported = now
        remaining = None if deadline is None else max(0.0, deadline - now)
        self.on_progress(
            BatteryProgress(phase, now - self.start, self.voltage, self.rpm, remaining)
        )

    def atrv(self) -> float | None:
        """Eine ``ATRV``-Messung (roh); bei einem Neustart des Adapters neu initialisieren."""
        raw, reset = _read_atrv(self.elm)
        if reset:
            self.reset = True
            self.elm.initialize()
            self.elm.protocol()
        if raw is not None:
            self.voltage = round(raw + self.offset, 3)
            self.curve.append((round(self.elapsed(), 3), self.voltage))
        return raw

    def wait_until(self, moment: float) -> None:
        while (remaining := moment - self.clock()) > 0:
            self.check_stop()
            self.sleep(min(remaining, 0.1))


def run_battery_test(
    elm: Elm327,
    *,
    on_progress: Callable[[BatteryProgress], None] | None = None,
    should_stop: Callable[[], bool] = lambda: False,
    start_timeout: float = START_TIMEOUT,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> BatteryTestResult:
    """Führt den Test durch (siehe Modul); der Nutzer startet den Motor auf ``Phase.START``.

    ``on_progress`` meldet Phasenwechsel sofort, sonst höchstens alle
    ``PROGRESS_INTERVAL``. ``BatteryTestRefused``: Motor läuft schon oder der Adapter
    liefert keine Spannung. ``BatteryTestCancelled``: ``should_stop`` wurde True.
    ``TransportError`` (Adapter weg) geht durch, ebenso Fehler beim Initialisieren.
    Springt der Motor nicht an, endet der Test nach ``start_timeout`` ohne Ladespannung.
    """
    run = _Run(elm, on_progress or (lambda _p: None), should_stop, clock, sleep)
    run.report(Phase.PREPARE, force=True)
    adapter = elm.initialize()
    elm.protocol()
    run.rpm = _read_rpm(elm)
    if run.rpm is not None and run.rpm > 0:
        raise BatteryTestRefused(
            tr(
                "Der Motor läuft ({rpm:.0f} 1/min). Motor abstellen, Zündung an lassen und "
                "den Test erneut starten."
            ).format(rpm=run.rpm)
        )
    coolant = _read_coolant(elm)
    run.check_stop()

    # Ruhe
    rest_end = run.clock() + REST_SECONDS
    run.report(Phase.REST, rest_end)
    ecu = [_read_ecu_voltage(elm)]
    rest: list[float] = []
    while run.clock() < rest_end:
        run.check_stop()
        sample_at = run.clock()
        raw = run.atrv()
        if raw is not None:
            rest.append(raw)
        run.report(Phase.REST, rest_end)
        run.wait_until(min(sample_at + SAMPLE_INTERVAL, rest_end))
    ecu.append(_read_ecu_voltage(elm))
    rest_adapter, rest_ecu = _median(rest), _median([v for v in ecu if v is not None])
    if rest_adapter is None:
        raise BatteryTestRefused(
            tr("Der Adapter liefert keine Bordspannung (ATRV); ohne sie geht der Test nicht.")
        )
    if rest_ecu is not None and abs(rest_ecu - rest_adapter) <= MAX_OFFSET:
        run.offset = round(rest_ecu - rest_adapter, 3)
        run.curve = [(t, round(v + run.offset, 3)) for t, v in run.curve]
        run.voltage = None if run.voltage is None else round(run.voltage + run.offset, 3)
    rest_best = rest_ecu if rest_ecu is not None else rest_adapter
    if run.rpm is None and rest_best >= ENGINE_RUNNING_VOLTAGE:
        raise BatteryTestRefused(
            tr(
                "Die Bordspannung ({voltage} V) ist so hoch wie bei laufendem Motor. Motor "
                "abstellen, Zündung an lassen und den Test erneut starten."
            ).format(voltage=decimal(rest_best))
        )

    # Anlassen
    threshold = rest_adapter - CRANK_DROP
    start_end = run.clock() + start_timeout
    run.report(Phase.START, start_end, force=True)
    crank: list[tuple[float, float]] = []  # (Sekunden, Volt roh) unter der Schwelle
    crank_start: float | None = None
    next_rpm = run.clock()
    started = False
    while run.clock() < start_end:
        run.check_stop()
        sample_at = run.clock()
        raw = run.atrv()
        dipped = raw is not None and raw < threshold
        if dipped:
            assert raw is not None
            if crank_start is None:
                crank_start = run.elapsed()
            crank.append((run.elapsed(), raw))
        elif run.clock() >= next_rpm:
            # Im Einbruch keine Drehzahl: jede Anfrage ans Fahrzeug kostet über 100 ms
            run.rpm = _read_rpm(elm)
            next_rpm = run.clock() + RPM_INTERVAL
            if run.rpm is not None and run.rpm >= RUNNING_RPM:
                started = True
                break
        run.report(Phase.START if crank_start is None else Phase.CRANKING, start_end)
        run.wait_until(sample_at + SAMPLE_INTERVAL)

    crank_minimum = crank_lowest = crank_seconds = None
    if crank:
        assert crank_start is not None
        lowest = min(v for _t, v in crank)
        settled = [v for t, v in crank if t >= crank_start + INRUSH_SKIP] or [lowest]
        crank_lowest = round(lowest + run.offset, 3)
        crank_minimum = round(min(settled) + run.offset, 3)
        crank_seconds = round(crank[-1][0] - crank_start, 3)

    # Laden
    charge_adapter = charge_ecu = None
    if started:
        settle_end = run.clock() + SETTLE_SECONDS
        charge_end = settle_end + CHARGE_SECONDS
        charging: list[float] = []
        ecu_charging: list[float] = []
        while run.clock() < charge_end:
            run.check_stop()
            sample_at = run.clock()
            raw = run.atrv()
            if sample_at >= settle_end:
                if raw is not None:
                    charging.append(raw + run.offset)
                value = _read_ecu_voltage(elm)
                if value is not None:
                    ecu_charging.append(value)
            run.report(Phase.RUNNING, charge_end)
            run.wait_until(sample_at + CHARGE_INTERVAL)
        charge_adapter, charge_ecu = _median(charging), _median(ecu_charging)

    return BatteryTestResult(
        adapter=adapter,
        rest_adapter=rest_adapter,
        rest_ecu=rest_ecu,
        offset=run.offset,
        coolant_temp=coolant,
        crank_detected=bool(crank),
        crank_minimum=crank_minimum,
        crank_lowest=crank_lowest,
        crank_seconds=crank_seconds,
        started=started,
        charge_adapter=charge_adapter,
        charge_ecu=charge_ecu,
        adapter_reset=run.reset,
        curve=tuple(run.curve),
    )


# --- Bewerten ------------------------------------------------------------------------


@dataclass(frozen=True)
class Verdict:
    rating: Rating
    text: str  # in der eingestellten Sprache


def _volts(value: float) -> str:
    return f"{decimal(value)} V"


def rest_verdict(result: BatteryTestResult) -> Verdict:
    voltage = result.rest_voltage
    if voltage is None:
        return Verdict(Rating.UNKNOWN, tr("nicht gemessen"))
    if voltage >= REST_GOOD:
        return Verdict(Rating.GOOD, tr("{voltage}: gut geladen").format(voltage=_volts(voltage)))
    if voltage >= REST_LOW:
        return Verdict(
            Rating.WEAK,
            tr("{voltage}: nur teilweise geladen, Batterie laden").format(voltage=_volts(voltage)),
        )
    return Verdict(
        Rating.BAD,
        tr("{voltage}: entladen, Batterie laden und den Test wiederholen").format(
            voltage=_volts(voltage)
        ),
    )


def crank_verdict(result: BatteryTestResult) -> Verdict:
    voltage = result.crank_minimum
    if voltage is None:
        if result.adapter_reset:
            return Verdict(
                Rating.WEAK,
                tr(
                    "nicht messbar: der Adapter startete beim Anlassen neu, die Spannung "
                    "brach vermutlich sehr tief ein"
                ),
            )
        if result.started:
            return Verdict(Rating.UNKNOWN, tr("kein Spannungseinbruch erkannt"))
        return Verdict(Rating.UNKNOWN, tr("kein Anlassen erkannt"))
    limit = crank_limit(result.coolant_temp)
    values = {"voltage": _volts(voltage), "limit": _volts(limit)}
    if voltage >= limit + CRANK_MARGIN:
        return Verdict(Rating.GOOD, tr("{voltage} (Grenze {limit}): gut").format(**values))
    if voltage >= limit:
        return Verdict(
            Rating.WEAK, tr("{voltage} (Grenze {limit}): knapp, Batterie altert").format(**values)
        )
    return Verdict(
        Rating.BAD,
        tr(
            "{voltage} (Grenze {limit}): zu tief, Batterie schwach oder entladen "
            "(auch Anlasser und Kabel kommen in Frage)"
        ).format(**values),
    )


def charge_verdict(result: BatteryTestResult) -> Verdict:
    voltage = result.charge_voltage
    if voltage is None:
        if not result.started:
            return Verdict(Rating.UNKNOWN, tr("nicht gemessen: Motor ist nicht angesprungen"))
        return Verdict(Rating.UNKNOWN, tr("nicht lesbar"))
    text = _volts(voltage)
    if voltage > CHARGE_MAX:
        return Verdict(
            Rating.BAD, tr("{voltage}: zu hoch, Laderegler prüfen lassen").format(voltage=text)
        )
    if voltage >= CHARGE_GOOD:
        return Verdict(Rating.GOOD, tr("{voltage}: Lichtmaschine lädt").format(voltage=text))
    if voltage >= CHARGE_MIN:
        return Verdict(
            Rating.WEAK,
            tr(
                "{voltage}: niedrig; bei Fahrzeugen mit Batteriemanagement (Start-Stopp) oft "
                "gewollt, sonst Lichtmaschine prüfen"
            ).format(voltage=text),
        )
    return Verdict(Rating.BAD, tr("{voltage}: Lichtmaschine lädt nicht").format(voltage=text))


_ORDER = (Rating.BAD, Rating.WEAK, Rating.GOOD)


def overall_rating(result: BatteryTestResult) -> Rating:
    """Schlechteste der drei Bewertungen; UNKNOWN nur, wenn keine bewertet ist."""
    ratings = {
        v.rating for v in (rest_verdict(result), crank_verdict(result), charge_verdict(result))
    }
    return next((r for r in _ORDER if r in ratings), Rating.UNKNOWN)


_OVERALL_TEXTS = {
    Rating.GOOD: N_("Batterie und Ladung in Ordnung"),
    Rating.WEAK: N_("Auffällig: Einzelheiten beachten"),
    Rating.BAD: N_("Mangelhaft: Batterie bzw. Ladung prüfen lassen"),
    Rating.UNKNOWN: N_("Keine Bewertung möglich"),
}


def overall_text(result: BatteryTestResult) -> str:
    return tr(_OVERALL_TEXTS[overall_rating(result)])


def notes(result: BatteryTestResult) -> list[str]:
    """Hinweise zur Messung (in der eingestellten Sprache)."""
    out = []
    if result.offset:
        out.append(
            tr(
                "Adapter-Messung um {offset} V korrigiert (Abweichung zum Steuergerät in Ruhe)."
            ).format(offset=decimal(result.offset))
        )
    elif result.rest_ecu is None:
        out.append(
            tr(
                "Das Steuergerät meldet keine Spannung (PID 42): Werte nur vom Adapter, "
                "oft einige Zehntel Volt zu niedrig."
            )
        )
    if result.crank_detected and not result.started:
        out.append(tr("Der Motor ist nicht angesprungen (oder die Drehzahl war nicht lesbar)."))
    if result.adapter_reset:
        out.append(tr("Der Adapter startete beim Anlassen neu und wurde neu eingerichtet."))
    out.append(tr(BATTERY_NOTE))
    return out


BATTERY_NOTE = N_(
    "Richtwerte für 12-V-Bleibatterien. Kurz nach einer Fahrt zeigt die Ruhespannung zu "
    "viel; am aussagekräftigsten ist der Test nach einigen Stunden Standzeit."
)

# Anzeige je Phase (CLI und Oberfläche)
PHASE_TEXTS = {
    Phase.PREPARE: N_("Verbinde und prüfe, ob der Motor steht …"),
    Phase.REST: N_("Messe die Ruhespannung, Motor noch nicht starten …"),
    Phase.START: N_("Jetzt den Motor starten."),
    Phase.CRANKING: N_("Anlassen erkannt …"),
    Phase.RUNNING: N_("Motor läuft, messe die Ladespannung. Motor laufen lassen …"),
}

# Was der Nutzer vor dem Start wissen muss (CLI und Oberfläche)
BATTERY_INSTRUCTIONS = N_(
    "Fahrzeug steht, Gang in P bzw. Leerlauf, Feststellbremse an. Zündung an, Motor aus, "
    "Verbraucher (Licht, Gebläse) aus. Den Motor erst starten, wenn obd-diag dazu "
    "auffordert, und laufen lassen, bis der Test fertig ist (etwa 15 s)."
)


def result_to_dict(result: BatteryTestResult) -> dict[str, Any]:
    """Für ``obd-diag battery --json``; Schlüssel englisch wie bei den anderen Befehlen."""
    return {
        "adapter": result.adapter,
        "rest_voltage": result.rest_voltage,
        "rest_adapter": result.rest_adapter,
        "rest_ecu": result.rest_ecu,
        "offset": result.offset,
        "coolant_temp": result.coolant_temp,
        "crank_detected": result.crank_detected,
        "crank_minimum": result.crank_minimum,
        "crank_lowest": result.crank_lowest,
        "crank_seconds": result.crank_seconds,
        "crank_limit": crank_limit(result.coolant_temp),
        "started": result.started,
        "charge_voltage": result.charge_voltage,
        "charge_adapter": result.charge_adapter,
        "charge_ecu": result.charge_ecu,
        "adapter_reset": result.adapter_reset,
        "ratings": {
            "rest": rest_verdict(result).rating.value,
            "crank": crank_verdict(result).rating.value,
            "charge": charge_verdict(result).rating.value,
            "overall": overall_rating(result).value,
        },
        "curve": [list(point) for point in result.curve],
    }
