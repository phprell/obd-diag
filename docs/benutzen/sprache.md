# Sprache

obd-diag spricht Deutsch und Englisch: Oberfläche, Kommandozeile, PDF-Bericht, CSV und
die Fehlercode-Texte aus dem Katalog.

## Welche Sprache gilt

- **Oberfläche:** Menü *Optionen → Sprache / Language*, dann *Deutsch* oder *English*.
  Der Menüname ist zweisprachig, damit man ihn in jeder Sprache findet. Die Wahl wird je
  Nutzer gespeichert (`~/.config/obd-diag/obd-diag.conf`) und gilt sofort für alle
  Menüs, Reiter und Meldungen. Fehlercode-Texte aus dem Katalog wechseln ab dem nächsten
  Scan.
- **Kommandozeile:** `--lang de` oder `--lang en`, vor oder nach dem Unterbefehl
  (`obd-diag --lang en scan` oder `obd-diag scan --lang en`). Gilt für alle Ausgaben,
  die Hilfetexte und die Fehlercode-Texte.
- **Standard:** die Systemsprache wie bei anderen Programmen (`LANGUAGE`, `LC_ALL`,
  `LC_MESSAGES`, `LANG`). Deutsch, wenn die Systemsprache Deutsch ist, sonst Englisch.

```sh
obd-diag --lang en diagnose --port /dev/ttyUSB0 --pdf report.pdf
LANG=de_DE.UTF-8 obd-diag scan --port /dev/ttyUSB0     # Deutsch, aus der Systemsprache
```

## Was sich mit der Sprache ändert

| | Deutsch | Englisch |
| --- | --- | --- |
| Texte und Fehlercode-Katalog | Deutsch | Englisch |
| Zahlen in Oberfläche und PDF | `12,4 V` | `12.4 V` |
| CSV (Export und Live-Aufzeichnung) | Trennzeichen `;`, Dezimalkomma | Trennzeichen `,`, Dezimalpunkt |
| Datum im PDF-Bericht | `07.10.2026, 14:32 Uhr` | `2026-10-07, 14:32` |

Gespeicherte Sitzungen sind sprachneutral bis auf die Fehlercode-Texte, die in der
Sprache des Scans bleiben ({doc}`sitzungen`). Mitschnitte, Protokollmeldungen und die
Entwickler-Doku im Code (Docstrings, Kommentare) bleiben deutsch.

## Übersetzungen

Die Texte im Code sind deutsch, die englischen Übersetzungen stehen in
`src/obd_diag/locale/en.po`. Wie neue Texte dazukommen, steht unter
{doc}`../entwickeln/regeln`.
