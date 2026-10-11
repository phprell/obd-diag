# Gebrauchtwagen-Check

Wer einen Gebrauchtwagen ansieht, will wissen, ob jemand kurz vorher die Fehlercodes
gelöscht hat, damit die Motorkontrollleuchte aus ist. Das lässt sich am Fahrzeug
ablesen, nur lesend: Beim Löschen (Mode 04) setzt jedes Abgas-Steuergerät einige
Zähler auf null, und die laufen erst mit dem Fahren wieder hoch.

```sh
obd-diag used-car --port /dev/ttyUSB0          # nur der Check
obd-diag used-car --port /dev/ttyUSB0 --json   # maschinenlesbar
obd-diag diagnose --port /dev/ttyUSB0 --pdf bericht.pdf   # vollständig, mit Check
```

Die vollständige Diagnose (`obd-diag diagnose`, „Verbinden & Scannen“ in der Oberfläche)
liest den Check immer mit. In der Oberfläche steht er im Reiter „Gebrauchtwagen“, im
PDF-Bericht nach der Readiness. Am besten mit Zündung an und Motor aus, wie bei der
Diagnose ({doc}`erster-test`).

```{image} ../_static/screenshots/gebrauchtwagen-light.png
:alt: Reiter Gebrauchtwagen: Urteil, Befunde und Zähler seit dem Löschen
:class: only-light
```

```{image} ../_static/screenshots/gebrauchtwagen-dark.png
:alt: Reiter Gebrauchtwagen: Urteil, Befunde und Zähler seit dem Löschen
:class: only-dark
```

## Was gelesen wird

| Anfrage | Bedeutung | beim Löschen |
| --- | --- | --- |
| `0130` | Warmläufe seit dem Löschen (0 bis 255) | auf 0 |
| `0131` | Strecke seit dem Löschen in km | auf 0 |
| `014E` | Zeit seit dem Löschen in Minuten | auf 0 |
| `0121` | Strecke mit Warnleuchte an in km | auf 0 |
| `014D` | Zeit mit Warnleuchte an in Minuten | auf 0 |
| `0A` | permanente Fehlercodes | bleiben |
| `0101` | Warnleuchte und Readiness-Tests | Tests wieder offen |

Ein Warmlauf heißt nach SAE J1979: Das Kühlmittel ist seit dem Start um mindestens
22 °C gestiegen und hat mindestens 70 °C erreicht. Nicht jedes Fahrzeug meldet alle
Zähler; der Mercedes A 180 d aus den Tests am Auto kennt `0121`, `0130` und `0131`,
aber keine Zeiten. Antworten mehrere Steuergeräte, gilt bei den Zählern seit dem
Löschen der kleinste Wert und bei den Warnleuchten-Zählern der größte.

## Wie das Urteil zustande kommt

**Auffällig** ist ein Fahrzeug, wenn mindestens einer dieser Befunde zutrifft:

- vor kurzem gelöscht: weniger als 10 Warmläufe, 200 km oder 300 Minuten seit dem
  Löschen
- die Motorkontrollleuchte ist an, oder sie war seit dem letzten Löschen an (Strecke
  oder Zeit mit Warnleuchte über 0)
- permanente Fehlercodes: Sie lassen sich nicht löschen; das Steuergerät entfernt sie
  erst, wenn es den Fehler selbst nicht mehr feststellt

**Unauffällig** heißt: Die Zähler sind lesbar und keiner dieser Befunde trifft zu.
**Nicht beurteilbar** ist es, wenn das Fahrzeug keinen der Zähler seit dem Löschen
meldet und sonst nichts auffällt. Offene Readiness-Tests stehen als Hinweis dabei: Nach
dem Löschen sind sie wieder offen, sie können aber auch an den Fahrbedingungen liegen.

:::{note}
Die Grenzen sind eine Faustregel, keine Norm. Auch eine Werkstatt, ein Batteriewechsel
oder ein neues Steuergerät setzen die Zähler zurück. Ein Befund ist also ein Grund, den
Verkäufer zu fragen, wann und warum gelöscht wurde, kein Beweis für einen versteckten
Fehler.
:::
