# Erster Test am Auto

obd-diag ist noch nicht an einem echten Fahrzeug getestet. Der erste Test liest nur
und schneidet alles mit, damit Ausgabe und Mitschnitt danach geprüft werden können.

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
