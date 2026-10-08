# obd-diag

OBD-II-Diagnose für Linux über ELM327-kompatible Adapter: Fehlercodes lesen und erklären,
Freeze Frame, Readiness, FIN, Live-Daten mit Aufzeichnung, als Kommandozeile und
Desktop-Oberfläche (PySide6/QML). Standardmäßig nur lesend: der einzige schreibende
Befehl ist das Löschen der Fehlercodes, und das nur nach Prüfung und Sicherung.

> **Löschen ist vorerst deaktiviert.** Erst wenn das Lesen an einem echten Fahrzeug
> geprüft ist, wird es freigegeben (`CLEAR_ENABLED` in `services/clear.py`). Bis dahin
> bricht `obd-diag clear` ab, ohne den Port zu öffnen, und die Schaltfläche in der
> Oberfläche bleibt grau. Der Ablauf unten beschreibt, wie es nach der Freigabe läuft.

Design und Roadmap: [OBD-Diagnose – Designvorschlag](https://claude.ai/code/artifact/de949a8a-4f58-41b4-a629-6b9d238bdac7)

## Stand

- **v0.1** (Roadmap-Schritte 1–4, in `main`): Fehlercodes (Mode 03/07/0A) mit deutschem
  Klartext, Readiness, Freeze Frame, FIN mit Offline-Dekodierung, sicheres Löschen,
  Diagnosesitzungen als JSON, PDF-Bericht und CSV, Adapter-Mitschnitt, Oberfläche
  mit hellem und dunklem Design.
- **v0.2** (in `main`, Version 0.2.0, noch ohne Release-Tag): Live-Daten nach SAE
  J1979 (116 Werte aus 82 PIDs, darunter alle Lambdasonden), Aufzeichnung als CSV,
  Reiter „Live-Daten“ und `obd-diag live`.
- **Noch nicht am echten Auto getestet.** Die Antwortverarbeitung ist ohne Hardware
  gegen Datenblatt, echte Mitschnitte und python-OBD geprüft (siehe „Entwicklung“).
  Ablauf für den ersten Test: „Erster Test am Auto“ unten.
- Geplant (v0.3+): Bluetooth LE, Community-Profile, Fehlerspeicher aller
  Steuergeräte über UDS (nur lesend), siehe Designdokument.

## Entwicklung

```sh
uv sync                     # Umgebung inkl. Dev-Werkzeuge
uv run pytest               # Unit- und Emulator-Tests
uv run ruff check . && uv run ruff format --check .
uv run mypy
```

Ohne uv: `python -m venv .venv && .venv/bin/pip install -e '.[gui]' pytest pytest-qt pypdf ruff mypy types-pyserial types-reportlab ELM327-emulator hypothesis pyyaml`.

`tests/verification` prüft die Antwortverarbeitung ohne Adapter gegen echte Mitschnitte
(ELM327-Datenblatt, Nutzer-Logs aus python-OBD/ELMduino/AndrOBD, Quellen in
`tests/fixtures/traces/`), gegen den DTC-Decoder von python-OBD und mit
Hypothesis-Round-Trip- und Fuzz-Tests. Die Formeln der Live-Daten werden für jeden
Bytewert mit python-OBD verglichen (`test_pid_differential.py`); die zwei
Abweichungen (PID `32` und `44`) sind dort gegen J1979 begründet. python-OBD steht
unter GPL-2.0 und kommt nur in Tests vor.

Mutationstests (Freigabeliste, Löschen, Live-Daten, Ablage, Scan, Sitzung, Katalog,
Mitschnitt; Konfiguration unter `[tool.mutmut]` in `pyproject.toml`, rund zwei
Minuten):
`uv run --with mutmut mutmut run`, danach `uv run --with mutmut mutmut results`.
In `services/clear.py` darf kein Mutant überleben. In `protocol/elm327.py`,
`protocol/obd.py`, `protocol/pids.py` und `services/live.py` überleben nur Mutanten an
Log- und Fehlertexten oder gleichwertige (z. B. `"utf-8"` → `"UTF-8"`); keiner davon
ändert, ob oder wie oft etwas gesendet wird. Tests, die den Quelltext selbst prüfen,
laufen unter mutmut nicht. Der Ordner `mutants/` ist nur Arbeitskopie.

Die GUI-Tests (`tests/ui`) laufen ohne Bildschirm (`QT_QPA_PLATFORM=offscreen`, setzt
`tests/ui/conftest.py`) und werden übersprungen, wenn PySide6 fehlt.

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
serielles Gerät bereit. `tools/emulator.py` startet ihn mit standardgemäßen Antworten
(der Emulator lässt sonst bei CAN das Zählbyte der Fehlercodes weg und beantwortet den
Freeze Frame nicht nach SAE J1979, siehe `tests/emulator_patches.py`):

```sh
uv run python tools/emulator.py                    # zeigt das pty an, z. B. /dev/pts/5
uv run python tools/emulator.py --stored P0420,P0300 --pending P0171 --engine-off
uv run obd-diag diagnose --port /dev/pts/5
```

`--engine-off` meldet Drehzahl 0, damit sich das Löschen ausprobieren lässt.
`uv run elm -s car` direkt funktioniert für `info`, aber nicht für Fehlercodes.

### Echter Adapter

```sh
obd-diag info --port /dev/ttyUSB0
```

Unterstützt werden ELM327-kompatible Adapter über USB und klassisches Bluetooth
(`/dev/rfcomm*`). Bluetooth LE kommt erst mit v0.3. Billige „v1.5“-Klone sind oft
fehlerhaft; Adapter mit echtem ELM327- oder STN-Chip (z. B. OBDLink) sind
verlässlicher.

Rechte: unter Arch heißt die Gruppe `uucp` (Debian/Ubuntu: `dialout`):
`sudo usermod -aG uucp $USER` – oder die udev-Regel aus `packaging/` installieren.

### Erster Test am Auto

1. Motor aus, Zündung an (bei Start-Stopp-Knopf: drücken, ohne auf die Bremse zu
   treten).
2. `obd-diag ports` zeigt den Adapter, `obd-diag info --port …` Version und
   Bordspannung.
3. `obd-diag diagnose --port … --save --trace`: liest alles, nur lesend, und schneidet
   die Kommunikation mit.
4. Optional mit laufendem Motor: `obd-diag live --port … --duration 30 --record --trace`.
5. `clear` ist bis dahin gesperrt. Erst wenn Ausgabe und Mitschnitt geprüft sind, wird
   Löschen freigegeben. Aus dem Mitschnitt wird mit `ReplayTransport` ein
   Regressionstest.

Wird die Verbindung mitten in der Abfrage unterbrochen (Adapter abgezogen, Stecker vom
Auto ab, Bluetooth weg), bricht das Tool mit einer Meldung ab („Verbindung zu …
unterbrochen“ bzw. „keine Antwort von …“). Danach wird nichts mehr gesendet, eine
halbe Diagnose wird nicht gespeichert, und eine Live-Aufzeichnung behält alle
vollständigen Runden. Meldet der Adapter dagegen Busfehler (Zündung aus), endet
Live nach drei Runden ohne Antwort.

Die genormte Diagnose sieht nur abgasrelevante Steuergeräte (Motor, Getriebe).
Airbag, ABS, Komfortelektronik usw. brauchen herstellerspezifische Diagnose; die ist
noch nicht eingebaut (geplant: nur lesend über UDS).

### Sicherheit: was das Tool senden kann

Das Tool soll am Auto nichts verändern können. Dafür gibt es eine harte Grenze im
Code und Tests, die sie prüfen:

- **Freigabeliste** (`protocol/elm327.py`): `Elm327.command` ist der einzige Weg zum
  Adapter. Durch geht nur, was dort steht: Adapter-Befehle (`ATZ`, `ATE0`, `ATRV` …),
  die nichts ans Fahrzeug senden, und lesende OBD-Anfragen (`01xx`, `02xx[00]`, `03`,
  `07`, `0A`, `0902`). Alles andere wird **vor** dem Senden mit
  `ForbiddenCommandError` abgewiesen, auch Kleinschreibung, Leerzeichen oder ein
  angehängter zweiter Befehl.
- **Löschen** (`04`) ist nur innerhalb von `clear_dtcs` freigeschaltet und läuft nur
  über den Ablauf unter „Fehlercodes löschen“.
- **Keine Codierung, kein Flashen, keine Servicefunktionen** (Routinen, Aktoren,
  Anlernwerte). Das ist eine bewusste Entscheidung (`docs/adr/0002-nur-lesend.md`).

Geprüft wird das so:

- Freigabeliste mit Hypothesis: beliebiger Text ist entweder freigegeben oder wird
  nie gesendet; für jede Funktion steht die exakte Befehlsfolge im Test, gegen
  Datenblatt und J1979 geprüft.
- Löschen mit beliebig kaputten Antworten: `04` geht höchstens einmal hinaus, und nur
  wenn alle Vorbedingungen nachweislich erfüllt sind und die Sicherung auf der Platte
  liegt.
- Live-Daten mit beliebigen Antworten: nur Lesendes, nie `04`.
- Aufrufgraph (AST): nur die vorgesehenen Stellen erreichen `allow_clear`,
  `clear_dtcs`, `clear_codes`, den Transport und pyserial; keine Sockets, kein
  `os.write`, kein `eval`/`getattr`.
- Jeder gesendete Befehl hat auf der Leitung genau das ELM327-Format (Großbuchstaben,
  Ziffern, ein CR), auch in den Emulator-Tests über den echten seriellen Transport.

Siehe `tests/unit/test_command_guard.py`, `test_write_safety.py`, `test_live_safety.py`.

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

**Derzeit deaktiviert** (siehe oben). Die Tests prüfen den Ablauf trotzdem vollständig,
damit er bei der Freigabe stimmt.

Das Tool kann technisch nur freigegebene Befehle senden: Adapter-Befehle (`AT…`) und
lesende OBD-Anfragen (`01xx`, `02xx00`, `03`, `07`, `0A`, `0902`). `04` (Löschen) ist
nur innerhalb des Lösch-Ablaufs freigeschaltet; alles andere wird vor dem Senden
abgewiesen (`ForbiddenCommandError`).

```sh
obd-diag clear --port /dev/ttyUSB0          # zeigt die Codes und fragt nach
obd-diag clear --port /dev/ttyUSB0 --yes    # ohne Rückfrage
```

`clear` ist die einzige schreibende Aktion (Mode 04) und hält sich an feste Regeln:

1. Erst lesen: Scan wie bei `scan`. Gibt es keine gespeicherten oder ausstehenden
   Codes, wird nichts gesendet. Permanente Codes (Mode 0A) löscht Mode 04 nicht, sie
   verschwinden erst, wenn das Steuergerät den Fehler in Fahrzyklen als behoben sieht.
2. Vorbedingungen: das Steuergerät antwortet (Zündung an), die Bordspannung liegt
   nicht unter 11,8 V und die Drehzahl ist 0 (Motor aus). Antworten mehrere
   Steuergeräte (z. B. Motor und Getriebe), muss jedes gültig 0 melden. Ist eine
   Drehzahl nicht lesbar, wird ebenfalls abgelehnt.
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

### Vollständige Diagnose

```sh
obd-diag diagnose --port /dev/ttyUSB0                   # Tabelle
obd-diag diagnose --port /dev/ttyUSB0 --json            # Sitzung als JSON
obd-diag diagnose --port /dev/ttyUSB0 --save            # Sitzung speichern, Pfad auf stderr
obd-diag diagnose --port /dev/ttyUSB0 --pdf bericht.pdf --csv codes.csv
obd-diag diagnose --port /dev/ttyUSB0 --online-vin      # FIN zusätzlich bei NHTSA vPIC
```

`diagnose` liest in einem Durchgang, nur lesend (nie Mode 04):

1. Scan wie bei `scan` (Adapter-Reset, Protokoll, Bordspannung, Codes aus Mode 03/07/0A).
   Schlägt er fehl, bricht `diagnose` ab.
2. Readiness (Mode 01 PID 01): MIL, gemeldete Codezahl, Motorart (Otto/Diesel) und je
   Monitor „abgeschlossen“, „nicht abgeschlossen“ oder „nicht unterstützt“, dazu
   „Alle Tests abgeschlossen: ja/nein“ (ja, wenn kein unterstützter Monitor offen
   ist). Antworten mehrere Steuergeräte, zählt je Monitor der schlechteste Stand, die
   MIL ist an, wenn ein Steuergerät sie meldet, und die Codezahlen werden addiert.

   Das ist bewusst **keine AU-Bewertung** (früher „AU-bereit“): Seit der AU-Richtlinie
   von 2017 (Verkehrsblatt 19/2017, Leitfaden 5.01, ab 01.01.2018) gehört zur AU bei
   allen OBD-Fahrzeugen neben der OBD-Prüfung wieder die Endrohrmessung; offene
   Monitore führen nicht pauschal zum Nichtbestehen. Eine allgemeine, zitierfähige
   Liste, welche offenen Monitore toleriert werden, gibt es nicht – das hängt vom
   Fahrzeug und vom Prüfablauf des AU-Geräts ab. Quellen: Hella Gutmann,
   [Informationen zum Leitfaden 5.01](https://www.hella-gutmann.com/fileadmin/user_upload/Download-Dateien/X_Downloads/downloads_instructions/downloads_manuals_quickstarts/DE/BD0059_HG4_Info_Leitfaden_5-01.pdf)
   (12/2017); zur Regelung ab 2010 (Readiness nicht gesetzt → Abgasmessung statt
   Mangel) die [Zusammenfassung bei werner-austen.de](http://www.werner-austen.de/plaintext/informationen/regelung-abgasuntersuchung-112010/index.php). In JSON heißt das Feld
   `all_complete`; `ready` bleibt als alter Name erhalten.
3. Freeze Frame (Mode 02, Frame 00): auslösender Code, Last, Kühlmitteltemperatur,
   Drehzahl, Geschwindigkeit. Ohne gespeicherten Code ist er leer und erscheint als
   „keiner gespeichert“.
4. FIN (Mode 09 PID 02, CAN mehrteilig oder ältere Protokolle mit fünf Zeilen) und
   ihre Offline-Dekodierung.

Antwortet das Fahrzeug auf Readiness, Freeze Frame oder FIN nicht (oder unbrauchbar),
fehlt nur dieser Teil. `--save` legt die Sitzung wie unten beschrieben ab, `--pdf` und
`--csv` exportieren direkt (wie `obd-diag export`). Bei `--json` bleibt stdout reines
JSON; Pfade gespeicherter Dateien stehen auf stderr.

### FIN

```sh
obd-diag vin --port /dev/ttyUSB0          # aus dem Fahrzeug lesen und dekodieren
obd-diag vin WVWZZZ1KZ6W123456            # nur dekodieren, ohne Adapter
obd-diag vin WVWZZZ1KZ6W123456 --json
```

Die Dekodierung ist offline:

- gültig: 17 Zeichen, nur 0-9 und A-Z ohne I, O, Q;
- Prüfziffer (Stelle 9, ISO 3779 / 49 CFR 565): Pflicht nur in Nordamerika (FIN
  beginnt mit 1-5) und China (`L`), dort „stimmt“/„stimmt nicht“; sonst „stimmt“, wenn
  sie zufällig oder freiwillig passt, und „nicht vorgeschrieben (passt nicht, kein
  Fehler)“ andernfalls;
- Hersteller aus einer Tabelle häufiger Herstellerkennungen (WMI, Stellen 1-3,
  `src/obd_diag/data/wmi.py`, geprüft gegen Wikipedia und NHTSA vPIC, Quellen dort),
  Land aus den ISO-3780-Regionsbereichen der Stellen 1-2 (ISO-Übersicht 2021);
- Modelljahr aus Stelle 10. Der Code wiederholt sich alle 30 Jahre; in Nordamerika
  entscheidet Stelle 7 (Ziffer: 1980-2009, Buchstabe: 2010-2039), sonst gilt das
  jüngste Jahr bis höchstens ein Jahr in der Zukunft als beste Schätzung, und das 30
  Jahre ältere wird mitgenannt: „2026 oder 1996 (aus Stelle 10, ohne Gewähr)“. Kommt
  die FIN aus dem Fahrzeug, fallen ältere Jahre weg, die zum OBD-Protokoll nicht
  passen (OBD-II-Protokolle: nicht vor 1994; CAN nach ISO 15765-4: nicht vor 2000).
  Europäische Hersteller nutzen Stelle 10 nicht alle als Modelljahr, die Angabe ist
  dort ohne Gewähr. In JSON steht das zweite Jahr in `model_year_alternatives`.

**Datenschutz:** Die FIN bleibt auf dem Rechner. Nur mit `--online-vin` wird sie an die
NHTSA-Datenbank [vPIC](https://vpic.nhtsa.dot.gov/api/) (USA) geschickt; übernommen
werden Modell, Modelljahr, Karosserie, Zylinder, Hubraum, Kraftstoff, Werk u. Ä. Die
Antwort wird je FIN unter `$XDG_CACHE_HOME/obd-diag/vpic/` (Standard
`~/.cache/obd-diag/vpic/`) abgelegt, eine FIN wird also nur einmal abgefragt.
Netzwerkfehler werden ignoriert. vPIC kennt vor allem Fahrzeuge für den US-Markt; für
europäische Modelle sind die Angaben oft lückenhaft.

Im Emulator antworten `0101` und `0902` standardgemäß (die FIN wechselt reihum zwischen
drei Beispielen); Mode 02 erwartet er ohne Frame-Nummer, der Freeze Frame bleibt dort
leer. `tests/integration/test_diagnosis_emulator.py` stellt das für die Tests um.

### Live-Daten

```sh
obd-diag live --port /dev/ttyUSB0 --list                      # unterstützte Werte
obd-diag live --port /dev/ttyUSB0                             # übliche Werte, Strg+C beendet
obd-diag live --port /dev/ttyUSB0 --pids rpm,speed,fuel_rail_pressure --interval 0.5
obd-diag live --port /dev/ttyUSB0 --duration 60 --record      # 60 s als CSV aufzeichnen
```

```
Zeit (s)  Motordrehzahl (1/min)  Geschwindigkeit (km/h)  Kühlmitteltemperatur (°C)  …  Spannung (V)
     0.0                   1726                      50                         86  …          14.1
     1.0                   1731                      51                         86  …          14.1
```

- **Werte:** 116 Werte aus 82 Mode-01-PIDs nach SAE J1979 (`protocol/pids.py`), u. a.
  Drehzahl, Geschwindigkeit, Kühlmittel-, Ansaugluft- und Öltemperatur, Last,
  Luftmasse, Saugrohr- und Raildruck, AGR, Lambda-Sollwert, Kraftstofftrimm (auch
  Nachkat), Lambdasonden 1–8 (Schmal- und Breitband), Drehmomentstufen,
  Tankfüllstand, Kraftstoffverbrauch und Kilometerstand. Liefert eine PID mehrere
  Werte (z. B. Sondenspannung und Trimm), wird sie je Runde nur einmal abgefragt.
  Die Sonden heißen 1–8 in PID-Reihenfolge; welche Bank und Position das ist, legt
  das Fahrzeug fest (PID `13` oder `1D`). Angefragt werden nur Werte, die das
  Fahrzeug als unterstützt meldet (`0100`, `0120` … über alle Steuergeräte).
  Ohne `--pids`: Drehzahl, Geschwindigkeit, Kühlmitteltemperatur, Last,
  Ansauglufttemperatur und Steuergerätespannung, soweit unterstützt.
- **Nicht enthalten:** PIDs mit Statusbyte, deren Aufbau sich zwischen Ausgaben der
  Norm geändert hat oder in freien Quellen nicht eindeutig ist, darunter Ladedruck
  (`70`) und Partikelfilter (`7A`–`7C`); Liste im Docstring von `protocol/pids.py`.
- **Ablauf:** Runde für Runde wird jeder Wert einmal gelesen; ist einer nicht lesbar,
  steht „-“ (in der CSV eine leere Zelle), die Abfrage läuft weiter. Meldet der Adapter
  drei Runden lang bei jedem Wert einen Busfehler (z. B. Zündung aus), endet sie mit
  einer Fehlermeldung. Die Bordspannung wird jede zehnte Runde gelesen; unter 11,8 V
  wird nur noch alle 5 s abgefragt.
- **Aufzeichnung:** CSV unter `$XDG_DATA_HOME/obd-diag/recordings/`
  (`live-JJJJMMTT-HHMMSS.csv`) oder in der angegebenen Datei, die nicht überschrieben
  wird. Format wie beim Export: UTF-8 mit BOM, `;`, Dezimalkomma; erste Spalte
  `Zeit (s)`, dann je Wert `Name (Einheit)`, zuletzt `Bordspannung (V)`. Jede Zeile
  wird sofort geschrieben, ein Abbruch verliert also nichts.
- **Nur lesend:** gesendet werden nur `01xx` und `ATRV`.

Während der Fahrt nur durch Beifahrer bedienen. Gegen den Emulator liefert
`tools/emulator.py` zufällig wechselnde Werte bei laufendem Motor.

### Diagnosesitzungen und Export

Eine Diagnosesitzung (Scan mit Klartexten, Readiness, Freeze Frame, FIN, Zeitpunkt)
wird als JSON unter `$XDG_DATA_HOME/obd-diag/sessions/` gespeichert (Standard
`~/.local/share/obd-diag/sessions/`), Dateiname `session-JJJJMMTT-HHMMSS.json` nach
dem Zeitpunkt der Diagnose; vorhandene Dateien werden nie überschrieben (dann
`…-2.json` usw.). Die Datei trägt `"format": "obd-diag-session"` und `"version": 1`,
der Teil `scan` hat dieselbe Form wie `obd-diag scan --json`. Fremde oder neuere
Formate lehnt das Laden mit Meldung ab.

Eine gespeicherte Sitzung lässt sich umwandeln:

```sh
obd-diag export ~/.local/share/obd-diag/sessions/session-20261007-143205.json \
    --pdf bericht.pdf --csv fehlercodes.csv
```

- **PDF** (DIN A4, Deutsch): Fahrzeug (FIN, Prüfziffer, Hersteller, Land, Modelljahr),
  Adapter, Protokoll und Bordspannung (mit Warnung bei niedriger Spannung),
  Kurzübersicht, Readiness mit „Alle Tests abgeschlossen: ja/nein“, Fehlercodes nach Art mit
  Erklärung, Ursachen samt Wahrscheinlichkeit, Symptomen und Kostenrahmen, Freeze
  Frame. Fehlende Teile erscheinen als „nicht verfügbar“. Erzeugt mit
  [ReportLab](https://www.reportlab.com/) (BSD-Lizenz). Schrift: DejaVu Sans,
  Liberation Sans oder Noto Sans, falls installiert (wird eingebettet), sonst
  Helvetica aus dem PDF-Standardumfang.
- **CSV**: eine Zeile pro Fehlercode mit den Spalten Code; Art (Gespeichert,
  Ausstehend, Permanent); Titel; Beschreibung; Ursachen; Symptome; MIL;
  Abgasrelevant; Reparaturaufwand; Kosten; Kosten von (EUR); Kosten bis (EUR);
  Datum; FIN. Mehrere Ursachen/Symptome stehen durch ` | ` getrennt in einer Zelle.
  Kodierung UTF-8 mit BOM und `;` als Trennzeichen, damit ein deutsches Excel die
  Datei per Doppelklick mit Umlauten und Spalten richtig öffnet.


### Oberfläche

```sh
uv run obd-diag-gui
```

Installiert wird die Oberfläche über das Extra `gui` (`pip install 'obd-diag[gui]'`);
ohne es bleibt eine Kopfzeilen-Installation (z. B. auf dem Raspberry Pi) klein.

Oben Port und Baudrate wählen – die Liste zeigt gefundene Adapter, ein Pfad lässt sich
auch eintippen – und „Verbinden & Scannen“ drücken. Das liest in einem Durchgang
Fehlercodes, Readiness, Freeze Frame und FIN (nur lesend). Rechts oben steht dann das
Fahrzeug (Hersteller und FIN), darunter fünf Reiter:

- **Fehlercodes**: links die Codes nach gespeichert, ausstehend und permanent
  gruppiert, rechts die Erklärung des gewählten Codes mit Ursachen, Symptomen und
  Kostenrahmen.
- **Readiness**: „Alle Tests abgeschlossen“ (grün) oder „Nicht alle Tests abgeschlossen“ (gelb) mit den offenen
  Tests, Motorkontrollleuchte, und jeder Monitor als abgeschlossen, nicht
  abgeschlossen oder nicht unterstützt.
- **Freeze Frame**: auslösender Code und die Messwerte beim Speichern des Codes
  (Motorlast, Kühlmitteltemperatur, Drehzahl, Geschwindigkeit).
- **Fahrzeug**: FIN, Hersteller, Land, Modelljahr, Prüfziffer; auf Wunsch Angaben aus
  NHTSA vPIC (Modell, Motor …).
- **Live-Daten**: Werte auswählen (die vom Fahrzeug unterstützten erscheinen nach dem
  ersten Start), Intervall 0,5/1/2 s, „Aufzeichnen (CSV)“, dann Start. Je Wert eine
  Kachel mit aktuellem Wert, kleinster/größter gesehener Wert und einer Kurve der
  letzten 120 Werte; die Kurve skaliert nach den gezeigten Werten. Oben Bordspannung,
  Laufzeit und Runden. Solange Live-Daten laufen, sind Scan, Löschen und Export
  gesperrt (am Adapter läuft immer nur eine Aktion); umgekehrt startet Live nicht
  während einer Diagnose.

Antwortet das Steuergerät auf einen Teil nicht, zeigt der Reiter „Nicht verfügbar“.
Unten: Adapter, Protokoll, Bordspannung (rot bei niedriger Spannung).

„FIN online nachschlagen (NHTSA)“ (Menü *Optionen* oder Reiter *Fahrzeug*) ist
standardmäßig aus. Eingeschaltet geht beim nächsten Scan nur die FIN an die
US-Behörde NHTSA; die Einstellung wird je Nutzer gespeichert
(`~/.config/obd-diag/obd-diag.conf`).

Menü *Datei* und Schaltflächen unten:

- **Sitzung speichern** (Strg+S) legt die Diagnose als JSON ab (siehe oben) und zeigt
  den Pfad.
- **Sitzung öffnen …** (Strg+O) zeigt eine gespeicherte Sitzung nur zum Ansehen;
  Löschen geht dann nicht („nur bei verbundenem Fahrzeug“).
- **Bericht als PDF …** (Strg+P) und **CSV exportieren …** fragen nach dem Ziel
  (Vorschlag `obd-bericht-JJJJMMTT-HHMM.pdf` bzw. `.csv` im Ordner Dokumente) und
  schreiben im Hintergrund.

Die Dateidialoge kommen vom Desktop (xdg-desktop-portal oder GTK); fehlt beides,
nimmt Qt einen eigenen Dialog.

„Fehlercodes löschen …“ ist derzeit gesperrt (grau, der Tooltip nennt den Grund).
Nach der Freigabe fragt es vorher nach (Zündung an, Motor aus; Codes und Freeze
Frame werden gesichert; die Readiness für die Abgasuntersuchung wird zurückgesetzt),
zeigt danach den Pfad der Sicherung und liest die Diagnose neu ein – Readiness und
Freeze Frame zeigen also den Stand nach dem Löschen.

Gegen den Emulator:

```sh
uv run python tools/emulator.py   # zeigt das pty an, z. B. /dev/pts/5
uv run obd-diag-gui               # /dev/pts/5 ins Port-Feld eintragen, verbinden
```

Jede Aktion öffnet den Port, arbeitet in einem Hintergrund-Thread und schließt ihn
wieder; die Oberfläche bleibt dabei bedienbar.

### Mitschnitt (`--trace`)

Alle Befehle mit Adapter (`info`, `scan`, `diagnose`, `vin`, `clear`, `live`) schneiden mit
`--trace` jede gesendete und empfangene Zeile mit Zeitstempel mit:

```sh
uv run obd-diag diagnose --port /dev/ttyUSB0 --trace            # ~/.local/share/obd-diag/traces/
uv run obd-diag diagnose --port /dev/ttyUSB0 --trace auto.log   # eigene Datei
```

```
# obd-diag 0.0.1 Mitschnitt 2026-10-07T16:09:33+02:00 /dev/ttyUSB0 38400 Baud
    0.000 >> ATZ\r
    0.508 << ATZ\r\r\rELM327 v1.5\r\r>
    3.013 >> 03\r
    3.016 << 00A\r0: 430404200133\r1: 0300C100\r\r>
```

Steuerzeichen stehen als `\r`, `\xNN`; `>>` ist gesendet, `<<` empfangen, `!!` ein
Fehler. In der Oberfläche schaltet „Optionen → Adapter-Mitschnitt aufzeichnen“ das
für jede Aktion ein (eine Datei je Aktion). Der Mitschnitt enthält ggf. die FIN und
bleibt lokal. `ReplayTransport` in `transport/trace.py` spielt ihn ohne Adapter wieder
ab, z. B. als Test-Fixture.

### Protokoll-Details

Der Adapter läuft ohne Header (`ATH0`), ohne Leerzeichen und ohne Echo. Abweichungen
echter Adapter und Steuergeräte fängt das Tool so ab:

- **Vermischte mehrteilige Antworten:** Senden zwei Steuergeräte gleichzeitig
  mehrteilige CAN-Nachrichten, mischt der ELM327 ohne Header deren Frames (Datenblatt
  ELM327DS S. 45). Erkennt das Zerlegen das (Lücken in der Frame-Nummerierung,
  unvollständige Nachricht), wird die lesende Anfrage (Mode 03/07/0A, FIN `0902`) einmal
  mit `ATH1` wiederholt, die Frames werden je CAN-ID nach ISO 15765-2 zusammengesetzt
  (11 Bit `7E8 …`, 29 Bit `18 DA F1 10 …`), danach wieder `ATH0`
  (`protocol/headers.py`). Die Codes stehen dann nach Steuergeräte-Adresse geordnet
  (7E8 vor 7E9), sonst in Eingangsreihenfolge. Ältere Protokolle mit Header
  (`48 6B 10 … <Prüfbyte>`) werden ebenfalls zerlegt; das Prüfbyte wird entfernt, aber
  nicht geprüft. Dort tritt das Mischen nicht auf (eine Zeile je Nachricht).
- **Freeze Frame:** angefragt nach SAE J1979 mit Frame-Nummer (`020C00`). Antwortet
  das Fahrzeug auf `020200` mit `NO DATA` oder `7F 02 12`, wird `0202` ohne
  Frame-Nummer versucht (wie python-OBD) und bei Erfolg der ganze Freeze Frame so
  gelesen. Die Schlüssel in `raw` (Sicherung, Sitzung) zeigen das benutzte Format.
- **Löschen mit `7F 04 78`** (Steuergerät meldet „Antwort folgt“): es wird ohne
  erneutes Senden bis zu 10 s auf `44` oder eine Ablehnung gewartet. Kommt nichts,
  gilt das Löschen als nicht bestätigt (Hinweis auf `obd-diag scan`, Sicherung bleibt).
  Mode 04 wird nie wiederholt.
- **Protokollnummer unklar** (`ATDPN` meldet `0`, `?` o. Ä.): `ATDPN` wird erneut
  gefragt; bleibt sie unklar, wird `0100` einmal mit Headern gesendet und an deren
  Form erkannt, ob CAN 11 Bit, CAN 29 Bit oder ein älteres Protokoll vorliegt. Die
  Protokollangabe trägt dann den Zusatz „laut Headern …“.
- **Echo:** eine erste Zeile, die dem Befehl ohne Rücksicht auf Groß-/Kleinschreibung
  und Leerzeichen gleicht (`at dpn` für `ATDPN`), wird entfernt.

## Struktur

```
src/obd_diag/
├── transport/   # Byte-Kanal zum Adapter (Protocol, USB-Seriell, Adaptersuche)
├── protocol/    # ELM327-Befehle mit Freigabeliste, OBD-II-Dekodierung, PID-Tabelle
├── services/    # Abläufe: Scan, Löschen, Sitzung speichern/laden, Live-Daten
├── data/        # DTC-Katalog (SQLite), WMI-Tabelle für die FIN
├── export/      # PDF-Bericht und CSV einer Sitzung
├── ui/          # Desktop-Oberfläche: View-Models (Python) und QML
└── cli.py
```
