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
