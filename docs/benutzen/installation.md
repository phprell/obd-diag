# Installation

obd-diag braucht Linux. Ab Version 0.2.1 gibt es fertige Pakete; der Fehlercode-Katalog
ist in allen schon enthalten.

| Weg | Für wen | Oberfläche |
|---|---|---|
| pipx (PyPI) | jede Distribution, auch Raspberry Pi | mit Extra `gui` |
| AUR | Arch Linux und Ableger | immer, mit Startmenü-Eintrag und udev-Regel |
| Flatpak | Desktop, unabhängig von der Distribution | immer, mit Startmenü-Eintrag |
| Quellen | Entwicklung | mit `uv sync` |

## pipx

[pipx](https://pipx.pypa.io/) installiert Programme aus PyPI in eine eigene Umgebung
(Python 3.12 oder neuer):

```sh
pipx install 'obd-diag[gui]'     # mit Oberfläche
pipx install obd-diag            # nur Kommandozeile, etwa auf einem Raspberry Pi
obd-diag --version
obd-diag-gui --install-desktop   # Eintrag im Startmenü (entfernen: --remove-desktop)
```

`--install-desktop` legt Desktop-Datei und Symbol unter `~/.local/share` ab. Aktualisieren
mit `pipx upgrade obd-diag`. Ohne PyPI geht es auch mit dem Wheel aus einem
[GitHub-Release](https://github.com/phprell/obd-diag/releases):
`pipx install 'obd-diag[gui] @ <Adresse der .whl-Datei>'`.

## AUR

Das Paket `obd-diag` nutzt PySide6, pyserial und ReportLab aus Arch und installiert
Startmenü-Eintrag, Symbol und die udev-Regel (siehe unten) gleich mit:

```sh
yay -S obd-diag                  # oder ein anderer AUR-Helfer
```

Ohne Helfer: `PKGBUILD` aus dem GitHub-Release (oder dem AUR) in einen leeren Ordner legen
und dort `makepkg -si` aufrufen. Die Vorlage liegt in `packaging/aur/`.

## Flatpak

Jedes Release enthält `obd-diag.flatpak` (x86_64) mit der Oberfläche. Laufzeit und
PySide6 kommen von Flathub:

```sh
flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak install --user obd-diag.flatpak
flatpak run io.github.phprell.obd_diag
flatpak run --command=obd-diag io.github.phprell.obd_diag --version   # Kommandozeile
```

Das Flatpak darf auf alle Geräte zugreifen (`--device=all`), weil Flatpak serielle
Schnittstellen nicht einzeln freigeben kann, und ins Netz nur für die abschaltbaren
Online-Abfragen. Die Rechte für den Port regelt trotzdem das System (siehe unten). Das
Manifest liegt in `packaging/flatpak/`.

## Aus den Quellen

Empfohlen ist [uv](https://docs.astral.sh/uv/), das Umgebung und Abhängigkeiten aus
`uv.lock` einrichtet.

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
Kostenrahmen) liegen offline in `src/obd_diag/data/dtc_catalog.sqlite`. Die Pakete
enthalten die Datei; im Quellcode wird sie nicht eingecheckt, sondern gebaut:

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

oder die udev-Regel installieren (das AUR-Paket bringt sie mit). Sie gibt dem gerade
angemeldeten Nutzer Zugriff auf die gängigen USB-Seriell-Chips (FTDI, CH340/CH341,
CP210x), ohne Gruppe und Dateirechte zu ändern. Sie liegt im Quellcode unter
`src/obd_diag/share/`:

```sh
curl -LO https://raw.githubusercontent.com/phprell/obd-diag/main/src/obd_diag/share/70-obd-diag.rules
sudo install -m 644 70-obd-diag.rules /etc/udev/rules.d/
sudo udevadm control --reload && sudo udevadm trigger
```

Danach den Adapter einmal ab- und wieder anstecken.

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
