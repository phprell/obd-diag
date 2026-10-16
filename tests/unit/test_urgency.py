"""Dringlichkeit je Fehlercode (``services.urgency``): Regeln, Zusammenfassen, Ausgabe."""

import pytest

from obd_diag.i18n import set_language, tr
from obd_diag.services.diagnostics import DiagnosticCode, DtcKind
from obd_diag.services.urgency import (
    URGENCY_LABELS,
    Urgency,
    UrgencyRating,
    catalog_mil_text,
    highest,
    rate_code,
    rate_codes,
)

STORED, PENDING, PERMANENT = DtcKind.STORED, DtcKind.PENDING, DtcKind.PERMANENT


@pytest.mark.parametrize(
    ("code", "kinds", "mil_on", "level"),
    [
        # Übertemperatur und Öldruck: immer anhalten, auch ausstehend und ohne MIL
        ("P0217", {STORED}, False, Urgency.STOP),
        ("P0218", {PENDING}, None, Urgency.STOP),
        ("P0524", {PERMANENT}, True, Urgency.STOP),
        # Aussetzer: bestätigt bald in die Werkstatt, ausstehend beobachten
        ("P0300", {STORED}, False, Urgency.SOON),
        ("P0312", {PERMANENT}, None, Urgency.SOON),
        ("P0306", {PENDING}, True, Urgency.WATCH),
        ("P0313", {STORED}, False, Urgency.CHECK),  # nicht mehr im Aussetzer-Bereich
        # Airbag (B00xx)
        ("B0001", {STORED}, False, Urgency.SOON),
        ("B0099", {PENDING}, False, Urgency.WATCH),
        ("B0100", {STORED}, False, Urgency.CHECK),
        # Allgemeine Regeln
        ("P0420", {PERMANENT}, False, Urgency.SOON),
        ("P0420", {STORED}, True, Urgency.SOON),
        ("P0420", {STORED}, False, Urgency.CHECK),
        ("P0420", {STORED}, None, Urgency.CHECK),
        ("U1218", {STORED}, False, Urgency.CHECK),
        ("P0133", {PENDING}, True, Urgency.WATCH),
        ("P0133", {PENDING, STORED}, False, Urgency.CHECK),
    ],
)
def test_rules(code: str, kinds: set[DtcKind], mil_on: bool | None, level: Urgency) -> None:
    assert rate_code(code, kinds, mil_on).level is level


def test_reason_names_mil_state() -> None:
    assert rate_code("P0420", {STORED}, False).reason == "Gespeichert, die Kontrollleuchte ist aus."
    assert "nicht gelesen" in rate_code("P0420", {STORED}, None).reason
    assert "Katalysator" in rate_code("P0300", {STORED}, False).reason


def test_code_without_kind_is_an_error() -> None:
    with pytest.raises(ValueError):
        rate_code("P0420", set(), None)


def test_rate_codes_combines_kinds_per_code() -> None:
    codes = [
        DiagnosticCode("P0420", STORED),
        DiagnosticCode("P0133", PENDING),
        DiagnosticCode("P0420", PERMANENT),
    ]
    ratings = rate_codes(codes, False)
    assert list(ratings) == ["P0420", "P0133"]
    assert ratings["P0420"].level is Urgency.SOON  # wegen permanent
    assert ratings["P0133"].level is Urgency.WATCH


def test_highest() -> None:
    assert highest([]) is None
    watch = UrgencyRating(Urgency.WATCH, "a")
    soon = UrgencyRating(Urgency.SOON, "b")
    other_soon = UrgencyRating(Urgency.SOON, "c")
    assert highest([watch, soon, other_soon]) is soon
    assert highest([watch]) is watch


def test_levels_have_labels_and_keys() -> None:
    assert set(URGENCY_LABELS) == set(Urgency)
    assert [u.key for u in sorted(Urgency)] == ["watch", "check", "soon", "stop"]
    set_language("en")
    assert tr(URGENCY_LABELS[Urgency.STOP]) == "Avoid driving"


@pytest.mark.parametrize(
    ("mil", "kind", "text"),
    [
        (True, STORED, "Schaltet die Motorkontrollleuchte ein"),
        (True, PERMANENT, "Schaltet die Motorkontrollleuchte ein"),
        (True, PENDING, "Kontrollleuchte erst, wenn bestätigt"),
        (False, PENDING, "Keine Motorkontrollleuchte"),
        (None, STORED, None),
    ],
)
def test_catalog_mil_text_depends_on_the_kind(
    mil: bool | None, kind: DtcKind, text: str | None
) -> None:
    """P24C7 am W177: ausstehend, Katalog „MIL: ja“, Leuchte aus. Die Anzeige darf nicht
    „Kontrollleuchte an“ sagen."""
    assert catalog_mil_text(mil, kind) == text
