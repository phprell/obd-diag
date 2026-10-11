"""Adapter-Check: was der ELM327-Adapter wirklich kann.

Viele Adapter melden sich als „ELM327 v1.5“ oder „v2.1“, sind aber Nachbauten mit
unvollständigem Befehlssatz. Der Check fragt Kennung (``ATI``), Beschreibung (``AT@1``)
und Spannung (``ATRV``) ab und probiert dann lesende Adapter-Befehle, die laut
Versionsliste des Datenblatts (ELM327DSJ S. 90 bis 91) erst mit einer bestimmten
Firmware hinzukamen. Daraus folgt, welcher Version der Befehlssatz tatsächlich
entspricht, ob das zur gemeldeten Version passt und ob der Adapter voraussichtlich für
die Steuergerätesuche über UDS (v0.3) taugt.

Gesendet werden nur Adapter-Befehle aus der Freigabeliste; das Fahrzeug wird nicht
angesprochen, und kein Befehl verändert den Adapter dauerhaft. ``ATSH`` und ``ATCRA``,
die UDS tatsächlich braucht, sendet der Check bewusst nicht: Sie verstellen die
Adressierung und werden erst mit v0.3 und eigenem Sicherheitskonzept freigegeben. Das
UDS-Urteil ist deshalb ein Schluss aus dem übrigen Befehlssatz, keine Messung.
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from obd_diag.i18n import N_, decimal, tr
from obd_diag.protocol.elm327 import Elm327, ElmError
from obd_diag.services.diagnostics import LOW_VOLTAGE
from obd_diag.transport import TransportTimeout

# Ein Adapter-Befehl antwortet sofort; bleibt die Antwort aus, kennt ihn der Adapter
# vermutlich nicht (statt ``?``). Kürzer als ``DEFAULT_TIMEOUT``, damit der Check nicht
# je Befehl 10 s hängt.
PROBE_TIMEOUT = 3.0

# Firmware-Versionen laut Versionsliste im Datenblatt (ELM327DSJ S. 90 bis 91; „Elm
# Electronics never made a v1.4a“). Eine v1.5 gibt es dort nicht.
GENUINE_VERSIONS = ("1.0", "1.0a", "1.1", "1.2", "1.2a", "1.3", "1.3a", "1.4", "1.4b", "2.0", "2.1")
DATASHEET_VERSION = "2.1"

# Unter dieser Spannung steckt der Adapter vermutlich nicht im Auto (nur über USB
# versorgt): Pin 2 des ELM327 misst die Spannung an Pin 16 der OBD-Buchse.
NOT_IN_CAR_VOLTAGE = 5.0

# Mindestversion des Befehlssatzes, ab der die UDS-Suche voraussichtlich geht: ``ATSH``
# gibt es ab v1.0, Flusssteuerung (``ATFC``) ab v1.1, ``ATCRA`` (Empfangsfilter) ab
# v1.3 (Versionsliste S. 90). Belegt ist ein Befehlssatz ab v1.4 durch ``ATIGN`` und
# ``ATRD``; dann ist v1.3 sicher enthalten.
UDS_LIKELY_FROM = "1.4"
UDS_POSSIBLE_FROM = "1.1"

_VERSION = re.compile(r"\bv?(\d+)\.(\d+)([a-z]?)\b", re.IGNORECASE)
# ``AT PPS``: je Parameter „hh:vv N“ (an) oder „hh:vv F“ (aus), Datenblatt S. 68
_PARAMETER = re.compile(r"\b([0-9A-F]{2}):([0-9A-F]{2})\s*([NF])\b")


@dataclass(frozen=True)
class Probe:
    """Ein lesender Adapter-Befehl, der seit ``since`` im Befehlssatz steht."""

    command: str
    since: str  # Firmware-Version laut Datenblatt, z. B. "1.4"
    purpose: str  # deutsch, mit N_ markiert; zur Anzeige tr()


# Reihenfolge = Reihenfolge auf der Leitung. Alle lesen nur (Datenblatt-Seite):
PROBES = (
    Probe("ATI", "1.0", N_("Kennung")),  # S. 18
    Probe("AT@1", "1.0", N_("Gerätebeschreibung")),  # S. 28
    Probe("ATRV", "1.0", N_("Spannung")),  # S. 24
    Probe("ATPPS", "1.1", N_("Übersicht der Programmable Parameters")),  # S. 23
    Probe("ATIGN", "1.4", N_("Zündungseingang")),  # S. 19
    Probe("ATRD", "1.4", N_("Nutzerspeicher lesen")),  # S. 24
    Probe("ATAMC", "2.0", N_("Aktivitätszähler")),  # S. 12
)


class AdapterKind(Enum):
    MATCHES = "matches"  # Version laut Datenblatt, Befehlssatz passt dazu
    CLONE = "clone"  # Version gibt es nicht, oder Befehle fehlen, die sie haben müsste
    UNKNOWN = "unknown"  # keine Version erkennbar


class UdsSuitability(Enum):
    LIKELY = "likely"
    POSSIBLE = "possible"
    UNLIKELY = "unlikely"


@dataclass(frozen=True)
class ProbeResult:
    probe: Probe
    reply: str | None  # None: nicht unterstützt (``?``, Fehler oder keine Antwort)

    @property
    def supported(self) -> bool:
        return self.reply is not None


@dataclass(frozen=True)
class AdapterReport:
    reset_id: str  # letzte Zeile der ATZ-Antwort
    results: tuple[ProbeResult, ...]
    voltage: float | None

    def reply(self, command: str) -> str | None:
        return next((r.reply for r in self.results if r.probe.command == command), None)

    @property
    def identification(self) -> str:
        """Kennung laut ``ATI``, sonst laut ``ATZ``."""
        return self.reply("ATI") or self.reset_id

    @property
    def description(self) -> str | None:
        return self.reply("AT@1")

    @property
    def version(self) -> str | None:
        """Gemeldete Version ohne „v“, z. B. "1.5"; None, wenn keine erkennbar ist."""
        match = _VERSION.search(self.identification)
        if match is None:
            return None
        return f"{int(match[1])}.{int(match[2])}{match[3].lower()}"

    @property
    def version_known(self) -> bool:
        """Die Version steht in der Versionsliste des Datenblatts oder ist neuer."""
        version = self.version
        if version is None:
            return False
        return version in GENUINE_VERSIONS or _key(version) > _key(DATASHEET_VERSION)

    @property
    def command_set(self) -> str | None:
        """Höchste Version, deren geprüfte Befehle alle beantwortet werden.

        None, wenn schon Befehle aus v1.0 fehlen.
        """
        level = None
        for since in sorted({r.probe.since for r in self.results}, key=_key):
            if not all(r.supported for r in self.results if _key(r.probe.since) <= _key(since)):
                break
            level = since
        return level

    @property
    def missing(self) -> tuple[str, ...]:
        """Befehle, die die gemeldete Version laut Datenblatt hätte, die aber fehlen."""
        version = self.version
        if version is None:
            return ()
        claimed = min(_key(version), _key(DATASHEET_VERSION))
        return tuple(
            r.probe.command
            for r in self.results
            if not r.supported and _key(r.probe.since) <= claimed
        )

    @property
    def kind(self) -> AdapterKind:
        if self.version is None:
            return AdapterKind.UNKNOWN
        if not self.version_known or self.missing:
            return AdapterKind.CLONE
        return AdapterKind.MATCHES

    @property
    def uds(self) -> UdsSuitability:
        level = self.command_set
        if level is not None and _key(level) >= _key(UDS_LIKELY_FROM):
            return UdsSuitability.LIKELY
        if level is not None and _key(level) >= _key(UDS_POSSIBLE_FROM):
            return UdsSuitability.POSSIBLE
        return UdsSuitability.UNLIKELY

    @property
    def parameters(self) -> tuple[tuple[str, str], ...] | None:
        """Eingeschaltete Programmable Parameters (Nummer, Wert) laut ``ATPPS``.

        None, wenn der Adapter ``ATPPS`` nicht kennt oder die Antwort nicht lesbar ist.
        """
        reply = self.reply("ATPPS")
        if reply is None:
            return None
        found = _PARAMETER.findall(reply)
        if not found:
            return None
        return tuple((number, value) for number, value, state in found if state == "N")


def _key(version: str) -> tuple[int, int, str]:
    """Sortierschlüssel: "1.4b" → (1, 4, "b"); "1.4" vor "1.4b" vor "2.0"."""
    match = _VERSION.fullmatch(version)
    assert match is not None, version
    return int(match[1]), int(match[2]), match[3].lower()


def _probe(elm: Elm327, command: str) -> str | None:
    try:
        return elm.command(command).strip() or None
    except (ElmError, TransportTimeout):
        return None


def check_adapter(elm: Elm327) -> AdapterReport:
    """Setzt den Adapter zurück und prüft seinen Befehlssatz; nur Adapter-Befehle.

    ``TransportError`` (Verbindung weg) bricht ab; ein Befehl, den der Adapter ablehnt
    oder nicht beantwortet, zählt als nicht unterstützt.
    """
    reset_id = elm.initialize()
    saved = elm.timeout
    elm.timeout = min(saved, PROBE_TIMEOUT)
    try:
        results = tuple(ProbeResult(p, _probe(elm, p.command)) for p in PROBES)
    finally:
        elm.timeout = saved
    voltage = None
    reply = next(r.reply for r in results if r.probe.command == "ATRV")
    if reply is not None:
        try:
            voltage = float(reply.rstrip("Vv"))
        except ValueError:
            voltage = None
    return AdapterReport(reset_id, results, voltage)


# --- Texte für CLI und Oberfläche -------------------------------------------------


def version_text(report: AdapterReport) -> str:
    version = report.version
    if version is None:
        return tr("keine Versionsangabe erkennbar")
    if version in GENUINE_VERSIONS:
        return tr("v{version} (steht in der Versionsliste des Datenblatts)").format(version=version)
    if report.version_known:
        return tr("v{version} (neuer als das Datenblatt v{datasheet})").format(
            version=version, datasheet=DATASHEET_VERSION
        )
    return tr(
        "v{version}: steht nicht in der Versionsliste von Elm Electronics, "
        "der Adapter ist ein Nachbau"
    ).format(version=version)


def kind_text(report: AdapterReport) -> str:
    if report.kind is AdapterKind.UNKNOWN:
        return tr("unklar: der Adapter nennt keine Version")
    level = report.command_set
    level_text = (
        tr("Befehlssatz wie v{level}").format(level=level)
        if level is not None
        else tr("selbst Grundbefehle fehlen")
    )
    if report.kind is AdapterKind.MATCHES:
        return tr("passt zur gemeldeten Version ({level})").format(level=level_text)
    if report.missing:
        return tr("Nachbau, {level}; es fehlt {commands}").format(
            level=level_text, commands=", ".join(report.missing)
        )
    return tr("Nachbau, {level}").format(level=level_text)


def uds_text(report: AdapterReport) -> str:
    if report.uds is UdsSuitability.LIKELY:
        return tr(
            "voraussichtlich ja: Befehlssatz ab v1.4, die nötigen Befehle ATSH (ab v1.0) "
            "und ATCRA (ab v1.3) sind damit enthalten"
        )
    if report.uds is UdsSuitability.POSSIBLE:
        return tr(
            "unsicher: belegt ist nur ein Befehlssatz wie v{level}, ATCRA (ab v1.3) "
            "fehlt vielleicht"
        ).format(level=report.command_set)
    return tr("eher nicht: der Adapter beherrscht nur Grundbefehle")


UDS_NOTE = N_(
    "Geschlossen aus dem Befehlssatz: ATSH und ATCRA selbst sendet der Check nicht, weil "
    "sie die Adressierung verstellen. Sicher zeigt es erst die Steuergerätesuche (v0.3)."
)


def voltage_text(report: AdapterReport) -> str:
    voltage = report.voltage
    if voltage is None:
        return tr("nicht lesbar (ATRV)")
    text = f"{decimal(voltage)} V"
    if voltage < NOT_IN_CAR_VOLTAGE:
        return tr("{voltage}: Adapter steckt vermutlich nicht im Auto").format(voltage=text)
    if voltage < LOW_VOLTAGE:
        return tr("{voltage}: niedrig, Batterie laden").format(voltage=text)
    return text


def parameters_text(report: AdapterReport) -> str:
    parameters = report.parameters
    if parameters is None:
        return tr("nicht lesbar (ATPPS)")
    if not parameters:
        return tr("alle auf Werkseinstellung")
    changed = ", ".join(f"PP {number} = {value}" for number, value in parameters)
    return tr(
        "geändert: {changed}; obd-diag ändert sie nicht, sie können aber Antworten "
        "anders formatieren"
    ).format(changed=changed)


def probe_text(result: ProbeResult) -> str:
    """„ja“ bzw. „nein“ mit Version, z. B. für die Befehlsliste."""
    state = tr("ja") if result.supported else tr("nein")
    return tr("{state} (seit v{since}: {purpose})").format(
        state=state, since=result.probe.since, purpose=tr(result.probe.purpose)
    )


def report_to_dict(report: AdapterReport) -> dict[str, Any]:
    """Für ``obd-diag adapter --json``; Schlüssel englisch wie bei den anderen Befehlen."""
    parameters = report.parameters
    return {
        "identification": report.identification,
        "description": report.description,
        "version": report.version,
        "version_known": report.version_known,
        "voltage": report.voltage,
        "commands": {r.probe.command: r.reply for r in report.results},
        "command_set": report.command_set,
        "missing": list(report.missing),
        "kind": report.kind.value,
        "uds": report.uds.value,
        "parameters_on": None if parameters is None else dict(parameters),
    }
