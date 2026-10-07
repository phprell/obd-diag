# obd-diag

OBD-II-Diagnose für Linux über ELM327-kompatible Adapter: Fehlercodes lesen und erklären,
Freeze Frame, Readiness, FIN – später Live-Daten und eine QML-Oberfläche.

Design und Roadmap: [OBD-Diagnose – Designvorschlag](https://claude.ai/code/artifact/de949a8a-4f58-41b4-a629-6b9d238bdac7)

## Stand

Roadmap-Schritt 1 (Grundgerüst): Transport-Schicht für USB-Seriell, minimaler
ELM327-Treiber, DTC-Dekodierung, Tests gegen Fake und Emulator, CI.

## Entwicklung

```sh
uv sync                     # Umgebung inkl. Dev-Werkzeuge
uv run pytest               # Unit- und Emulator-Tests
uv run ruff check . && uv run ruff format --check .
uv run mypy
```

Ohne uv: `python -m venv .venv && .venv/bin/pip install -e . pytest ruff mypy types-pyserial ELM327-emulator`.

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

## Struktur

```
src/obd_diag/
├── transport/   # Byte-Kanal zum Adapter (Protocol + USB-Seriell)
├── protocol/    # ELM327-Befehle, OBD-II-Dekodierung
├── services/    # Diagnose-Abläufe (folgt)
├── data/        # DTC-Katalog, FIN (folgt)
├── ui/          # QML (folgt)
└── cli.py
```
