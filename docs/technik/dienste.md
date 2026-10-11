# OBD-II-Dienste

OBD-II nach SAE J1979 (ISO 15031-5) kennt „Services“ (Modes). obd-diag nutzt nur die
lesenden und den einen schreibenden Dienst Mode 04. Eine positive Antwort beginnt mit
dem Mode plus `40` (`01` → `41`), eine Ablehnung mit `7F` ({doc}`antwortformate`).

| Mode | Anfrage | Antwort | Wofür | Code |
| --- | --- | --- | --- | --- |
| 01 | `01 <PID>` | `41 <PID> <Daten>` | aktuelle Werte, unterstützte PIDs, Readiness | `protocol/pids.py`, `services/readiness.py` |
| 02 | `02 <PID> 00` | `42 <PID> 00 <Daten>` | Freeze Frame 00 | `protocol/obd.py` |
| 03 | `03` | `43 [Zahl] <Codes>` | gespeicherte Fehlercodes | `protocol/obd.py`, `dtc_decode.py` |
| 04 | `04` | `44` | Fehlercodes löschen (gesperrt) | `protocol/obd.py`, `services/clear.py` |
| 06 | `06 <MID>` | `46 <MID> <TID> <Einheit> <Wert> <Min> <Max> …` | Testergebnisse (nur CAN) | `protocol/monitor_tests.py` |
| 07 | `07` | `47 [Zahl] <Codes>` | ausstehende Fehlercodes | wie 03 |
| 09 | `0902` | `49 02 01 <17 Zeichen>` | FIN | `services/vehicle.py` |
| 09 | `0900`, `0904`, `0906`, `0908`, `090A`, `090B` | `49 <Typ> <Zahl> <Daten>` | Steuergeräte-Informationen | `protocol/vehicle_info.py` |
| 0A | `0A` | `4A [Zahl] <Codes>` | permanente Fehlercodes | wie 03 |

Die Zahl der Codes steht nur bei CAN in der Antwort ({doc}`antwortformate`).

## Mode 01: aktuelle Daten

**Unterstützte PIDs.** `0100` liefert vier Bytes, je Bit eine PID: Bit 7 von A ist PID
01, Bit 0 von D ist PID 20. Ist PID 20 gesetzt, gibt es mit `0120` die nächsten 32 PIDs,
dann `0140` usw. obd-diag fragt das bei allen Steuergeräten ab und vereinigt die
Ergebnisse.

```text
4100BE3FA813   → A=BE B=3F C=A8 D=13: u. a. PIDs 01, 03, 04, 05, 06, 07 unterstützt
```

**Werte.** Jede PID hat eine feste Umrechnung, z. B. `410C1AF8` → (256 · 0x1A + 0xF8) / 4
= 1726 1/min. Alle 116 Werte mit Formel: {doc}`pids`.

**Readiness (PID 01).** Vier Datenbytes A bis D:

| Bits | Bedeutung |
| --- | --- |
| A7 | Motorkontrollleuchte (MIL) an |
| A6 bis A0 | Zahl der gespeicherten Codes dieses Steuergeräts |
| B3 | 0 = Ottomotor, 1 = Diesel |
| B0 bis B2 / B4 bis B6 | Aussetzer, Kraftstoffsystem, Komponenten: unterstützt / nicht abgeschlossen |
| C, D | Bit n: Monitor n unterstützt / nicht abgeschlossen; Bedeutung je nach Motorart (Katalysator, Lambdasonde, AGR … bzw. NOx, Partikelfilter, Ladedruck …) |

Antworten mehrere Steuergeräte, zählt je Monitor der schlechteste Stand, die MIL ist
an, wenn eines sie meldet, und die Codezahlen werden addiert.

**Readiness dieser Fahrt (PID 41).** Gleicher Aufbau wie PID 01, aber Byte A ist
reserviert, und die Bits gelten nur für den laufenden Fahrzyklus (seit Zündung an): das
erste Bit je Monitor heißt „in dieser Fahrt freigegeben“ (0 = ausgesetzt oder nicht
vorhanden), das zweite „in dieser Fahrt nicht abgeschlossen“. Welche Belegung C und D
haben, kommt aus PID 01. Nutzt der Readiness-Assistent ({doc}`../benutzen/readiness`,
`services/readiness_assist.py`).

## Mode 02: Freeze Frame

Beim Speichern eines Codes hält das Steuergerät einige Messwerte fest. obd-diag liest
Frame 00: PID 02 (auslösender Code), 04 (Last), 05 (Kühlmittel), 0C (Drehzahl), 0D
(Geschwindigkeit).

```text
020200 → 4202000420   42 02 00: Antwort auf PID 02, Frame 00; 04 20: auslösender Code P0420
```

Die Frame-Nummer gehört nach J1979 in die Anfrage (`020C00`). Antwortet das Fahrzeug
auf `020200` mit `NO DATA` oder `7F 02 12`, wird `0202` ohne Frame-Nummer versucht (wie
python-OBD); klappt das, wird der ganze Freeze Frame so gelesen. Welche Form ein
Fahrzeug erwartet, lässt sich nur am echten Gerät klären.

## Mode 03, 07, 0A: Fehlercodes

Ein Code besteht aus zwei Bytes (SAE J2012):

```{mermaid}
flowchart LR
    B["04 20"] --> H["erstes Byte 0x04 = 0000 0100"]
    H --> S["Bits 7-6: 00 → P"]
    H --> D1["Bits 5-4: 00 → 0"]
    H --> D2["Bits 3-0: 0100 → 4"]
    B --> L["zweites Byte 0x20 → 20"]
    S & D1 & D2 & L --> C["P0420"]
```

| Bits 7–6 des ersten Bytes | Buchstabe | Bereich |
| --- | --- | --- |
| `00` | P | Antrieb (Powertrain) |
| `01` | C | Fahrwerk (Chassis) |
| `10` | B | Karosserie (Body) |
| `11` | U | Netzwerk |

Beispiel: `C1 00` → Bits 7–6 `11` = U, dann `0`, `1`, `00` → **U0100**.

## Mode 04: Löschen

Löscht gespeicherte und ausstehende Codes, Freeze Frame und Readiness. Permanente
Codes bleiben. Nach SAE muss ein Diagnosegerät vorher nachfragen (Datenblatt S. 35).
Mode 04 ist gesperrt und nur über den Ablauf unter {doc}`../benutzen/fehlercodes`
erreichbar; die Absicherung beschreibt {doc}`sicherheit`.

## Mode 06: Testergebnisse

`0600` liefert wie `0100` eine Bitmaske der unterstützten Monitor-IDs (MID) `01` bis
`20`, `0620` die nächsten usw. Für jede gesetzte MID fragt obd-diag `06 <MID>` ab. Bei
CAN besteht die Antwort aus Blöcken zu 9 Bytes, einer je Test:

```text
4621 80 24 0064 0000 00C8
```

`46 21`: Antwort für MID 21 (Katalysator Bank 1); `80`: Test-ID (ab `80` vom Hersteller
festgelegt); `24`: Einheit und Skalierung (UASID 24: Anzahl, Faktor 1); dann Wert `0064`
= 100, Minimum `0000` = 0 und Maximum `00C8` = 200: bestanden. UASIDs ab `80` sind
vorzeichenbehaftet (Zweierkomplement). Die Tabelle der Einheiten steht in SAE J1979
Anhang E; obd-diag kennt dieselben wie python-OBD. Ältere Protokolle antworten in einem
anderen Format ohne UASID; obd-diag liest Mode 06 dort nicht.

Mode 06 und Mode 09 (außer der FIN) liest obd-diag mit eingeschalteten Headern
(`ATH1`, danach wieder `ATH0`), damit jeder Wert dem richtigen Steuergerät zugeordnet
wird.

## Mode 09: FIN

`0902` liefert die Fahrzeug-Identifizierungsnummer, bei CAN mehrteilig:

```text
014
0: 490201575657
1: 5A5A5A314B5A36
2: 57313233343536
```

0x014 = 20 Bytes: `49 02` (Antwort, InfoType 02), `01` (ein Datenelement), dann 17
ASCII-Zeichen **WVWZZZ1KZ6W123456**. Ältere Protokolle liefern fünf Zeilen
`49 02 <1–5> <4 Bytes>`, die erste vorn mit `00` aufgefüllt. Die Anfrage heißt genau
`0902`; `09021` (mit falscher Antwortzahl) kann laut Datenblatt Probleme machen und ist
verboten. Die Dekodierung beschreibt {doc}`fin`.

## Mode 09: Steuergeräte

`0900` liefert je Steuergerät eine Bitmaske der unterstützten InfoTypes. obd-diag fragt
davon nur `04` (Kalibrierungs-IDs, je 16 ASCII-Zeichen), `06` (CVN, je 4 Bytes), `08`
bzw. `0B` (Überwachungszähler Otto bzw. Diesel, je 2 Bytes) und `0A` (Name, 20
ASCII-Zeichen) ab. Das Byte nach dem InfoType nennt die Zahl der Datenelemente:

```text
4906019F3E61C2
```

`49 06`: Antwort auf InfoType 06; `01`: eine CVN; `9F3E61C2`: die CVN. Die
Überwachungszähler beginnen mit den Fahrzyklen mit erfüllten Bedingungen und der Zahl
der Zündungen, danach je Test ein Paar *abgeschlossen*, *Bedingungen erfüllt*. Was
obd-diag daraus zeigt, beschreibt {doc}`../benutzen/steuergeraete-tests`.
