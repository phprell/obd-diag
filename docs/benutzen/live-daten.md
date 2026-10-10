# Live-Daten

Live-Daten fragen Runde für Runde aktuelle Messwerte ab (Mode 01) und zeigen sie an,
auf Wunsch mit Aufzeichnung als CSV.

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

:::{warning}
Während der Fahrt nur durch Beifahrer bedienen.
:::

## Welche Werte

116 Werte aus 82 Mode-01-PIDs nach SAE J1979, darunter Drehzahl, Geschwindigkeit,
Temperaturen, Last, Luftmasse, Saugrohr- und Raildruck, AGR, Kraftstofftrimm,
Lambdasonden 1–8, Drehmoment, Tankfüllstand, Verbrauch und Kilometerstand. Die
vollständige Liste mit Schlüssel, Formel und Wertebereich steht unter
{doc}`../technik/pids`.

- Angefragt werden nur Werte, die das Fahrzeug als unterstützt meldet (`0100`, `0120`
  … über alle Steuergeräte). `--list` zeigt sie mit Schlüssel.
- Ohne `--pids`: Drehzahl, Geschwindigkeit, Kühlmitteltemperatur, Last,
  Ansauglufttemperatur und Steuergerätespannung, soweit unterstützt.
- Liefert eine PID mehrere Werte (Sondenspannung und Trimm), wird sie je Runde nur
  einmal abgefragt.
- Die Sonden heißen 1–8 in PID-Reihenfolge. Welche Bank und Position das ist, legt das
  Fahrzeug fest (PID `13` oder `1D`).
- Nicht enthalten sind PIDs mit Statusbyte, deren Aufbau in freien Quellen nicht
  eindeutig ist, darunter Ladedruck (`70`) und Partikelfilter (`7A`–`7C`).

## Ablauf einer Runde

- Jeder gewählte Wert wird einmal gelesen. Ist einer nicht lesbar, steht „-“ (in der
  CSV eine leere Zelle), die Abfrage läuft weiter.
- Die Bordspannung (`ATRV`) wird jede zehnte Runde gelesen. Zeigt der Adapter weniger
  als 11,8 V, wird mit der Steuergerätespannung (PID 42) gegengeprüft: viele Adapter
  messen hinter einer Schutzdiode einige Zehntel Volt zu wenig. Liegt auch sie darunter
  (oder fehlt sie), wird nur noch alle 5 s abgefragt, um die Batterie zu schonen. Solange
  gedrosselt ist, wird die Spannung jede Runde gemessen; reicht sie wieder (Motor
  angesprungen), geht es sofort im normalen Takt weiter.
- Meldet der Adapter drei Runden lang bei jedem Wert einen Busfehler (z. B. Zündung
  aus), endet die Abfrage mit einer Fehlermeldung. `NO DATA`, `?` und Ablehnungen
  zählen dabei nicht als Busfehler.
- Gesendet werden nur `01xx` und `ATRV`.

## Aufzeichnung

`--record` schreibt eine CSV unter `~/.local/share/obd-diag/recordings/`
(`live-JJJJMMTT-HHMMSS.csv`) oder in die angegebene Datei, die nicht überschrieben
wird. Format wie beim Export: UTF-8 mit BOM, `;` als Trennzeichen, Dezimalkomma;
erste Spalte `Zeit (s)`, dann je Wert `Name (Einheit)`, zuletzt `Bordspannung (V)`.
Jede Zeile wird sofort geschrieben, ein Abbruch verliert also nichts.
