<div align="center">

# obd-diag

**Sehen, was hinter der Motorkontrollleuchte steckt, unter Linux.**

Einen günstigen ELM327-Adapter in die OBD-II-Buchse stecken, und obd-diag liest die
Fehlercodes, erklärt sie verständlich und zeigt, was der Motor gerade tut.
Desktop-Programm und Kommandozeile, auf Deutsch und Englisch, ohne Konto und ohne Cloud.

[![CI](https://github.com/phprell/obd-diag/actions/workflows/ci.yml/badge.svg)](https://github.com/phprell/obd-diag/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/obd-diag)](https://pypi.org/project/obd-diag/)
[![Python](https://img.shields.io/pypi/pyversions/obd-diag)](https://pypi.org/project/obd-diag/)
[![Lizenz: MIT](https://img.shields.io/badge/Lizenz-MIT-blue)](https://github.com/phprell/obd-diag/blob/main/LICENSE)
[![Doku](https://img.shields.io/badge/Doku-phprell.github.io-informational)](https://phprell.github.io/obd-diag/)

[English](https://github.com/phprell/obd-diag/blob/main/README.md) · **Deutsch**

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/phprell/obd-diag/main/.github/assets/readme/used-car-dark-de.png">
  <img src="https://raw.githubusercontent.com/phprell/obd-diag/main/.github/assets/readme/used-car-light-de.png" alt="obd-diag-Oberfläche: Gebrauchtwagen-Check eines Mercedes A 180 d, Urteil unauffällig" width="820">
</picture>

</div>

## Warum obd-diag

- **Nur lesend, mit Absicht.** obd-diag fragt nur, es verändert nichts am Auto. Jeder
  Befehl muss durch eine Freigabeliste nach ELM327-Datenblatt und SAE J1979; alles
  andere wird abgewiesen, bevor es den Adapter erreicht. Kein Codieren, kein Flashen,
  keine Servicefunktionen.
- **Antworten statt Hex.** Fehlercodes kommen mit Klartext aus einem Offline-Katalog und
  einer groben Dringlichkeit (von „Beobachten“ bis „Möglichst nicht weiterfahren“).
- **Gebrauchtwagen in Sicht?** Der Gebrauchtwagen-Check zeigt, ob jemand die Fehlercodes
  kurz vor der Besichtigung gelöscht hat, anhand der Zähler, die das Löschen zurücksetzt.
- **Am echten Auto geprüft** (Mercedes A 180 d) und ohne Hardware gegen das Datenblatt,
  Adapter-Mitschnitte anderer Programme und python-OBD, mit rund 2000 automatischen Tests.

## Was es zeigt

| | |
| --- | --- |
| **Fehlercodes** | gespeichert, ausstehend und permanent, mit Klartext und Dringlichkeit |
| **Readiness** | welche Eigentests gelaufen sind und welche Fahrt die fehlenden abschließt |
| **Gebrauchtwagen-Check** | vor kurzem gelöscht? Warmläufe und Strecke seitdem |
| **Live-Daten** | 140 Werte (Geschwindigkeit, Temperaturen, Ladedruck, Raildruck, AdBlue …) als Kacheln mit Verlauf, CSV-Aufzeichnung, Warnschwellen |
| **Freeze Frame** | Motorzustand in dem Moment, als ein Code gesetzt wurde |
| **Fahrzeug** | FIN offline dekodiert, Steuergeräte, Kalibrierungs-IDs, Testergebnisse |
| **Batterietest** | Ruhespannung, Einbruch beim Anlassen, Ladespannung |
| **Berichte** | PDF-Bericht, CSV, gespeicherte Sitzungen zum späteren Vergleich |

<table>
  <tr>
    <td width="50%">
      <picture>
        <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/phprell/obd-diag/main/.github/assets/readme/live-data-dark-de.png">
        <img src="https://raw.githubusercontent.com/phprell/obd-diag/main/.github/assets/readme/live-data-light-de.png" alt="Live-Daten als Kacheln mit Verlauf">
      </picture>
    </td>
    <td width="50%">
      <picture>
        <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/phprell/obd-diag/main/.github/assets/readme/readiness-dark-de.png">
        <img src="https://raw.githubusercontent.com/phprell/obd-diag/main/.github/assets/readme/readiness-light-de.png" alt="Readiness-Monitore, ein Test nicht abgeschlossen">
      </picture>
    </td>
  </tr>
</table>

## Ohne Auto ausprobieren

Der Demo-Modus spielt einen aufgezeichneten Mercedes A 180 d ab, ganz ohne Adapter:

```sh
pipx install 'obd-diag[gui]'
obd-diag-gui --demo
```

## Installieren

```sh
pipx install 'obd-diag[gui]'              # jede Distribution (PyPI); ohne [gui] nur Kommandozeile
yay -S obd-diag                           # Arch Linux (AUR)
flatpak install --user obd-diag.flatpak   # Paket aus dem GitHub-Release
```

Braucht Linux und Python 3.12 oder neuer. Einzelheiten, udev-Regel und Rechte für den
seriellen Port: [Installation](https://phprell.github.io/obd-diag/benutzen/installation.html).

## Am Auto

Nötig sind ein ELM327-kompatibler Adapter (USB oder Bluetooth seriell) und ein Auto mit
OBD-II (in der EU Benziner ab 2001, Diesel ab 2004; in den USA ab 1996).

```sh
obd-diag setup-check                      # findet den Adapter, prüft Rechte und Baudrate
obd-diag diagnose --port /dev/ttyUSB0     # Zündung an, Motor aus
obd-diag live --port /dev/ttyUSB0         # Live-Daten, Strg+C beendet
obd-diag-gui                              # oder alles in der Oberfläche
```

Vor der ersten Fahrt mit Laptop auf dem Beifahrersitz bitte
[Erster Test am Auto](https://phprell.github.io/obd-diag/benutzen/erster-test.html) lesen.

## Was es nicht kann

obd-diag spricht mit der genormten Abgasdiagnose (OBD-II). Airbag, ABS und
Komfortsteuergeräte erreicht es nicht, und es codiert, flasht und setzt keine
Serviceintervalle zurück. Fehlercodes löschen ist eingebaut, mit Prüfungen und
Sicherung, bleibt aber vorerst gesperrt. Als Nächstes geplant: Bluetooth-LE-Adapter und
der Fehlerspeicher aller Steuergeräte über UDS, weiterhin nur lesend.

## Dokumentation

Alles Weitere steht auf der Website, auf [Deutsch](https://phprell.github.io/obd-diag/)
und [Englisch](https://phprell.github.io/obd-diag/en/): jeder Befehl und jede Option, die
Oberfläche, häufige Probleme, wie das Programm mit dem Adapter spricht (mit einem echten
Mitschnitt Zeile für Zeile erklärt) und das Sicherheitskonzept.

## Mitmachen

Fehlerberichte und Mitschnitte von anderen Autos sind sehr willkommen, besonders von
anderen Marken als Mercedes. `--trace` schneidet die Kommunikation mit dem Adapter mit;
darin steht die FIN, also bitte vor dem Teilen entfernen.

```sh
uv sync && uv run python tools/build_dtc_db.py
uv run pytest
```

Regeln, Tests und Übersetzungen: [Mitmachen und Regeln](https://phprell.github.io/obd-diag/entwickeln/regeln.html),
Änderungen: [CHANGELOG.md](https://github.com/phprell/obd-diag/blob/main/CHANGELOG.md).

## Lizenz

[MIT](https://github.com/phprell/obd-diag/blob/main/LICENSE). Fehlercode-Texte aus
[OBDex](https://github.com/foerbsnavi/OBDex).
