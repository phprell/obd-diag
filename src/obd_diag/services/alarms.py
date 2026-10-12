"""Warnschwellen für Live-Daten: untere und obere Grenze je Wert, frei einstellbar.

Die Schwellen sind Hinweise für den Nutzer, keine Herstellergrenzen: Was für ein
Fahrzeug normal ist, steht in dessen Unterlagen. Die Vorgaben sind bewusst so gewählt,
dass sie im normalen Betrieb nicht anschlagen:

- Kühlmitteltemperatur über 115 °C: Moderne Motoren laufen unter Last bis etwa 105
  bis 110 °C (Kennfeldthermostat); darüber droht Überhitzung.
- Motoröltemperatur über 130 °C.
- Bordspannung (``ATRV``) und Steuergerätespannung (PID 42) unter 11,8 V wie
  ``LOW_VOLTAGE`` (Batterie schwach) und über 15,5 V (Ladespannung zu hoch;
  Lichtmaschinen mit Rekuperation laden kurzzeitig bis gut 15 V).

Gesendet wird dafür nichts: geprüft werden die Werte, die ``run_live`` ohnehin liest.
"""

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from obd_diag.i18n import N_, decimal, tr
from obd_diag.services.diagnostics import LOW_VOLTAGE

# Schlüssel der Bordspannung (``ATRV``); kein ``PidSpec``, steht in ``LiveSample.voltage``
VOLTAGE_KEY = "voltage"
VOLTAGE_LABEL = N_("Bordspannung")
HIGH_VOLTAGE = 15.5


@dataclass(frozen=True)
class Threshold:
    """Grenzen eines Werts; ``None`` = keine Grenze auf dieser Seite."""

    low: float | None = None
    high: float | None = None

    def __post_init__(self) -> None:
        for limit in (self.low, self.high):
            if limit is not None and not math.isfinite(limit):
                raise ValueError(tr("Schwelle muss eine Zahl sein"))
        if self.low is not None and self.high is not None and self.low >= self.high:
            raise ValueError(
                tr("untere Schwelle {low} muss kleiner sein als obere {high}").format(
                    low=decimal(self.low), high=decimal(self.high)
                )
            )

    @property
    def empty(self) -> bool:
        return self.low is None and self.high is None

    def side(self, value: float | None) -> str | None:
        """``"low"`` unter der unteren, ``"high"`` über der oberen Grenze, sonst ``None``
        (auch für nicht lesbare Werte). Genau auf der Grenze ist noch in Ordnung."""
        if value is None or math.isnan(value):
            return None
        if self.low is not None and value < self.low:
            return "low"
        if self.high is not None and value > self.high:
            return "high"
        return None


DEFAULT_THRESHOLDS: dict[str, Threshold] = {
    "coolant_temp": Threshold(high=115.0),
    "oil_temp": Threshold(high=130.0),
    "control_voltage": Threshold(low=LOW_VOLTAGE, high=HIGH_VOLTAGE),
    VOLTAGE_KEY: Threshold(low=LOW_VOLTAGE, high=HIGH_VOLTAGE),
}


@dataclass(frozen=True)
class Alarm:
    key: str  # PidSpec.key oder VOLTAGE_KEY
    value: float
    side: str  # "low" oder "high"
    limit: float  # die überschrittene Grenze

    def text(self, name: str, unit: str) -> str:
        """Z. B. „Kühlmitteltemperatur 117 °C über 115 °C“ in der eingestellten Sprache;
        ``name`` und ``unit`` schon übersetzt."""
        template = (
            tr("{name} {value} {unit} über {limit} {unit}")
            if self.side == "high"
            else tr("{name} {value} {unit} unter {limit} {unit}")
        )
        return (
            template.format(
                name=name, value=_number(self.value), limit=_number(self.limit), unit=unit
            )
            .replace("  ", " ")
            .strip()
        )


def _number(value: float) -> str:
    return str(int(value)) if value == int(value) else decimal(value)


def check(
    values: Mapping[str, float | None],
    voltage: float | None,
    thresholds: Mapping[str, Threshold],
) -> tuple[Alarm, ...]:
    """Alle überschrittenen Schwellen einer Runde, in der Reihenfolge von ``thresholds``.

    ``values`` sind die Werte je ``PidSpec.key``, ``voltage`` die Bordspannung
    (``VOLTAGE_KEY``). Schwellen für Werte, die nicht abgefragt werden, bleiben stumm.
    """
    alarms: list[Alarm] = []
    for key, threshold in thresholds.items():
        value = voltage if key == VOLTAGE_KEY else values.get(key)
        side = threshold.side(value)
        if side is None or value is None:
            continue
        limit = threshold.low if side == "low" else threshold.high
        assert limit is not None
        alarms.append(Alarm(key, value, side, limit))
    return tuple(alarms)


def parse_number(text: str) -> float | None:
    """Zahl mit Punkt oder Komma; leer ergibt ``None``, sonst ``ValueError``."""
    text = text.strip().replace(",", ".")
    if not text:
        return None
    value = float(text)
    if not math.isfinite(value):
        raise ValueError(text)
    return value


def parse_threshold(text: str) -> tuple[str, Threshold]:
    """``SCHLÜSSEL=UNTEN:OBEN`` aus der Kommandozeile, z. B. ``coolant_temp=:110``.

    Eine leere Seite heißt keine Grenze; ``SCHLÜSSEL=:`` schaltet die Warnung für den
    Wert ab. Ob es den Schlüssel gibt, prüft der Aufrufer. ``ValueError`` bei falscher Form.
    """
    key, sep, limits = text.partition("=")
    low_text, colon, high_text = limits.partition(":")
    key = key.strip().lower()
    if not sep or not colon or not key:
        raise ValueError(
            tr("Form SCHLÜSSEL=UNTEN:OBEN erwartet, z. B. coolant_temp=:110: {text}").format(
                text=repr(text)
            )
        )
    try:
        low, high = parse_number(low_text), parse_number(high_text)
    except ValueError:
        raise ValueError(tr("keine Zahl: {text}").format(text=repr(limits))) from None
    return key, Threshold(low, high)


def merge(
    base: Mapping[str, Threshold], changes: Iterable[tuple[str, Threshold]]
) -> dict[str, Threshold]:
    """``base`` mit ``changes`` (ersetzen je Schlüssel); leere Schwellen fallen weg."""
    merged = dict(base)
    for key, threshold in changes:
        merged[key] = threshold
    return {key: t for key, t in merged.items() if not t.empty}


def thresholds_to_json(thresholds: Mapping[str, Threshold]) -> dict[str, list[float | None]]:
    """Für die Einstellungen der Oberfläche: ``{"coolant_temp": [null, 115.0]}``."""
    return {key: [t.low, t.high] for key, t in thresholds.items()}


def thresholds_from_json(data: Any) -> dict[str, Threshold]:
    """Gegenstück zu ``thresholds_to_json``; ungültige Einträge werden übergangen."""
    result: dict[str, Threshold] = {}
    if not isinstance(data, dict):
        return result
    for key, limits in data.items():
        if not isinstance(key, str) or not isinstance(limits, list) or len(limits) != 2:
            continue
        if not all(v is None or isinstance(v, int | float) for v in limits):
            continue
        low, high = (None if v is None else float(v) for v in limits)
        try:
            threshold = Threshold(low, high)
        except ValueError:
            continue
        if not threshold.empty:
            result[key] = threshold
    return result
