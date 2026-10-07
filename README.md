# obd-diag

OBD-II-Diagnose für Linux über ELM327-kompatible Adapter: Fehlercodes lesen und erklären,
Freeze Frame, Readiness, FIN – später Live-Daten und eine QML-Oberfläche.

Design und Roadmap: [OBD-Diagnose – Designvorschlag](https://claude.ai/code/artifact/de949a8a-4f58-41b4-a629-6b9d238bdac7)

## Stand

Roadmap-Schritt 1 (Grundgerüst): Transport-Schicht für USB-Seriell, minimaler
ELM327-Treiber, DTC-Dekodierung, Tests gegen Fake und Emulator, CI.
Roadmap-Schritt 2: `obd-diag scan` liest Fehlercodes und erklärt sie.
Roadmap-Schritt 3 (ohne Oberfläche): `obd-diag clear` löscht Codes nach Sicherung,
`obd-diag ports` findet Adapter.

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

### Adapter finden

```sh
obd-diag ports
```

listet USB-Seriell-Adapter (`/dev/ttyUSB*`, `/dev/ttyACM*` und andere Geräte mit
USB-Kennung) und gebundene Bluetooth-Geräte (`/dev/rfcomm*`, z. B. nach
`sudo rfcomm bind 0 <MAC>`). Eingebaute Schnittstellen (`/dev/ttyS*`) erscheinen nicht.

### Fehlercodes löschen

```sh
obd-diag clear --port /dev/ttyUSB0          # zeigt die Codes und fragt nach
obd-diag clear --port /dev/ttyUSB0 --yes    # ohne Rückfrage
```

`clear` ist die einzige schreibende Aktion (Mode 04) und hält sich an feste Regeln:

1. Erst lesen: Scan wie bei `scan`. Gibt es keine gespeicherten oder ausstehenden
   Codes, wird nichts gesendet. Permanente Codes (Mode 0A) löscht Mode 04 nicht, sie
   verschwinden erst, wenn das Steuergerät den Fehler in Fahrzyklen als behoben sieht.
2. Vorbedingungen: das Steuergerät antwortet (Zündung an), die Bordspannung liegt
   nicht unter 11,8 V und die Drehzahl ist 0 (Motor aus). Ist die Drehzahl nicht
   lesbar, wird ebenfalls abgelehnt.
3. Rückfrage: die Codes werden aufgelistet, gelöscht wird nur nach Eingabe von `ja`
   (oder mit `--yes`).
4. Sicherung: Scan-Ergebnis, Freeze Frame (Mode 02: auslösender Code, Last,
   Kühlmitteltemperatur, Drehzahl, Geschwindigkeit, roh und dekodiert), Zeitpunkt,
   Adapter und Protokoll als JSON nach `$XDG_DATA_HOME/obd-diag/backups/`
   (Standard `~/.local/share/obd-diag/backups/`). Die Datei wird vollständig
   geschrieben, bevor Mode 04 gesendet wird; vorhandene Sicherungen werden nie
   überschrieben.
5. Erst dann Mode 04, danach ein Kontroll-Scan. Lehnt das Steuergerät ab
   (`7F 04 22`: Bedingungen nicht erfüllt), bricht `clear` mit Fehlermeldung ab;
   die Sicherung bleibt.

Schlägt ein Schritt vor dem Löschen fehl, wird Mode 04 nicht gesendet. Mit dem Löschen
gehen auch Freeze Frame und Readiness-Status verloren; ist der Fehler nicht behoben,
kommen die Codes wieder.

Im Emulator läuft der Motor standardmäßig (`010C` liefert wechselnde Drehzahlen ab
1303 1/min), `clear` lehnt also ab. Die Tests setzen die Drehzahl über
`emulator.answer["RPM"]` auf 0 (siehe `tests/integration/test_clear_emulator.py`).

## Struktur

```
src/obd_diag/
├── transport/   # Byte-Kanal zum Adapter (Protocol, USB-Seriell, Adaptersuche)
├── protocol/    # ELM327-Befehle, OBD-II-Dekodierung
├── services/    # Diagnose-Abläufe (Scan, Löschen)
├── data/        # DTC-Katalog (SQLite), FIN (folgt)
├── ui/          # QML (folgt)
└── cli.py
```
