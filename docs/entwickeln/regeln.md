# Mitmachen und Regeln

## Befehle

```sh
uv sync                                   # Umgebung inkl. Dev-Werkzeuge
uv run python tools/build_dtc_db.py       # Fehlercode-Katalog
uv run ruff format . && uv run ruff check . && uv run mypy   # mypy strict
uv run pytest                             # ca. 1600 Tests, ~3,5 min
```

Den Exit-Code von `pytest` selbst prüfen, nicht durch `| tail` verdecken.

## Regeln

- **Schichten:** transport → protocol → services → ui/cli, keine Importe nach oben;
  höhere Schichten kennen nur das `Transport`-Protocol ({doc}`../technik/architektur`).
- **Nur lesend:** Mode 04 nur über `services/clear.py`, Löschen bleibt gesperrt, bis
  das Lesen am Auto geprüft ist. Keine Codierung, kein Flashen, keine
  Herstellerbefehle ({doc}`../technik/sicherheit`).
- **Neue Befehle** bewusst in der Freigabeliste (`protocol/elm327.py`) freigeben und in
  `tests/fixtures/command_spec.yaml` mit Datenblatt-Seite und Zitat eintragen. Sonst
  schlagen die Tests fehl, und die {doc}`../technik/befehle` zeigt sie automatisch.
- **Standardtreue:** Parser halten sich an SAE J1979, ISO 15765 und das
  ELM327-Datenblatt. Abweichungen des Emulators werden in den Tests korrigiert, nicht im
  Produktcode.
- **Offline:** Fehlercode-Texte aus SQLite. Online nur nach Opt-in: NHTSA vPIC (FIN)
  und Kurzerklärungen für Codes ohne Katalogtext ({doc}`../adr/0004-online-erklaerungen`).
- **Sprache:** Deutsch in Oberfläche, Doku, Kommentaren und Commit-Messages. ruff meldet
  Gedankenstriche in Python-Strings; dort `:` oder `-` nehmen.

## Dokumentation

Die Dokumentation liegt unter `docs/` (Markdown mit MyST, Sphinx, Furo-Theme). Die CI
baut sie bei jedem PR und Push auf `main` und legt das HTML als Artefakt
„dokumentation“ am Lauf ab (`.github/workflows/docs.yml`). Nach GitHub Pages
veröffentlicht wird nur, wenn der Workflow von Hand gestartet wird. Lokal bauen:

```sh
uv run --group docs sphinx-build -W docs docs/_build
python -m http.server -d docs/_build      # dann http://localhost:8000
```

`-W` macht Warnungen zu Fehlern, z. B. kaputte Verweise. Was sich aus dem Code
erzeugen lässt, wird erzeugt und nicht abgeschrieben:

| Seite | Quelle | Erzeugt von |
| --- | --- | --- |
| {doc}`../technik/befehle` | `tests/fixtures/command_spec.yaml` | `tools/docs_tables.py` |
| {doc}`../technik/pids` | `PIDS` in `protocol/pids.py` | `tools/docs_tables.py` |
| {doc}`../benutzen/cli` (Optionen) | `build_parser` in `cli.py` | sphinx-argparse |
| {doc}`../api/index` | Docstrings | autodoc |
| Bilder unter {doc}`../benutzen/oberflaeche` | Oberfläche gegen den Emulator | `uv run python tools/docs_screenshots.py` |

Bei neuen Funktionen gehört die passende Seite unter „Benutzen“ in denselben PR. Nach
Änderungen an der Oberfläche die Bilder neu erzeugen und einchecken. Byte-Beispiele auf
den Technik-Seiten prüft `tests/unit/test_docs_examples.py` gegen die Parser; wer ein
Beispiel ändert, ändert den Test mit.
