# Mitmachen und Regeln

## Befehle

```sh
uv sync                                   # Umgebung inkl. Dev-Werkzeuge
uv run python tools/build_dtc_db.py       # Fehlercode-Katalog
uv run ruff format . && uv run ruff check . && uv run mypy   # mypy strict
uv run pytest                             # ca. 1800 Tests, ~4,5 min
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
- **Sprache:** Deutsch in Kommentaren, Docstrings und Commit-Messages. Oberfläche,
  Ausgaben und Doku gibt es auf Deutsch und Englisch (unten). ruff meldet
  Gedankenstriche in Python-Strings; dort `:` oder `-` nehmen.

## Übersetzungen

Texte im Code sind deutsch und gehen durch `tr("…")` (`obd_diag.i18n`); Platzhalter in
geschweiften Klammern, gefüllt nach dem Übersetzen:
`tr("Verbinde mit {port} …").format(port=port)`, nie ein f-String in `tr`. Konstanten,
die beim Import feststehen, markiert `N_("…")`, übersetzt wird bei der Ausgabe mit
`tr`; Einzahl/Mehrzahl mit `trn`. In QML `qsTr("…")` mit `.arg()`. Die englischen Texte
stehen in `src/obd_diag/locale/en.po`:

```sh
uv run python tools/translations.py           # neue Texte eintragen, alte entfernen
uv run python tools/translations.py --todo    # fehlende als JSON {"Nummer": "Text"}
uv run python tools/translations.py --fill uebersetzungen.json
```

`tests/unit/test_i18n.py` schlägt fehl, wenn der Katalog nicht aktuell ist, eine
Übersetzung fehlt, Platzhalter nicht übereinstimmen oder ein deutscher Text ohne
`tr`/`qsTr` ausgegeben wird. Protokollmeldungen (`log.…`) bleiben deutsch.

## Dokumentation

Die Dokumentation liegt unter `docs/` (Markdown mit MyST, Sphinx, Furo-Theme), die
englische Fassung mit denselben Dateinamen unter `docs/en/`. Die CI baut beide bei jedem
PR und Push auf `main` und legt das HTML als Artefakt „dokumentation“ am Lauf ab
(`.github/workflows/docs.yml`). Jeder Push auf `main` veröffentlicht sie unter
<https://phprell.github.io/obd-diag/> (Deutsch) und
<https://phprell.github.io/obd-diag/en/> (Englisch). Lokal bauen:

```sh
uv run --group docs sphinx-build -W docs docs/_build
uv run --group docs sphinx-build -W docs/en docs/_build/en
python -m http.server -d docs/_build      # dann http://localhost:8000
```

Jede Seite gibt es in beiden Sprachen; der Link oben in der Seitenleiste wechselt zur
selben Seite in der anderen Sprache. `tests/unit/test_docs_i18n.py` prüft, dass beide
Bäume dieselben Seiten mit gleich vielen Überschriften, Codeblöcken und Bildern haben.
Wer eine Seite ändert, ändert die andere Sprache im selben PR mit.
`-W` macht Warnungen zu Fehlern, z. B. kaputte Verweise. Was sich aus dem Code
erzeugen lässt, wird erzeugt und nicht abgeschrieben:

| Seite | Quelle | Erzeugt von |
| --- | --- | --- |
| {doc}`../technik/befehle` | `tests/fixtures/command_spec.yaml` | `tools/docs_tables.py` |
| {doc}`../technik/pids` | `PIDS` in `protocol/pids.py` | `tools/docs_tables.py` |
| {doc}`../benutzen/cli` (Optionen) | `build_parser` in `cli.py` | sphinx-argparse |
| {doc}`../api/index` | Docstrings | autodoc |
| Bilder unter {doc}`../benutzen/oberflaeche` | Oberfläche gegen den Emulator | `uv run python tools/docs_screenshots.py` (englisch: `--lang en`) |

Bei neuen Funktionen gehört die passende Seite unter „Benutzen“ in denselben PR. Nach
Änderungen an der Oberfläche die Bilder neu erzeugen und einchecken. Byte-Beispiele auf
den Technik-Seiten prüft `tests/unit/test_docs_examples.py` gegen die Parser; wer ein
Beispiel ändert, ändert den Test mit.
