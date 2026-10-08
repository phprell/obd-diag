"""Freigabeliste gegen die Quellen: ``tests/fixtures/command_spec.yaml`` nennt für jeden
Befehl, den obd-diag senden darf, Seite und Zitat aus dem ELM327-Datenblatt (ELM327DSJ)
bzw. den J1979-Service, und für verbotene Befehle, warum sie laut Datenblatt gefährlich
sind.

Geprüft wird hier, dass die Freigabeliste in ``protocol/elm327.py`` genau das erlaubt, was
die Spezifikation erlaubt, nicht mehr und nicht weniger. Dass jeder in einem Test
tatsächlich gesendete Befehl in der Spezifikation steht, prüft ``tests/conftest.py``
(``_commands_match_spec``) in allen Tests.
"""

import itertools

import pytest
from hypothesis import given
from hypothesis import strategies as st

from obd_diag.protocol.elm327 import (
    AT_COMMANDS,
    CLEAR_COMMAND,
    Elm327,
    ForbiddenCommandError,
    is_read_only,
)
from tests.command_spec import Forbidden, SpecEntry, load_spec
from tests.fakes import FakeTransport

SPEC = load_spec()
HEX = "0123456789ABCDEF"


def _code_allows(cmd: str) -> bool:
    """Was ``Elm327.command`` überhaupt durchlässt (04 nur in ``allow_clear``)."""
    return is_read_only(cmd) or cmd == CLEAR_COMMAND


# --- die Spezifikation selbst -----------------------------------------------------


@pytest.mark.parametrize("entry", SPEC.allowed, ids=lambda e: e.command or e.pattern)
def test_every_allowed_command_has_a_source(entry: SpecEntry) -> None:
    assert (entry.command is None) != (entry.pattern is None)
    assert entry.source in ("ELM327DSJ", "J1979")
    assert 1 <= entry.page <= 94
    assert entry.quote.strip()
    assert entry.meaning.strip()


def test_adapter_commands_never_reach_the_vehicle() -> None:
    for entry in SPEC.allowed:
        is_at = (entry.command or entry.pattern or "").startswith("AT")
        assert entry.reaches_vehicle is not is_at, entry
        assert not (is_at and entry.writes), entry


def test_only_mode_04_writes_and_only_inside_allow_clear() -> None:
    writing = [e for e in SPEC.allowed if e.writes]
    assert [(e.command, e.requires) for e in writing] == [(CLEAR_COMMAND, "allow_clear")]


def test_forbidden_and_allowed_do_not_overlap() -> None:
    for forbidden in SPEC.forbidden:
        assert not SPEC.allows(forbidden.command), forbidden


# --- Freigabeliste == Spezifikation -------------------------------------------------


def test_at_commands_are_exactly_the_specified_ones() -> None:
    specified = {e.command for e in SPEC.allowed if e.command and e.command.startswith("AT")}
    assert set(AT_COMMANDS) == specified


def _hex_candidates() -> list[str]:
    """Alle Hex-Befehle mit 1 bis 4 Zeichen und alle mit 6 Zeichen, die mit 0 beginnen
    (jeder OBD-Mode bis 0F); längere erlaubt weder Code noch Spezifikation."""
    short = ("".join(p) for n in range(1, 5) for p in itertools.product(HEX, repeat=n))
    six = ("0" + "".join(p) for p in itertools.product(HEX, repeat=5))
    return [*short, *six]


def test_whitelist_equals_spec_for_every_hex_command() -> None:
    """Erschöpfend: über 1,1 Mio. Hex-Befehle erlauben Code und Spezifikation dasselbe."""
    mismatches = [c for c in _hex_candidates() if _code_allows(c) != SPEC.allows(c)]
    assert mismatches == [], mismatches[:20]


@given(st.text(alphabet=HEX + "ATSPZEHLRVDN\r\n ", max_size=10))
def test_whitelist_equals_spec_for_any_text(cmd: str) -> None:
    assert _code_allows(cmd) == SPEC.allows(cmd)


@given(st.text(max_size=12))
def test_whitelist_equals_spec_for_arbitrary_text(cmd: str) -> None:
    assert _code_allows(cmd) == SPEC.allows(cmd)


# --- Gegenbeispiele aus dem Datenblatt ---------------------------------------------


@pytest.mark.parametrize("forbidden", SPEC.forbidden, ids=lambda f: repr(f.command))
def test_forbidden_command_is_refused_and_nothing_is_sent(forbidden: Forbidden) -> None:
    assert forbidden.source == "ELM327DSJ" and forbidden.quote.strip()
    assert not _code_allows(forbidden.command)
    transport = FakeTransport({})
    with pytest.raises(ForbiddenCommandError):
        Elm327(transport).command(forbidden.command)
    assert transport.sent == []


def test_mode_04_is_refused_outside_allow_clear() -> None:
    transport = FakeTransport({"04": "44"})
    elm = Elm327(transport)
    with pytest.raises(ForbiddenCommandError):
        elm.command(CLEAR_COMMAND)
    assert transport.sent == []
