# Kommandozeile

Alle Funktionen stehen unter einem Befehl `obd-diag` mit Unterbefehlen. Befehle mit
Adapter nehmen `--port` (Standard `/dev/ttyUSB0`), `--baud` (Standard 38400) und
`--trace` für einen Mitschnitt ({doc}`sitzungen`). `--lang de` oder `--lang en` stellt die
Sprache aller Ausgaben ein ({doc}`sprache`).

| Befehl | Zweck | sendet ans Fahrzeug |
| --- | --- | --- |
| `obd-diag ports` | Adapter auflisten | nichts |
| `obd-diag info` | Adapter-Kennung und Bordspannung | nichts (nur Adapter-Befehle) |
| `obd-diag scan` | Fehlercodes lesen | Mode 01, 03, 07, 0A |
| `obd-diag diagnose` | Fehlercodes, Readiness, Freeze Frame, FIN | Mode 01, 02, 03, 07, 09, 0A |
| `obd-diag vin` | FIN lesen oder eine eingegebene dekodieren | Mode 09 |
| `obd-diag live` | Live-Daten anzeigen und aufzeichnen | Mode 01 |
| `obd-diag export` | gespeicherte Sitzung als PDF/CSV | nichts |
| `obd-diag clear` | Fehlercodes löschen (gesperrt) | Mode 04 |

## Beispiele

```sh
obd-diag scan --port /dev/ttyUSB0                  # Tabelle, Sprache wie das System
obd-diag scan --port /dev/ttyUSB0 --lang en --json # englisch, maschinenlesbar
obd-diag diagnose --port /dev/ttyUSB0 --save --pdf bericht.pdf
obd-diag vin WVWZZZ1KZ6W123456                     # nur dekodieren, ohne Adapter
obd-diag live --port /dev/ttyUSB0 --pids rpm,speed --interval 0.5 --record
obd-diag export ~/.local/share/obd-diag/sessions/session-20261007-143205.json --pdf bericht.pdf
```

Bei `--json` bleibt die Standardausgabe reines JSON; Hinweise und Pfade gespeicherter
Dateien stehen auf der Fehlerausgabe. Rückgabewert 0 heißt Erfolg, 1 Fehler.

## Alle Befehle und Optionen

Die folgende Referenz wird beim Bauen der Dokumentation aus dem Programm selbst
erzeugt und ist damit immer auf dem Stand des Codes.

```{argparse}
:module: obd_diag.cli
:func: build_parser
:prog: obd-diag
```
