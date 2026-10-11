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

## Adapter prüfen

```sh
obd-diag adapter --port /dev/ttyUSB0
```

```
Adapter:        ELM327 v1.5
Beschreibung:   OBDII to RS232 Interpreter
Version:        v1.5: steht nicht in der Versionsliste von Elm Electronics, der Adapter ist ein Nachbau
Urteil:         Nachbau, Befehlssatz wie v1.4
Bordspannung:   12,4 V
Parameter:      alle auf Werkseinstellung
Für UDS (v0.3): voraussichtlich ja: Befehlssatz ab v1.4, die nötigen Befehle ATSH (ab v1.0) und ATCRA (ab v1.3) sind damit enthalten

Geprüfte Befehle:
  ATI:   ja (seit v1.0: Kennung)
  AT@1:  ja (seit v1.0: Gerätebeschreibung)
  ATRV:  ja (seit v1.0: Spannung)
  ATPPS: ja (seit v1.1: Übersicht der Programmable Parameters)
  ATIGN: ja (seit v1.4: Zündungseingang)
  ATRD:  ja (seit v1.4: Nutzerspeicher lesen)
  ATAMC: nein (seit v2.0: Aktivitätszähler)
```

(Beispiel mit erfundenen Antworten; welche Befehle der FORScan-ELMconfig-Adapter
wirklich kennt, zeigt erst der Check an ihm.)

Der Adapter-Check zeigt, was der Adapter wirklich kann. Viele Adapter melden sich als
„ELM327 v1.5“ oder „v2.1“, sind aber Nachbauten mit unvollständigem Befehlssatz. Der
Check fragt Kennung (`ATI`), Beschreibung (`AT@1`) und Spannung (`ATRV`) ab und probiert
Befehle, die laut Versionsliste des Datenblatts erst mit einer bestimmten Firmware
hinzukamen (`ATPPS` v1.1, `ATIGN` und `ATRD` v1.4, `ATAMC` v2.0). Daraus folgen:

- **Version**: Eine v1.5 hat Elm Electronics nie herausgebracht (die Versionsliste
  springt von v1.4b auf v2.0); ein Adapter, der sie meldet, ist ein Nachbau.
- **Urteil**: welcher Version der Befehlssatz tatsächlich entspricht und ob Befehle
  fehlen, die die gemeldete Version haben müsste („Nachbau, …; es fehlt ATAMC“).
- **Bordspannung**: unter 5 V steckt der Adapter vermutlich nicht im Auto (er misst die
  Spannung an Pin 16 der OBD-Buchse), unter 11,8 V ist die Batterie schwach.
- **Parameter**: ob jemand die Voreinstellungen des Adapters (Programmable Parameters)
  geändert hat; obd-diag ändert sie nie.
- **Für UDS (v0.3)**: ob der Adapter voraussichtlich für die geplante
  Steuergerätesuche taugt. Sie braucht `ATSH` und `ATCRA`; die sendet der Check selbst
  nicht, weil sie die Adressierung verstellen. Das Urteil ist deshalb ein Schluss aus
  dem übrigen Befehlssatz, sicher zeigt es erst die Suche selbst.

Der Check sendet nur Adapter-Befehle, das Fahrzeug wird nicht angesprochen; keiner der
Befehle verändert den Adapter dauerhaft ({doc}`../technik/befehle`). `--json` gibt das
Ergebnis maschinenlesbar aus. In der Oberfläche steht er unter *Optionen → Adapter
prüfen …*:

```{image} ../_static/screenshots/adapter-check-light.png
:alt: Dialog Adapter-Check mit Version, Urteil, Spannung, UDS-Eignung und den geprüften Befehlen
```
