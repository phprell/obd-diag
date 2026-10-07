# obd-diag

OBD-II-Diagnose für Linux über ELM327-kompatible Adapter: Fehlercodes lesen und erklären,
Freeze Frame, Readiness, FIN – später Live-Daten und eine QML-Oberfläche.

Design und Roadmap: [OBD-Diagnose – Designvorschlag](https://claude.ai/code/artifact/de949a8a-4f58-41b4-a629-6b9d238bdac7)

## Stand

Roadmap-Schritt 1 (Grundgerüst): Transport-Schicht für USB-Seriell, minimaler
ELM327-Treiber, DTC-Dekodierung, Tests gegen Fake und Emulator, CI.
Roadmap-Schritt 2: `obd-diag scan` liest Fehlercodes und erklärt sie.

## Entwicklung

```sh
uv sync                     # Umgebung inkl. Dev-Werkzeuge
uv run pytest               # Unit- und Emulator-Tests
uv run ruff check . && uv run ruff format --check .
uv run mypy
```

Ohne uv: `python -m venv .venv && .venv/bin/pip install -e . pytest ruff mypy types-pyserial ELM327-emulator`.

### Fehlercode-Katalog

Die Klartexte zu den Fehlercodes (Deutsch/Englisch, Ursachen, Symptome, Kostenrahmen)
liegen offline in `src/obd_diag/data/dtc_catalog.sqlite`. Die Datei wird nicht
eingecheckt, sondern gebaut – vor dem ersten Start und vor `uv build`:

```sh
uv run python tools/build_dtc_db.py                     # lädt die Daten von GitHub
uv run python tools/build_dtc_db.py --source ../OBDex   # oder aus lokalem Checkout
```

Datenquelle ist [OBDex](https://github.com/foerbsnavi/OBDex) (Daten unter
[CC0-1.0](https://creativecommons.org/publicdomain/zero/1.0/), Code MIT), fest auf einen
Commit gepinnt (`OBDEX_COMMIT` im Skript). Quelle, Commit, Lizenz und Bauzeit stehen in
der Tabelle `meta` des Katalogs. Ein Test gegen den echten Katalog läuft nur, wenn er
gebaut ist.

### Ohne Auto testen

Der [ELM327-emulator](https://github.com/Ircama/ELM327-emulator) stellt ein virtuelles
serielles Gerät bereit:

```sh
uv run elm -s car      # zeigt das pty an, z. B. /dev/pts/5
uv run obd-diag info --port /dev/pts/5
```

### Echter Adapter

```sh
obd-diag info --port /dev/ttyUSB0
```

Rechte: unter Arch heißt die Gruppe `uucp` (Debian/Ubuntu: `dialout`):
`sudo usermod -aG uucp $USER` – oder die udev-Regel aus `packaging/` installieren.

### Fehlercodes lesen

```sh
obd-diag scan --port /dev/ttyUSB0              # Tabelle, Texte auf Deutsch
obd-diag scan --port /dev/ttyUSB0 --lang en    # Texte auf Englisch
obd-diag scan --port /dev/ttyUSB0 --json       # maschinenlesbar
```

`scan` zeigt Adapter, Fahrzeugprotokoll und Bordspannung (mit Warnung unter 11,8 V)
und listet gespeicherte (Mode 03), ausstehende (Mode 07) und permanente (Mode 0A)
Fehlercodes mit Klartext aus dem Offline-Katalog. Fehlt der Katalog, erscheinen die
Codes ohne Beschreibung (bauen mit `uv run python tools/build_dtc_db.py`). Es wird
nur gelesen, nichts gelöscht.

Im Emulator sind standardmäßig keine Codes gesetzt; die Tests geben sie über die
Listen `DTC_STORED`, `DTC_PENDING` und `DTC_PERMANENT` in `elm.obd_message` vor
(siehe `tests/integration/test_scan_emulator.py`).

## Struktur

```
src/obd_diag/
├── transport/   # Byte-Kanal zum Adapter (Protocol + USB-Seriell)
├── protocol/    # ELM327-Befehle, OBD-II-Dekodierung
├── services/    # Diagnose-Abläufe (Scan)
├── data/        # DTC-Katalog (SQLite), FIN (folgt)
├── ui/          # QML (folgt)
└── cli.py
```
