# Oberfläche

```sh
uv run obd-diag-gui
```

Oben Port und Baudrate wählen (die Liste zeigt gefundene Adapter, ein Pfad lässt sich
auch eintippen) und „Verbinden & Scannen“ drücken. Das liest in einem Durchgang
Fehlercodes, Readiness, Freeze Frame und FIN, nur lesend. Rechts oben steht dann das
Fahrzeug (Hersteller und FIN), darunter fünf Reiter. Unten stehen Adapter, Protokoll
und Bordspannung (rot bei niedriger Spannung).

Die Bilder auf dieser Seite stammen aus dem Emulator (`tools/docs_screenshots.py`) und
folgen dem hellen oder dunklen Design dieser Website.

## Fehlercodes

```{image} ../_static/screenshots/fehlercodes-light.png
:alt: Reiter Fehlercodes: links die Codes nach Art, rechts die Erklärung
:class: only-light
```

```{image} ../_static/screenshots/fehlercodes-dark.png
:alt: Reiter Fehlercodes: links die Codes nach Art, rechts die Erklärung
:class: only-dark
```


Links die Codes, gruppiert nach gespeichert, ausstehend und permanent; rechts die
Erklärung des gewählten Codes mit Ursachen und ihrer Wahrscheinlichkeit, Symptomen und
Kostenrahmen aus dem Offline-Katalog ({doc}`fehlercodes`).

## Readiness

```{image} ../_static/screenshots/readiness-light.png
:alt: Reiter Readiness: Gesamturteil und Monitore
:class: only-light
```

```{image} ../_static/screenshots/readiness-dark.png
:alt: Reiter Readiness: Gesamturteil und Monitore
:class: only-dark
```


„Alle Tests abgeschlossen“ (grün) oder „Nicht alle Tests abgeschlossen“ (gelb) mit den
offenen Tests, Motorkontrollleuchte, Motorart und jeder Monitor als abgeschlossen,
nicht abgeschlossen oder nicht unterstützt. Das ist bewusst keine AU-Bewertung
({doc}`sitzungen`).

## Freeze Frame

```{image} ../_static/screenshots/freeze-frame-light.png
:alt: Reiter Freeze Frame: Messwerte beim Speichern des Codes
:class: only-light
```

```{image} ../_static/screenshots/freeze-frame-dark.png
:alt: Reiter Freeze Frame: Messwerte beim Speichern des Codes
:class: only-dark
```


Der auslösende Code und die Messwerte beim Speichern des Codes: Motorlast,
Kühlmitteltemperatur, Drehzahl, Geschwindigkeit.

## Fahrzeug

```{image} ../_static/screenshots/fahrzeug-light.png
:alt: Reiter Fahrzeug: FIN und Dekodierung
:class: only-light
```

```{image} ../_static/screenshots/fahrzeug-dark.png
:alt: Reiter Fahrzeug: FIN und Dekodierung
:class: only-dark
```


FIN, Hersteller, Land, Modelljahr und Prüfziffer ({doc}`../technik/fin`). „FIN online
nachschlagen (NHTSA)“ (Menü *Optionen* oder hier) ist standardmäßig aus; eingeschaltet
geht beim nächsten Scan nur die FIN an die US-Behörde NHTSA.

## Live-Daten

```{image} ../_static/screenshots/live-daten-light.png
:alt: Reiter Live-Daten: Kacheln mit Wert und Verlaufskurve
:class: only-light
```

```{image} ../_static/screenshots/live-daten-dark.png
:alt: Reiter Live-Daten: Kacheln mit Wert und Verlaufskurve
:class: only-dark
```


Werte auswählen (die vom Fahrzeug unterstützten erscheinen nach dem ersten Start),
Intervall 0,5, 1 oder 2 s, auf Wunsch „Aufzeichnen (CSV)“, dann Start. Je Wert eine
Kachel mit aktuellem Wert, kleinstem und größtem gesehenen Wert und einer Kurve der
letzten 120 Werte. Solange Live-Daten laufen, sind Scan, Löschen und Export gesperrt,
denn am Adapter läuft immer nur eine Aktion ({doc}`live-daten`).

## Menüs und Schaltflächen

| Aktion | Kürzel | Was passiert |
| --- | --- | --- |
| Sitzung speichern | Strg+S | Diagnose als JSON ablegen, Pfad wird angezeigt |
| Sitzung öffnen … | Strg+O | gespeicherte Sitzung nur ansehen; Löschen geht dann nicht |
| Bericht als PDF … | Strg+P | PDF-Bericht im Hintergrund schreiben |
| CSV exportieren … | – | eine Zeile pro Fehlercode |
| Optionen → Adapter-Mitschnitt aufzeichnen | – | jede Aktion schreibt einen Mitschnitt ({doc}`sitzungen`) |
| Optionen → Design | – | wie das System, hell oder dunkel |
| Fehlercodes löschen … | – | derzeit gesperrt (grau, der Tooltip nennt den Grund) |

Einstellungen werden je Nutzer unter `~/.config/obd-diag/obd-diag.conf` gespeichert.
Jede Aktion öffnet den Port, arbeitet in einem Hintergrund-Thread und schließt ihn
wieder; die Oberfläche bleibt dabei bedienbar.

## Gegen den Emulator ausprobieren

```sh
uv run python tools/emulator.py   # zeigt das pty an, z. B. /dev/pts/5
uv run obd-diag-gui               # /dev/pts/5 ins Port-Feld eintragen, verbinden
```
