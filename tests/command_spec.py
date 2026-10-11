"""Spezifikation der erlaubten Befehle aus ``tests/fixtures/command_spec.yaml``.

Die Datei ist aus dem ELM327-Datenblatt und SAE J1979 geschrieben, mit Seite und Zitat
je Befehl; sie verwendet bewusst nichts aus ``obd_diag``. Genutzt von
``tests/verification/test_command_spec.py`` und der Prüfung aller gesendeten Befehle in
``tests/conftest.py``.
"""

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import yaml

SPEC_PATH = Path(__file__).parent / "fixtures" / "command_spec.yaml"


@dataclass(frozen=True)
class SpecEntry:
    command: str | None
    pattern: str | None
    reaches_vehicle: bool
    writes: bool
    requires: str | None
    meaning: str
    meaning_en: str
    source: str
    page: int
    quote: str

    def matches(self, cmd: str) -> bool:
        if self.command is not None:
            return cmd == self.command
        assert self.pattern is not None
        return re.fullmatch(self.pattern, cmd) is not None


@dataclass(frozen=True)
class Forbidden:
    command: str
    reason: str
    reason_en: str
    source: str
    page: int
    quote: str


@dataclass(frozen=True)
class CommandSpec:
    allowed: tuple[SpecEntry, ...]
    forbidden: tuple[Forbidden, ...]

    def entry(self, cmd: str) -> SpecEntry | None:
        """Der erste Eintrag, der ``cmd`` erlaubt, sonst ``None``."""
        return next((e for e in self.allowed if e.matches(cmd)), None)

    def allows(self, cmd: str) -> bool:
        return self.entry(cmd) is not None


@cache
def load_spec() -> CommandSpec:
    data = yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))
    allowed = tuple(
        SpecEntry(
            command=e.get("command"),
            pattern=e.get("pattern"),
            reaches_vehicle=e["reaches_vehicle"],
            writes=e["writes"],
            requires=e.get("requires"),
            meaning=e["meaning"],
            meaning_en=e["meaning_en"],
            source=e["source"],
            page=e["page"],
            quote=e["quote"],
        )
        for e in data["allowed"]
    )
    forbidden = tuple(
        Forbidden(e["command"], e["reason"], e["reason_en"], e["source"], e["page"], e["quote"])
        for e in data["forbidden"]
    )
    return CommandSpec(allowed, forbidden)
