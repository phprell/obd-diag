# Batterie- und Anlasstest

Der Test misst die Bordspannung in drei Momenten und bewertet sie: in Ruhe (Ladezustand),
beim Anlassen (wie tief die Spannung einbricht, das zeigt die Startkraft der Batterie) und
im Leerlauf (ob die Lichtmaschine lädt). Den Motor startet man selbst, sobald obd-diag
dazu auffordert. Gesendet werden nur lesende Befehle ({doc}`../technik/sicherheit`).

```sh
obd-diag battery --port /dev/ttyUSB0
obd-diag battery --demo              # Ablauf ohne Auto ausprobieren
```

In der Oberfläche: *Optionen → Batterie- und Anlasstest …*, dann *Test starten*. Der
Dialog zeigt die Aufforderung groß, dazu die Spannung und die verbleibende Zeit.

## Ablauf

1. **Vorbereiten:** Fahrzeug steht, Gang in P bzw. Leerlauf, Feststellbremse an. Zündung
   an, Motor aus, Licht und Gebläse aus. Läuft der Motor schon, bricht der Test ab.
2. **Ruhe (3 s):** obd-diag liest die Spannung laut Adapter (`ATRV`) etwa 50-mal je
   Sekunde und die Spannung laut Motorsteuergerät (PID 42).
3. **Anlassen:** Aufforderung „Jetzt den Motor starten“. Bis zu 30 s
   (`--start-timeout`) wartet obd-diag; während des Anlassens wird nur die Spannung
   gelesen, so dicht wie möglich.
4. **Laden (10 s):** Motor laufen lassen. Nach 5 s Einschwingen wird 5 s lang die
   Ladespannung gemessen.

Der ganze Test dauert etwa 15 s plus die Zeit bis zum Start.

## Bewertung

| Messung | gut | auffällig | mangelhaft |
| --- | --- | --- | --- |
| Ruhespannung | ab 12,2 V | 11,8 bis 12,2 V | unter 11,8 V |
| Tiefstwert beim Anlassen | ab Grenze + 0,4 V | Grenze bis + 0,4 V | unter der Grenze |
| Ladespannung im Leerlauf | 13,5 bis 15,0 V | 12,8 bis 13,5 V | unter 12,8 oder über 15,0 V |

- **Ruhe:** Eine 12-V-Bleibatterie hat in Ruhe etwa 12,6 V, wenn sie voll ist, 12,4 V zu
  drei Vierteln und 12,2 V halb geladen; unter 12,0 V ist sie weitgehend leer. Mit
  Zündung an ziehen die Steuergeräte Strom, die Spannung liegt daher etwas tiefer als
  ganz ohne Last. Kurz nach einer Fahrt zeigt sie zu viel (Oberflächenladung):
  Aussagekräftig ist der Test nach einigen Stunden Standzeit.
- **Anlassen:** Die Grenze stammt vom klassischen Belastungstest, bei dem eine Batterie
  bei 21 °C nicht unter 9,6 V fallen darf. Bei Kälte gilt eine tiefere Grenze (16 °C
  9,5 V, 10 °C 9,4 V, 4 °C 9,3 V, −1 °C 9,1 V, −7 °C 8,9 V, −12 °C 8,7 V, −18 °C 8,5 V).
  obd-diag nimmt die Kühlmitteltemperatur vor dem Start als Batterietemperatur; bei
  warmem Motor ist sie höher und die Grenze damit die strengste. Die ersten 0,1 s des
  Anlassens (Einschaltstrom des Anlassers) zählen nicht. Ein zu tiefer Wert kann auch
  am Anlasser oder an Kabeln liegen.
- **Laden:** Fahrzeuge mit Batteriemanagement (Start-Stopp, geregelte Lichtmaschine)
  laden oft bewusst niedriger; 12,8 bis 13,5 V sind dort normal. Unter 12,8 V lädt die
  Lichtmaschine nicht, über 15,0 V lädt der Regler zu hoch.

Die Werte sind Richtwerte für 12-V-Bleibatterien (auch AGM und EFB), keine Angabe des
Fahrzeugherstellers. Das Gesamturteil ist die schlechteste der drei Bewertungen.

## Adapter und Steuergerät

Der Adapter misst die Spannung an Pin 16 der Diagnosebuchse, oft hinter einer
Schutzdiode. Am Mercedes A 180 d ({doc}`erster-test`) zeigte er stets etwa 0,8 V weniger
als das Motorsteuergerät. Liefert das Steuergerät seine Spannung (PID 42), rechnet
obd-diag die Abweichung in Ruhe auf alle Adapter-Werte auf und nimmt Ruhe- und
Ladespannung direkt vom Steuergerät; der Hinweis unter dem Ergebnis nennt die Korrektur.

Warum nicht nur das Steuergerät: Jede Anfrage ans Fahrzeug dauert mit der Mindestpause von
50 ms über 0,1 s, `ATRV` am FORScan-Adapter nur etwa 15 ms. Das Anlassen ist oft nach
einer Sekunde vorbei. `ATRV` ist ein Adapter-Befehl und erreicht das Fahrzeug nicht.

Bricht die Spannung so tief ein, dass der Adapter neu startet, richtet obd-diag ihn neu
ein und vermerkt das; der Anlasswert gilt dann als auffällig.
