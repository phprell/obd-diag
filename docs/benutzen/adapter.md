# Adapter und Anschluss

obd-diag spricht mit Adaptern, die sich wie ein ELM327 verhalten: über USB als
serielle Schnittstelle oder über klassisches Bluetooth (`/dev/rfcomm*`). Bluetooth LE
kommt erst mit v0.3 ({doc}`../entwickeln/roadmap`).

## Welcher Adapter

- Adapter mit echtem ELM327- oder STN-Chip (z. B. OBDLink) sind verlässlich.
- Billige „v1.5“-Klone melden sich oft als ELM327, beherrschen aber nicht alle
  Befehle oder liefern Zeichenmüll. obd-diag fängt einiges davon ab
  ({doc}`../technik/antwortformate`), garantiert aber nichts.
- **FORScan ELMconfig USB** (der Adapter für den ersten Test): hat einen Umschalter
  MS-CAN/HS-CAN. Für die genormte Diagnose muss er auf **HS-CAN** stehen; MS-CAN ist
  ein Ford-spezifischer Komfortbus. Der USB-Chip ist meist ein CH340.

## Baudrate

Die Baudrate gilt zwischen Rechner und Adapter, nicht zum Fahrzeug. Standard ist
38400 Baud (`--baud`); viele USB-Adapter laufen auch oder nur mit 115200. Antwortet
`obd-diag info` nicht, ist die andere Baudrate der erste Versuch.

## Adapter finden

```sh
obd-diag ports
```

listet USB-Seriell-Adapter (`/dev/ttyUSB*`, `/dev/ttyACM*` und andere Geräte mit
USB-Kennung) und gebundene Bluetooth-Geräte (`/dev/rfcomm*`). Eingebaute
Schnittstellen (`/dev/ttyS*`) erscheinen nicht. Ein Bluetooth-Adapter wird vorher
gebunden:

```sh
sudo rfcomm bind 0 <MAC-Adresse>    # danach /dev/rfcomm0
```

## Verbindung prüfen

```sh
obd-diag info --port /dev/ttyUSB0
```

```
Adapter:      ELM327 v1.5
Bordspannung: 12.4 V
```

`info` setzt den Adapter zurück und liest dessen Kennung und die Spannung an Pin 16 der
OBD-Buchse. Das Fahrzeug wird dabei noch nicht angesprochen.
