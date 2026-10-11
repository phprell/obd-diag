# Installation

obd-diag braucht Linux und Python 3.12 oder neuer. Empfohlen ist
[uv](https://docs.astral.sh/uv/), das Umgebung und Abhängigkeiten aus `uv.lock`
einrichtet.

```sh
git clone https://github.com/phprell/obd-diag.git
cd obd-diag
uv sync                                  # Umgebung inkl. Oberfläche und Dev-Werkzeuge
uv run python tools/build_dtc_db.py      # Fehlercode-Katalog bauen (lädt ~9 s von GitHub)
uv run obd-diag --version
```

Ohne uv:

```sh
python -m venv .venv
.venv/bin/pip install -e '.[gui]'
.venv/bin/python tools/build_dtc_db.py   # braucht zusätzlich pyyaml
```

Das Extra `gui` bringt PySide6 für die Oberfläche mit. Ohne es bleibt eine reine
Kommandozeilen-Installation klein, etwa auf einem Raspberry Pi.

## Fehlercode-Katalog

Die Klartexte zu den Fehlercodes (Deutsch und Englisch, Ursachen, Symptome,
Kostenrahmen) liegen offline in `src/obd_diag/data/dtc_catalog.sqlite`. Die Datei wird
nicht eingecheckt, sondern gebaut:

```sh
uv run python tools/build_dtc_db.py                     # lädt die Daten von GitHub
uv run python tools/build_dtc_db.py --source ../OBDex   # oder aus einem lokalen Klon
```

Datenquelle ist [OBDex](https://github.com/foerbsnavi/OBDex) (Daten unter
[CC0-1.0](https://creativecommons.org/publicdomain/zero/1.0/)), fest auf einen Commit
gepinnt (`OBDEX_COMMIT` im Skript). Fehlt der Katalog, zeigt obd-diag die Codes ohne
Beschreibung.

## Rechte für den seriellen Port

USB-Adapter erscheinen als `/dev/ttyUSB0` oder `/dev/ttyACM0` und gehören der Gruppe
`uucp` (Arch) bzw. `dialout` (Debian, Ubuntu). Entweder den Nutzer in die Gruppe
aufnehmen und neu anmelden:

```sh
sudo usermod -aG uucp $USER      # Debian/Ubuntu: dialout
```

oder die udev-Regel aus `packaging/` installieren. Sie gibt dem angemeldeten Nutzer
Zugriff auf die gängigen USB-Seriell-Chips (FTDI, CH340/CH341, CP210x):

```sh
sudo cp packaging/99-obd-diag.rules /etc/udev/rules.d/
sudo udevadm control --reload
```

## Einrichtung prüfen

```sh
obd-diag setup-check                     # alle gefundenen Adapter
obd-diag setup-check --port /dev/rfcomm0 # nur diesen Port
```

prüft der Reihe nach, ob die Oberfläche (PySide6) und der Fehlercode-Katalog
installiert sind, welche Adapter angeschlossen sind, ob der Nutzer sie öffnen darf
und mit welcher Baudrate sie antworten (erst 38400, dann 115200). Fehlt der Zugriff,
nennt die Prüfung die Gruppe des Geräts und den Befehl zum Eintragen; ist der Nutzer
schon eingetragen, aber noch nicht neu angemeldet, sagt sie das. Findet sie keinen
Adapter und ist `brltty` installiert, weist sie darauf hin: das Programm für
Braillezeilen belegt unter Ubuntu oft Adapter mit CH340-Chip.

```
  OK       Oberfläche (PySide6) installiert
  OK       Fehlercode-Katalog vorhanden
  OK       /dev/ttyUSB0 (USB Serial (QinHeng)): Zugriff erlaubt
  OK       /dev/ttyUSB0: ELM327 v1.5 antwortet mit 38400 Baud
  Hinweis  Vor dem Verbinden am Auto: Zündung einschalten (der Motor darf aus bleiben). Bei Adaptern mit MS-/HS-CAN-Schalter HS-CAN wählen.

Ergebnis: bereit. Weiter mit: obd-diag diagnose --port /dev/ttyUSB0 --baud 38400
```

Gesendet werden nur die Adapter-Befehle, mit denen jeder Befehl beginnt (`ATZ`,
`ATE0`, `ATL0`, `ATS0`, `ATH0`, `ATSP0`); nichts davon erreicht das Fahrzeug, die Prüfung
geht also auch ohne Auto. Der Exit-Code ist 0, wenn ein Adapter antwortet.

Die Oberfläche öffnet dieselbe Prüfung beim ersten Start von selbst und danach unter
*Optionen → Einrichtung prüfen …*; „Übernehmen“ trägt Port und Baudrate oben ein.
