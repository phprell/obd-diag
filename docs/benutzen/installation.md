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
