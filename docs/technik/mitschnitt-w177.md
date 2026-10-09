# Mitschnitt vom W177, Zeile für Zeile

Diese Seite geht die Mitschnitte des ersten Tests am echten Auto durch: Mercedes A 180 d
(W177, Diesel), Adapter FORScan ELMconfig (ELM327 v1.5, CH340) an `/dev/ttyUSB0`,
38400 Baud, Motor aus, Zündung an, 2026-10-09. Wie der Test ablief und was danach
behoben wurde, steht unter {doc}`../benutzen/erster-test`.

:::{note}
**Geschwärzt.** Die Seriennummer der FIN (Stellen 12 bis 17) ist durch `000000`
ersetzt, in den Hex-Bytes ebenso wie im Klartext. Sonst sind die Zeilen unverändert.
Alle Byte-Beispiele dieser Seite prüft `tests/unit/test_docs_examples.py` gegen den
Code; der ganze Diagnose-Mitschnitt läuft als Regressionstest
(`tests/verification/test_real_car.py`, Mitschnitte in
`tests/fixtures/traces/mercedes_w177/`).
:::

Ein Mitschnitt (`--trace`) hat je Zeile die Zeit seit dem Öffnen des Ports in Sekunden,
die Richtung (`>>` an den Adapter, `<<` vom Adapter) und den Text, mit `\r` für den
Zeilenumbruch. `>` am Ende ist der Prompt: Der Adapter ist bereit für den nächsten
Befehl. Das Format ist unter {doc}`../benutzen/sitzungen` beschrieben.

## Verbindungsaufbau

```text
    0.016 >> ATZ\r
    0.817 << \r\rELM327 v1.5\r\r>
    0.817 >> ATE0\r
    0.825 << ATE0\rOK\r\r>
    0.826 >> ATL0\r
    0.834 << OK\r\r>
    0.834 >> ATS0\r
    0.843 << OK\r\r>
    0.843 >> ATH0\r
    0.852 << OK\r\r>
    0.852 >> ATSP0\r
    0.861 << OK\r\r>
    0.861 >> ATRV\r
    0.875 << 11.2V\r\r>
```

`ATZ` setzt den Adapter zurück und dauert rund 0,8 s. Auf `ATE0` (Echo aus) kommt
das Echo noch einmal mit, danach nicht mehr. `ATL0`, `ATS0`, `ATH0` schalten
Zeilenvorschub, Leerzeichen und Header aus, `ATSP0` wählt die automatische
Protokollsuche ({doc}`elm327`). `ATRV` ist die Spannung, die der Adapter selbst an
Pin 16 misst: **11,2 V**.

Direkt nach dem Einstecken sah der allererste Befehl anders aus:

```text
    0.024 >> ATZ\r
    0.033 << ATZ\r?\r\r>
```

Der Adapter lehnte das erste `ATZ` mit `?` ab, vermutlich wegen eines Störbytes im
Puffer des Adapters. Seit diesem Test wird ein `ATZ`, das mit `?` beantwortet
wird, einmal wiederholt; beim zweiten Mal kam wie oben `ELM327 v1.5`.

## Protokollsuche und vier Steuergeräte

```text
    0.925 >> 0100\r
    1.255 << SEARCHING...\r410098180001\r410098180001\r410098180011\r410098180001\r\r>
    1.255 >> ATDPN\r
    1.264 << A7\r\r>
    1.264 >> ATDP\r
    1.282 << AUTO, ISO 15765-4 (CAN 29/500)\r\r>
```

Das erste `0100` stößt die Protokollsuche an (`SEARCHING...`); nach 0,3 s steht sie.
Danach kommen **vier Zeilen**: Vier Steuergeräte beantworten die Anfrage, jedes mit
seiner Liste unterstützter PIDs. `ATDPN` meldet `A7`: `A` heißt „automatisch
gefunden“, `7` ist ISO 15765-4 CAN mit 29-Bit-Adressen und 500 kBit/s. Daraus folgt
`can=True`, also wird bei Fehlercodes das Zählbyte gelesen ({doc}`antwortformate`).

Eine Zeile zerlegt:

| Bytes | Bedeutung |
| --- | --- |
| `41 00` | positive Antwort auf Mode 01, PID 00 |
| `98 18 00 01` | Bitmaske: PIDs `01`, `04`, `05`, `0C`, `0D` und `20` (weitere Liste) |
| `98 18 00 11` | dasselbe, eines der Steuergeräte meldet zusätzlich `1C` (OBD-Norm) |

Die Reihenfolge der vier Zeilen ist nicht fest: Im Live-Mitschnitt kam dieselbe
Antwort als `…0001`, `…0011`, `…0001`, `…0001`. Ohne Header lässt sich eine Zeile
deshalb keinem Steuergerät zuordnen. obd-diag behandelt jede Zeile als eigene Antwort
und wertet sie nach Inhalt aus, nicht nach Position.

Die weiteren Listen (`0120` bis `0180`, im Live-Mitschnitt) beantworten immer weniger
Steuergeräte. Zusammen bietet das Fahrzeug diese PIDs an:

| Liste | Antworten | unterstützte PIDs |
| --- | --- | --- |
| `0100` | 4 | `01`, `04`, `05`, `0C`, `0D`, `1C` (eines), `20` |
| `0120` | 4 | `21`, `30`, `31`, `40` (drei) |
| `0140` | 3 | `41`, `42`, `49` (eines), `60` (zwei) |
| `0160` | 2 | `66`, `68`, `6C`, `6D`, `70` (eines), `80` |
| `0180` | 2 | `85`, `88`, `92` |

`68`, `6C`, `6D`, `70`, `85`, `88` und `92` liest obd-diag noch nicht: Es sind PIDs mit
Statusbyte, deren Aufbau in freien Quellen nicht eindeutig ist (Liste im Docstring von
`protocol/pids.py`). Dass der W177 etwa `70` (Ladedruckregelung) anbietet, macht ihn
zu einem guten Kandidaten, um das Format später an echten Antworten zu klären.

## Steuergerätespannung (PID 42)

```text
    1.290 >> 0142\r
    1.320 << 41422EDC\r\r>
```

Nur ein Steuergerät antwortet. `2E DC` sind 11996 mV, also **12,0 V**, gut 0,8 V mehr
als die 11,2 V aus `ATRV`. Der Adapter misst an Pin 16, oft hinter einer Schutzdiode,
und zeigt deshalb weniger an. Seit diesem Test liest obd-diag PID 42, wenn `ATRV` unter 11,8 V
liegt, und nimmt den Wert des Steuergeräts, wenn er plausibel ist (5 bis 30 V). Im
Test selbst gab es diese Abfrage noch nicht; die Zeile stammt aus dem Live-Mitschnitt
derselben Sitzung und ist im Regressionstest an dieser Stelle eingefügt.

## Fehlercodes

```text
    1.332 >> 03\r
    1.562 << 4301D218\r4300\r4300\r4300\r\r>
    1.612 >> 07\r
    1.701 << 4700\r4700\r4700\r4700\r\r>
    1.752 >> 0A\r
    1.837 << 4A00\r\r>
```

| Antwort | Bedeutung |
| --- | --- |
| `43 01 D2 18` | Mode 03, Zählbyte `01`, ein Code: `D2 18` |
| `43 00` | drei Steuergeräte: keine gespeicherten Codes |
| `47 00` (viermal) | keine ausstehenden Codes (Mode 07) |
| `4A 00` | keine permanenten Codes (Mode 0A); nur ein Steuergerät antwortet |

`D2 18` dekodiert: Die oberen zwei Bits von `D` (`11`) ergeben `U`
(Netzwerk), die nächsten zwei (`01`) die `1`, der Rest `2 18`: **U1218**. Die `1` an
zweiter Stelle heißt herstellerspezifisch, deshalb steht im Katalog kein Text dazu
({doc}`../benutzen/fehlercodes`).

## Readiness (Mode 01 PID 01)

```text
    1.887 >> 0101\r
    1.973 << 4101000EEB20\r410100040000\r4101000C0200\r4101010C0000\r\r>
```

Byte A ist die Motorkontrollleuchte (Bit 7) und die Zahl der Codes (Bits 0 bis 6),
B die allgemeinen Monitore und die Motorart (Bit 3), C und D die motorspezifischen
Monitore ({doc}`dienste`).

| Antwort | MIL | Codes | Diesel | Monitore |
| --- | --- | --- | --- | --- |
| `00 0E EB 20` | aus | 0 | ja | sechs abgeschlossen, Abgassensor offen |
| `00 04 00 00` | aus | 0 | nein | nur Komponenten |
| `00 0C 02 00` | aus | 0 | ja | Komponenten, NOx-Nachbehandlung |
| `01 0C 00 00` | aus | 1 | ja | nur Komponenten |

Das erste ist vermutlich das Motorsteuergerät, das vierte das mit U1218 (es meldet den
einen Code). obd-diag fasst die vier zusammen (`combine_readiness`): Die Motorart
kommt von der ersten Zeile, Zeilen mit anderer Motorart zählen für die Monitore nicht
mit (hier das zweite Steuergerät, dessen Bits C/D anders belegt wären). Je Monitor gilt
der schlechteste Stand, die MIL ist an, wenn eines sie meldet, und die Codes werden
addiert. Ergebnis: Kontrollleuchte aus, ein Code, Diesel, alles abgeschlossen bis auf
den Abgassensor, Verbrennungsaussetzer nicht unterstützt.

## Freeze Frame (Mode 02)

```text
    2.023 >> 020200\r
    2.091 << 4202000000\r4202000000\r4202000000\r420200D218\r\r>
    2.141 >> 020400\r
    2.201 << 42040000\r\r>
    2.251 >> 020500\r
    2.312 << 4205004D\r\r>
    2.362 >> 020C00\r
    2.422 << 420C000000\r\r>
    2.473 >> 020D00\r
    2.529 << 420D0000\r\r>
```

`020200` fragt, welcher Code den Freeze Frame ausgelöst hat (Frame `00`). Drei
Steuergeräte antworten `00 00`: kein Freeze Frame. Eines antwortet `D2 18`, also
U1218. Vor diesem Test nahm obd-diag die erste Zeile und zeigte „unbekannt“; jetzt
zählt die Zeile mit einem Code.

Die Werte danach kommen nur von diesem einen Steuergerät: Last `00` = 0 %,
Kühlmittel `4D` = 77 − 40 = **37 °C**, Drehzahl `00 00` = 0, Geschwindigkeit `00` = 0.
Der Code wurde also im Stand bei halbwarmem Motor gespeichert.

## FIN (Mode 09 PID 02)

```text
    2.579 >> 0902\r
    2.656 << 014\r0:490201574444\r1:31373730303331\r2:4A303030303030\r\r>
```

Eine mehrteilige ISO-TP-Antwort im ELM-Format ({doc}`antwortformate`): `014` sind
20 Bytes Nutzdaten, nämlich `49 02 01` (Antwort, PID, Anzahl) und die 17 Zeichen der
FIN in ASCII. Zusammengesetzt: `WDD1770031J000000`.

| Stellen | Wert | Bedeutung |
| --- | --- | --- |
| 1 bis 3 | `WDD` | Hersteller-Code (WMI): Mercedes-Benz, Deutschland |
| 4 bis 9 | `177003` | Fahrzeugbeschreibung, darin die Baureihe 177 |
| 10 | `1` | bei Mercedes die Lenkung, **kein Modelljahr** |
| 11 | `J` | bei Mercedes das Werk |
| 12 bis 17 | `000000` | Seriennummer, hier geschwärzt |

Vor diesem Test zeigte obd-diag aus Stelle 10 das Modelljahr „2001“. Europäische
Mercedes-FINs kodieren dort kein Modelljahr, deshalb zeigt obd-diag für sie keins mehr
an ({doc}`fin`). Eine Prüfziffer (Stelle 9) ist außerhalb Nordamerikas nicht
vorgeschrieben; sie „weicht ab“, und das ist kein Fehler.

## Live-Daten

`obd-diag live --duration 20 --record --trace`, Motor aus. Vor der ersten Runde liest
`prepare_live` die PID-Listen `0100` bis `0180` (oben) und einmal `ATRV`. Von den
Standardwerten unterstützt der W177 alle außer der Ansauglufttemperatur (`0F`), die
damit ohne Meldung entfällt. Eine Runde:

```text
    2.185 >> 010C\r
    2.254 << 410C0000\r410C0000\r410C0000\r410C0000\r\r>
    2.305 >> 010D\r
    2.373 << 410D00\r410D00\r410D00\r410D00\r\r>
    2.423 >> 0105\r
    2.488 << 41053B\r41053B\r41053B\r41053B\r\r>
    2.538 >> 0104\r
    2.602 << 410400\r410400\r410400\r410400\r\r>
    2.652 >> 0142\r
    2.709 << 41422EDC\r\r>
```

Drehzahl 0, Geschwindigkeit 0, Kühlmittel `3B` = 59 − 40 = **19 °C**, Last 0 %,
Steuergerätespannung 12,0 V. Zwischen zwei Anfragen liegen gut 50 ms: Das ist die
Mindestpause, die obd-diag vor jeder Anfrage ans Fahrzeug einhält
({doc}`sicherheit`).

Die nächsten Runden beginnen bei 7,1 s, 12,1 s und 17,1 s, also alle 5 s statt jede
Sekunde. Grund war die Drosselung bei niedriger Bordspannung: `ATRV` meldete 11,2 V.
Die Steuergerätespannung stieg in den vier Runden von 11,996 auf 12,007 V. Seit diesem
Test prüft auch Live-Daten ein niedriges `ATRV` per PID 42 gegen; mit 12,0 V vom
Steuergerät wäre nicht gedrosselt worden.

## Was der Mitschnitt nicht zeigt

- **Der erste Versuch.** Mit Zündung aus endete die Protokollsuche nach dem ersten
  `0100` mit `UNABLE TO CONNECT`; obd-diag brach ab, gesendet waren nur AT-Befehle und
  dieses eine `0100`. Erst mit Zündung an antworteten die Steuergeräte. Die Meldung
  dazu lautet seit PR #8 „Kein Steuergerät antwortet. Zündung einschalten (der Motor
  darf aus bleiben), bei Adaptern mit MS-/HS-CAN-Schalter HS-CAN wählen und erneut
  versuchen.“
- **Welches Steuergerät welches ist.** Dafür bräuchte es Header (`ATH1`): Dann trägt
  jede Zeile ihre CAN-Adresse, etwa `18 DA F1 10` ({doc}`antwortformate`). obd-diag
  schaltet sie nur zu, wenn sich mehrteilige Antworten mehrerer Steuergeräte mischen.
  Das war hier nicht nötig, weil nur die FIN mehrteilig ist und nur ein Steuergerät
  sie sendet.
- **Löschen.** Mode 04 kam nicht vor; Löschen ist weiter gesperrt
  ({doc}`../benutzen/fehlercodes`).
