# Antwortformate

Nach dem Bereinigen ({doc}`elm327`) bleibt Hex-Text. Wie er zu lesen ist, hängt davon
ab, ob Header an sind, ob das Fahrzeug CAN spricht und ob die Antwort in einen
CAN-Frame passt. Alle Beispiele auf dieser Seite prüft
`tests/unit/test_docs_examples.py` gegen den Code.

## Ohne Header (Normalfall, `ATH0`)

### Einteilige Antwort

Jede Zeile ist die vollständige Antwort eines Steuergeräts. Antworten zwei
Steuergeräte (Motor und Getriebe), stehen zwei Zeilen da.

```text
410C1AF8
```

| Bytes | Bedeutung |
| --- | --- |
| `41` | positive Antwort auf Mode 01 (Mode + `40`) |
| `0C` | PID 0C, Motordrehzahl |
| `1A F8` | Daten A, B: (256 · 26 + 248) / 4 = **1726 1/min** |

### Mehrteilige CAN-Antwort (ISO-TP)

Passt eine Antwort nicht in einen CAN-Frame (mehr als 7 Bytes), setzt der ELM327 sie
selbst zusammen und schreibt sie so (Datenblatt S. 42 ff., „Multiline Responses“):

```text
00A
0: 430404200133
1: 0300C100
```

| Zeile | Bedeutung |
| --- | --- |
| `00A` | Länge der Nutzdaten: 10 Bytes (hex) |
| `0: 43 04 04 20 01 33` | Frame 0: Mode-Byte `43` (Antwort auf `03`), Zählbyte `04`, erste zwei Codes |
| `1: 03 00 C1 00` | Frame 1: zwei weitere Codes; ein aufgefüllter Rest würde auf die Länge gekürzt |

Ergebnis: vier gespeicherte Codes **P0420, P0133, P0300, U0100**. Die Frame-Nummern
laufen 0 bis F und dann wieder 0. Fehlt ein Frame oder stimmt die Reihenfolge nicht,
meldet `split_messages` einen `FrameSequenceError`, statt falsche Daten zu liefern.

## Mit Header (`ATH1`)

Senden zwei Steuergeräte gleichzeitig mehrteilige Antworten, mischt der ELM327 ohne
Header deren Frames (Datenblatt S. 45, Beispiel `09 04`); eine Zuordnung ist dann
unmöglich. obd-diag erkennt das an Lücken in der Nummerierung oder unvollständigen
Nachrichten und wiederholt die lesende Anfrage einmal mit Headern. Danach wird wieder
`ATH0` gesetzt; gelingt das nicht, bricht die Sitzung ab, damit niemand weiter falsch
liest.

Mit Header trägt jede Zeile ihren Absender, und die Frames werden je Steuergerät nach
ISO 15765-2 zusammengesetzt:

```text
7E8 10 0A 43 04 04 20 01 33
7E9 04 43 01 01 71
7E8 21 03 00 C1 00 00 00 00
```

| Teil | Bedeutung |
| --- | --- |
| `7E8`, `7E9` | CAN-ID (11 Bit) des antwortenden Steuergeräts: Motor bzw. Getriebe |
| `10 0A` | PCI erster Frame: Typ 1, Länge 0x00A = 10 Bytes; dann 6 Datenbytes |
| `21` | PCI Folge-Frame Nummer 1; bis zu 7 Datenbytes, Füllbytes am Ende werden abgeschnitten |
| `04` | PCI Einzel-Frame mit 4 Datenbytes |

Ergebnis, nach Steuergeräte-Adresse geordnet: `7E8` meldet P0420, P0133, P0300,
U0100; `7E9` meldet P0171.

| Format | Beispiel | Header |
| --- | --- | --- |
| CAN 11 Bit | `7E8 06 41 00 BE 3F A8 13` | 3 Hex-Ziffern CAN-ID |
| CAN 29 Bit | `18 DA F1 10 06 41 00 BE 3F A8 13` | 4 Bytes, OBD-Antworten `18 DA F1 <Quelle>` |
| J1850, ISO 9141, KWP | `48 6B 10 41 00 BE 3E B8 11 FA` | 3 Header-Bytes (Priorität, Ziel, Quelle), am Ende 1 Prüfbyte |

Das Prüfbyte älterer Protokolle wird entfernt, aber nicht geprüft; der ELM327 verwirft
fehlerhafte Nachrichten selbst bzw. meldet `<DATA ERROR`.

## Fehlercodes: das Zählbyte bei CAN

Bei CAN folgt auf das Mode-Byte immer ein Zählbyte (SAE J1979), danach je Code zwei
Bytes. Bytes nach den gezählten Codes sind Füllbytes und werden ignoriert; sind es zu
wenige, ist die Antwort kaputt (`ValueError`). Ältere Protokolle haben kein Zählbyte
und füllen jede Zeile mit `00 00` auf drei Codes auf.

| Protokoll | Antwort auf `03` | Codes |
| --- | --- | --- |
| CAN | `430204200133` | Zählbyte `02`: P0420, P0133 |
| CAN, keine Codes | `4300` | Zählbyte `00`: keine |
| J1850/ISO 9141/KWP | `43042001330000` | P0420, P0133; `00 00` ist Füllung |

Ob CAN, kommt aus `ATDPN` ({doc}`elm327`). Eine Antwort ohne Zählbyte bei CAN wird
bewusst nicht toleriert. Der ELM327-emulator lässt das Zählbyte weg; das korrigieren
die Tests, nicht der Produktcode ({doc}`../entwickeln/testen`).

## Ablehnungen (`7F`)

Ein Steuergerät kann eine Anfrage mit `7F <Mode> <Grund>` ablehnen (Codes nach
ISO 14229-1 / ISO 15031-5).

| Antwort | Bedeutung | obd-diag |
| --- | --- | --- |
| `7F 0A 11`, `7F 0A 12` | Mode nicht unterstützt | „keine Codes dieser Art von diesem Steuergerät“ |
| `7F 03 21` | beschäftigt, später wiederholen | Fehler, nie „keine Codes“ |
| `7F 04 22` | Bedingungen nicht erfüllt (z. B. Motor läuft) | Löschen bricht ab, Sicherung bleibt |
| `7F 03 78` | Antwort folgt | bis 5 s weiterlesen, ohne erneut zu senden; jede spätere positive Antwort zählt als die angekündigte |
