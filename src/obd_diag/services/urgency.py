"""Dringlichkeit je Fehlercode: grobe Einordnung in vier Stufen, keine Ferndiagnose.

Grundlage sind nur Angaben, die das Fahrzeug genormt liefert, und die Codenummer:

- Art des Codes: gespeichert (Mode 03, bestätigt), ausstehend (Mode 07, einmal
  erkannt) oder permanent (Mode 0A: das Steuergerät hat die Kontrollleuchte
  eingeschaltet; der Code verschwindet erst, wenn die Eigendiagnose den Fehler als
  behoben erkennt, nicht durch Löschen).
- Kontrollleuchte (MIL) laut Mode 01 PID 01, Bit A7. Ob sie blinkt, liefert OBD nicht.
- Codegruppen nach SAE J2012, deren Bedeutung herstellerunabhängig feststeht:
  ``P0217`` (Kühlmittel-Übertemperatur), ``P0218`` (Getriebeöl-Übertemperatur),
  ``P0524`` (Motoröldruck zu niedrig), ``P0300``-``P0312`` (Verbrennungsaussetzer) und
  ``B0001``-``B0099`` (Rückhaltesysteme, Airbag).

Regeln, die erste zutreffende gilt (je Code über alle Arten, in denen er vorkommt):

1. Übertemperatur oder Öldruck (gespeichert, permanent oder ausstehend): ``STOP``
2. Verbrennungsaussetzer, gespeichert oder permanent: ``SOON``. Bei Aussetzern, die
   den Katalysator schädigen, blinkt die Kontrollleuchte (so verlangt es z. B. die
   kalifornische OBD-II-Vorschrift, 13 CCR 1968.2); das sieht nur, wer im Auto sitzt,
   daher steht es in der Begründung.
3. Airbag-Code, gespeichert oder permanent: ``SOON``
4. permanent: ``SOON``
5. gespeichert bei eingeschalteter Kontrollleuchte: ``SOON``
6. gespeichert: ``CHECK``
7. nur ausstehend: ``WATCH``

Bewusst nicht berücksichtigt: Katalogangaben „schaltet die Kontrollleuchte ein“
(gilt im Katalog für fast jeden P-Code und sagte daher nichts) und
herstellerspezifische Codes (P1xxx, U1xxx …), deren Bedeutung ohne Herstellerunterlagen
unbekannt ist; sie fallen unter die allgemeinen Regeln 4 bis 7.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import IntEnum

from obd_diag.i18n import N_
from obd_diag.services.diagnostics import DiagnosticCode, DtcKind


class Urgency(IntEnum):
    """Stufen von harmlos bis ernst; vergleichbar (``max`` ergibt die ernsteste)."""

    WATCH = 1  # beobachten
    CHECK = 2  # bei Gelegenheit prüfen lassen
    SOON = 3  # bald in die Werkstatt
    STOP = 4  # möglichst nicht weiterfahren

    @property
    def key(self) -> str:
        """Stabiler Name für JSON, CSV-Auswertung und QML, z. B. ``"soon"``."""
        return self.name.lower()


# Bezeichnungen deutsch, bei der Ausgabe mit ``tr`` übersetzen
URGENCY_LABELS = {
    Urgency.WATCH: N_("Beobachten"),
    Urgency.CHECK: N_("Bei Gelegenheit prüfen"),
    Urgency.SOON: N_("Bald in die Werkstatt"),
    Urgency.STOP: N_("Möglichst nicht weiterfahren"),
}

URGENCY_NOTE = N_(
    "Die Dringlichkeit ist eine grobe Einordnung aus Code, Art und Kontrollleuchte, "
    "keine Ferndiagnose. Im Zweifel und bei spürbaren Auffälligkeiten (Geräusche, "
    "Geruch, Leistungsverlust, Warnmeldungen im Kombiinstrument) eine Werkstatt fragen."
)

_CRITICAL = {
    "P0217": N_(
        "Der Motor meldet Übertemperatur. Weiterfahren kann den Motor schwer beschädigen; "
        "anhalten, Motor abstellen, Kühlmittel prüfen (erst nach dem Abkühlen öffnen)."
    ),
    "P0218": N_(
        "Das Getriebe meldet Übertemperatur. Weiterfahren kann das Getriebe beschädigen; "
        "anhalten und abkühlen lassen."
    ),
    "P0524": N_(
        "Der Motoröldruck ist zu niedrig. Weiterfahren kann den Motor schwer beschädigen; "
        "anhalten, Motor abstellen, Ölstand prüfen."
    ),
}
_MISFIRE = re.compile(r"P03(0\d|1[0-2])")
_RESTRAINT = re.compile(r"B00\d\d")

_MISFIRE_REASON = N_(
    "Verbrennungsaussetzer. Blinkt die Kontrollleuchte, kann der Katalysator Schaden "
    "nehmen: dann nur langsam und ohne Last weiterfahren, am besten gleich in die Werkstatt."
)
_RESTRAINT_REASON = N_("Rückhaltesystem (Airbag, Gurtstraffer): Es kann im Ernstfall ausfallen.")
_PERMANENT_REASON = N_(
    "Permanenter Code: Das Steuergerät hat die Kontrollleuchte eingeschaltet. Er "
    "verschwindet erst, wenn die Eigendiagnose den Fehler als behoben erkennt."
)
_MIL_REASON = N_("Gespeichert, und die Kontrollleuchte ist an.")
_STORED_MIL_OFF_REASON = N_("Gespeichert, die Kontrollleuchte ist aus.")
_STORED_REASON = N_("Gespeichert; ob die Kontrollleuchte an ist, wurde nicht gelesen.")
_PENDING_REASON = N_(
    "Ausstehend: einmal erkannt, aber noch nicht bestätigt. Nach einigen Fahrten erneut lesen."
)


@dataclass(frozen=True)
class UrgencyRating:
    level: Urgency
    reason: str  # deutsch, zur Ausgabe ``tr(reason)``

    @property
    def label(self) -> str:
        """Bezeichnung der Stufe (deutsch, zur Ausgabe ``tr``)."""
        return URGENCY_LABELS[self.level]


def rate_code(code: str, kinds: Iterable[DtcKind], mil_on: bool | None) -> UrgencyRating:
    """Dringlichkeit eines Codes, der in den Arten ``kinds`` gemeldet wurde.

    ``mil_on``: Kontrollleuchte laut PID 01, ``None`` = nicht gelesen. Regeln im
    Modul-Docstring. ``ValueError`` ohne Art.
    """
    kinds = set(kinds)
    if not kinds:
        raise ValueError("Code ohne Art")
    confirmed = bool(kinds & {DtcKind.STORED, DtcKind.PERMANENT})
    if code in _CRITICAL:
        return UrgencyRating(Urgency.STOP, _CRITICAL[code])
    if confirmed and _MISFIRE.fullmatch(code):
        return UrgencyRating(Urgency.SOON, _MISFIRE_REASON)
    if confirmed and _RESTRAINT.fullmatch(code):
        return UrgencyRating(Urgency.SOON, _RESTRAINT_REASON)
    if DtcKind.PERMANENT in kinds:
        return UrgencyRating(Urgency.SOON, _PERMANENT_REASON)
    if DtcKind.STORED in kinds:
        if mil_on:
            return UrgencyRating(Urgency.SOON, _MIL_REASON)
        if mil_on is None:
            return UrgencyRating(Urgency.CHECK, _STORED_REASON)
        return UrgencyRating(Urgency.CHECK, _STORED_MIL_OFF_REASON)
    return UrgencyRating(Urgency.WATCH, _PENDING_REASON)


def rate_codes(codes: Iterable[DiagnosticCode], mil_on: bool | None) -> dict[str, UrgencyRating]:
    """Dringlichkeit je Code (ein Code, der z. B. gespeichert und permanent ist, wird
    einmal über beide Arten bewertet). Reihenfolge wie das erste Auftreten."""
    kinds: dict[str, set[DtcKind]] = {}
    for c in codes:
        kinds.setdefault(c.code, set()).add(c.kind)
    return {code: rate_code(code, found, mil_on) for code, found in kinds.items()}


def highest(ratings: Iterable[UrgencyRating]) -> UrgencyRating | None:
    """Die ernsteste Einstufung (bei Gleichstand die erste), ``None`` ohne Codes."""
    result: UrgencyRating | None = None
    for rating in ratings:
        if result is None or rating.level > result.level:
            result = rating
    return result
