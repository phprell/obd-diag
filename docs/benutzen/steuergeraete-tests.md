# Steuergeräte und Testergebnisse

Neben der FIN geben die Steuergeräte der Abgasdiagnose weitere Auskünfte (Mode 09) und
die Messwerte ihrer Eigendiagnosen samt Grenzwerten (Mode 06). obd-diag liest beides
bei jeder Diagnose mit; einzeln gibt es eigene Befehle:

```sh
obd-diag ecus --port /dev/ttyUSB0           # Mode 09 je Steuergerät
obd-diag tests --port /dev/ttyUSB0          # Mode 06 Testergebnisse
obd-diag tests --demo --json                # Beispielfahrzeug, maschinenlesbar
```

In der Oberfläche stehen die Steuergeräte im Reiter *Fahrzeug* unter der FIN, die
Testergebnisse im Reiter *Testergebnisse*. Der PDF-Bericht und gespeicherte Sitzungen
enthalten beides, die CSV-Datei nicht.

## Steuergeräte (Mode 09)

```
Steuergeräte (Mode 09):
  18DAF110  ECM-EngineControl
    Kalibrierungs-ID:               A6519006400
    CVN:                            9F3E61C2
    Überwachungszähler:             96 Fahrzyklen, 240 Zündungen
      NMHC-Katalysator:             40 von 96 (0.42)
      Abgassensor:                  0 von 96 (0.00)
      …
  18DAF118  TCM-TransmissionCtl
    Kalibrierungs-ID: A7229002000
    CVN:              0C55D1E7
```

Je Steuergerät, das antwortet (Adresse aus dem CAN-Header):

| Angabe | Anfrage | Bedeutung |
| --- | --- | --- |
| Name | `090A` | Kurzname des Steuergeräts, etwa `ECM-EngineControl` (Motor) oder `TCM-TransmissionCtl` (Getriebe) |
| Kalibrierungs-ID | `0904` | Kennung des Software-Stands; ändert sich mit einem Software-Update |
| CVN | `0906` | Prüfsumme über die Kalibrierung; eine andere CVN bei gleicher Kalibrierungs-ID heißt, die Software wurde verändert |
| Überwachungszähler | `0908` (Otto), `090B` (Diesel) | wie oft ein Test abgeschlossen wurde und wie oft die Bedingungen dafür gegeben waren |

Die Überwachungszähler (In-Use Performance Tracking) zeigen, ob ein Test im Alltag
überhaupt läuft. Die Quote ist *abgeschlossen / Bedingungen*; je nach Test und Vorschrift
(EU, Kalifornien) sind Mindestquoten zwischen 0,1 und 0,336 gefordert. Eine Quote von 0 heißt: Der Test hatte
Gelegenheit, ist aber nie fertig geworden. Das passt oft zu einem dauerhaft offenen
Readiness-Test ({doc}`readiness`). Gezählt wird seit dem Bau bzw. dem letzten
Software-Update; Löschen der Fehlercodes setzt die Zähler nicht zurück.

Welche Angaben ein Steuergerät liefert, meldet es selbst (`0900`). obd-diag fragt nur
diese ab und nur die genannten; die Seriennummer des Steuergeräts und Ähnliches bleiben
außen vor.

Fehlen Name oder Kalibrierungs-ID, war die Antwort meist zu lang für den Adapter: Antworten
mehrere Steuergeräte gleichzeitig mehrteilig, läuft sein Puffer über (`BUFFER FULL`,
besonders bei 38400 Baud). obd-diag behält dann, was vollständig ankam, und fragt einmal
nach. Am Mercedes W177 fehlen so nur die vier Kalibrierungs-IDs des Motorsteuergeräts.

## Testergebnisse (Mode 06)

```{image} ../_static/screenshots/demo-testergebnisse-light.png
:alt: Reiter Testergebnisse im Demo-Modus: sieben Tests des Motorsteuergeräts
:class: only-light
```

```{image} ../_static/screenshots/demo-testergebnisse-dark.png
:alt: Reiter Testergebnisse im Demo-Modus: sieben Tests des Motorsteuergeräts
:class: only-dark
```

Die Steuergeräte prüfen Katalysator, Lambdasonden, Abgasrückführung, Partikelfilter und
andere Bauteile regelmäßig selbst. Mode 06 liefert zu jedem dieser Tests den zuletzt
gemessenen Wert und die Grenzen, innerhalb derer das Steuergerät ihn als in Ordnung
bewertet. Damit lässt sich sehen, wie knapp ein Bauteil noch besteht, bevor ein
Fehlercode entsteht.

| Spalte | Bedeutung |
| --- | --- |
| Monitor | was geprüft wird, z. B. *Katalysator Bank 1* (genormte Monitor-ID) |
| Test | welcher Test des Monitors; ab Nummer 80 legt der Hersteller fest, was er misst |
| Wert, Grenzen | Messwert mit Minimum und Maximum, umgerechnet in die genormte Einheit |
| Ergebnis | *bestanden* (Minimum ≤ Wert ≤ Maximum), *nicht bestanden* oder *kein Ergebnis* |

*Kein Ergebnis* zeigt obd-diag, wenn Wert und Grenzen alle 0 sind: Meist ist der Test
seit dem letzten Löschen noch nicht gelaufen. Ein *nicht bestanden* allein ist noch kein
Fehler; das Steuergerät setzt erst nach eigener Logik (oft zwei Fahrten in Folge) einen
Fehlercode.

Grenzen am Rand des Wertebereichs heißen „keine Grenze“: Bei Einheiten mit Vorzeichen
steht dann etwa −3276,8 °C als Minimum (Rohwert `8000`, der kleinste mögliche Wert), ohne
Vorzeichen 0 als Minimum oder 65535 als Maximum. Ab Testnummer 80 sagt nur der Hersteller,
was gemessen wird; obd-diag zeigt dort Wert, Grenzen und Urteil, aber keine Deutung.

obd-diag liest Mode 06 nur bei CAN-Fahrzeugen (etwa ab 2008). Ältere Protokolle
verwenden ein anderes Format ohne Einheiten, das hier nicht ausgewertet wird. Wie die
Anfragen aussehen, steht unter {doc}`../technik/dienste`.

:::{note}
Die Antworten des Demo-Fahrzeugs auf Mode 09 und Mode 06 sind erfunden. Echte Antworten
eines Mercedes W177 (dritter Autotest) prüfen die Regressionstests
(`tests/verification/test_real_car_mode09.py`).
:::
