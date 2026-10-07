# obd-diag

Linux-OBD-II-Diagnose-Tool (Python 3.12+, später PySide6/QML). Spezifikation:
Claude-Docs-Dokument „OBD-Diagnose – Designvorschlag“
https://claude.ai/code/artifact/de949a8a-4f58-41b4-a629-6b9d238bdac7 – Architektur-
und Roadmap-Änderungen dort nachziehen. Die Ideen stammen aus einem Cowork-Projekt,
auf das Claude Code keinen Zugriff hat.

## Regeln
- Strikte Schichten: transport → protocol → services → ui. Höhere Schichten kennen nur
  das `Transport`-Protocol, nie pyserial direkt.
- python-OBD (falls eingesetzt) nur hinter eigener Schnittstelle kapseln.
- Standardmäßig nur lesend. Schreibende OBD-Befehle (Mode 04) nur mit Bestätigung und
  nach Sichern von Codes + Freeze Frame. Keine Codierung/Flashen.
- Fehlercode-Texte offline (SQLite), Online-APIs nur optional.
- Sprache in UI, Doku und Kommentaren: Deutsch.

## Befehle
- `uv run pytest` (Integrationstests brauchen `ELM327-emulator`, sonst übersprungen)
- `uv run ruff check . && uv run ruff format --check .`
- `uv run mypy` (strict)
