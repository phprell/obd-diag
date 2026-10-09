# Erster Test am Auto

Der erste Test liest nur und schneidet alles mit, damit Ausgabe und Mitschnitt danach
geprüft werden können. Am 2026-10-09 lief er an einem Mercedes A 180 d (W177); was
dabei auffiel, steht unten unter „Ergebnis am W177“.

:::{note}
Ziel des ersten Tests ist ein Mercedes A 180 d (W177) mit dem Adapter FORScan
ELMconfig USB (Schalter auf HS-CAN, {doc}`adapter`).
:::

## Ablauf

1. Motor aus, Zündung an. Bei Start-Stopp-Knopf: drücken, ohne auf die Bremse zu
   treten.
2. Adapter einstecken und finden:

   ```sh
   obd-diag ports
   obd-diag info --port /dev/ttyUSB0
   ```

3. Vollständige Diagnose, nur lesend, mit Mitschnitt:

   ```sh
   obd-diag diagnose --port /dev/ttyUSB0 --save --trace
   ```

   Die Sitzung landet unter `~/.local/share/obd-diag/sessions/`, der Mitschnitt unter
   `~/.local/share/obd-diag/traces/`.
4. Optional mit laufendem Motor, im Stand:

   ```sh
   obd-diag live --port /dev/ttyUSB0 --duration 30 --record --trace
   ```

5. `clear` bleibt gesperrt. Erst wenn Ausgabe und Mitschnitt geprüft sind, wird
   Löschen freigegeben, und aus dem Mitschnitt wird mit `ReplayTransport` ein
   Regressionstest ({doc}`../entwickeln/testen`).

## Vorsicht, unabhängig von der Software

obd-diag sendet nur lesende Anfragen, eine nach der anderen ({doc}`../technik/sicherheit`).
Was trotzdem schiefgehen kann, liegt am Drumherum:

- **Adapterschalter:** vor dem Einstecken auf HS-CAN stellen; bei Zündung aus
  einstecken und abziehen. Findet das Tool nichts („UNABLE TO CONNECT“), nicht mit dem
  Schalter experimentieren, sondern aufhören und den Mitschnitt ansehen.
- **Batterie:** Zündung an ohne Motor zieht Strom. Den Test kurz halten (eine Diagnose
  dauert Sekunden), danach Zündung aus. Unter 12 V lieber mit laufendem Motor oder
  Ladegerät testen.
- **Adapter abziehen:** er hängt an Dauerplus (Pin 16) und kann über Nacht die Batterie
  leeren oder Steuergeräte wachhalten.
- **Kabel und Motor:** Kabel weg von den Pedalen, Live-Daten im Stand, Motor nur im
  Freien laufen lassen.
- **Adapter-Firmware:** Was im Adapter passiert, kann obd-diag nicht kontrollieren.
  Nach Datenblatt sendet ein ELM327 ans Auto nur, was angefragt wird; billige Klone
  halten sich nicht immer daran und antworten mitunter falsch. Dafür ist der
  Mitschnitt da.

## Was zurückgeschickt wird

- die Ausgabe von `diagnose`,
- die Sitzungsdatei (`session-….json`),
- die Mitschnitte (`trace-….log`).

Der Mitschnitt enthält die FIN. Er bleibt lokal, bis er bewusst weitergegeben wird.

## Wenn etwas schiefgeht

Wird die Verbindung mitten in der Abfrage unterbrochen (Adapter abgezogen, Stecker vom
Auto ab), bricht obd-diag mit „Verbindung zu … unterbrochen“ bzw. „keine Antwort von
…“ ab. Danach wird nichts mehr gesendet, und eine halbe Diagnose wird nicht
gespeichert. Weitere Fälle: {doc}`probleme`.

Die genormte Diagnose sieht nur abgasrelevante Steuergeräte (Motor, Getriebe).
Airbag, ABS und Komfortelektronik brauchen herstellerspezifische Diagnose; die ist
noch nicht eingebaut.

## Ergebnis am W177

Protokoll ISO 15765-4 (CAN 29/500), vier Steuergeräte antworten. Gelesen wurden ein
gespeicherter Code (U1218, herstellerspezifisch, daher ohne Text im Katalog),
Readiness (Diesel, nur der Abgassensor offen), Freeze Frame und FIN; Live-Daten liefen
im Stand. Was jede Zeile des Mitschnitts bedeutet, steht unter
{doc}`../technik/mitschnitt-w177`.

### Ablauf am 2026-10-09

| Schritt | Ergebnis |
| --- | --- |
| `obd-diag ports` | `/dev/ttyUSB0` gefunden, Zugriff über die Gruppe `uucp` klappt |
| `obd-diag info` | erster Start direkt nach dem Einstecken: Abbruch, weil der Adapter das erste `ATZ` mit `?` ablehnte; zweiter Start: ELM327 v1.5, 11,5 V |
| `obd-diag diagnose --save --trace` | **fehlgeschlagen:** Protokollsuche endet mit `UNABLE TO CONNECT`, weil die Zündung noch aus war. Abgebrochen, gesendet waren nur AT-Befehle und ein `0100` |
| Zündung an, `diagnose` erneut | vollständig: CAN 29/500, vier Steuergeräte, U1218, Readiness, Freeze Frame, FIN; 11,2 V laut Adapter |
| `obd-diag live --duration 20 --record --trace` | vier Runden im Abstand von 5 s (Drosselung wegen 11,2 V), Drehzahl 0, Kühlmittel 19 °C, Steuergerät 12,0 V |
| Ende | Zündung aus, Adapter abgezogen; nichts gelöscht |

Die Bordspannung laut Adapter fiel in den wenigen Minuten mit Zündung an von 11,5 V
(`info`) auf 11,2 V (`diagnose`); ein PDF-Bericht kurz davor zeigt 11,0 V. Der Test
sollte also wirklich kurz bleiben oder mit Ladegerät laufen.

`UNABLE TO CONNECT` ist mit Zündung aus der Normalfall, kein Fehler von Adapter oder
Tool: Der Adapter selbst antwortet (Dauerplus an Pin 16), die Steuergeräte schlafen.
obd-diag meldet dann „Kein Steuergerät antwortet. Zündung einschalten (der Motor darf
aus bleiben), bei Adaptern mit MS-/HS-CAN-Schalter HS-CAN wählen und erneut
versuchen.“ (seit PR #8). Also Zündung an und neu starten; steht der Schalter schon auf
HS-CAN, nicht weiter daran probieren.

### Behoben

Behoben wurden danach (PR #7):

- Das erste `ATZ` nach dem Einstecken beantwortete der Adapter mit `?`; es wird jetzt
  einmal wiederholt.
- Drei Steuergeräte melden im Freeze Frame `00 00` (kein Freeze Frame), eines U1218;
  angezeigt wird jetzt der Code, nicht „unbekannt“.
- Der Adapter maß 11,2 V, das Motorsteuergerät 12,0 V; bei niedrigem `ATRV` zählt
  jetzt die Steuergerätespannung (PID 42).
- Stelle 10 der FIN ist bei Mercedes die Lenkung, kein Modelljahr („2001“ war falsch).

So sieht `obd-diag diagnose` mit diesem Stand für den Mitschnitt des W177 aus
(abgespielt über `ReplayTransport`, FIN-Seriennummer geschwärzt, ohne gebauten
Fehlercode-Katalog):

```text
Fahrzeug:
  FIN:        WDD1770031J000000
  Prüfziffer: nicht vorgeschrieben (weicht ab)
  Hersteller: Mercedes-Benz
  Land:       Deutschland

Adapter:      ELM327 v1.5
Protokoll:    ISO 15765-4 (CAN 29/500)
Bordspannung: 12.0 V

Gespeichert:
  U1218  (keine Beschreibung im Katalog)

Readiness (Diesel-Motor):
  Kontrollleuchte (MIL): aus
  Gemeldete Fehlercodes: 1
  Verbrennungsaussetzer:    nicht unterstützt
  Kraftstoffsystem:         abgeschlossen
  Komponenten:              abgeschlossen
  NMHC-Katalysator:         abgeschlossen
  NOx-Nachbehandlung (SCR): abgeschlossen
  Ladedruck:                abgeschlossen
  Abgassensor:              nicht abgeschlossen
  Partikelfilter:           abgeschlossen
  Abgasrückführung:         abgeschlossen
  Alle Tests abgeschlossen: nein

Freeze Frame (ausgelöst durch U1218):
  Motorlast:             0 %
  Kühlmitteltemperatur:  37 °C
  Drehzahl:              0 1/min
  Geschwindigkeit:       0 km/h
```

Am Auto selbst stand noch „Bordspannung 11.2 V“ mit Warnung, Modelljahr „2001“ und ein
Freeze Frame ohne auslösenden Code. Der AU-Hinweis unter der Readiness ist hier
gekürzt.

### Mitschnitte weitergeben

Sitzung, PDF-Bericht und Mitschnitt enthalten die vollständige FIN. Bevor sie in ein
Ticket, einen Chat oder ins Repository gehen:

- im Mitschnitt die Antwort auf `0902` schwärzen: Bei CAN stehen die Stellen 11 bis 17
  der FIN als ASCII-Hex im Frame `2:`; dessen letzte sechs Bytes durch `30` (Zeichen
  `0`) ersetzen, wie in `tests/fixtures/traces/mercedes_w177/diagnose.log`,
- in der Sitzungsdatei das Feld `vin` (Klartext) ebenso und, falls die Online-Abfrage
  an war, den Abschnitt `online` leeren,
- den PDF-Bericht nicht weitergeben, sondern aus der geschwärzten Sitzung neu erzeugen
  (`obd-diag export`, {doc}`sitzungen`).

Zeitstempel, Port und Adapter-Kennung im Kopf des Mitschnitts sind unkritisch.

