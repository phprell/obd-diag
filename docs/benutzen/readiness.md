# Readiness-Assistent

Nach dem Löschen der Fehlercodes, einem Batteriewechsel oder einer Reparatur sind die
Eigendiagnosen des Abgassystems (Readiness-Tests, Monitore) zurückgesetzt und laufen erst
bei passenden Fahrten wieder. Der Readiness-Assistent zeigt, welche Tests noch offen sind,
was davon in der laufenden Fahrt schon geklappt hat und welche Art von Fahrt die übrigen
typischerweise abschließt.

```sh
obd-diag readiness --port /dev/ttyUSB0
obd-diag readiness --demo           # Beispielfahrzeug, ohne Auto
obd-diag readiness --port /dev/ttyUSB0 --json
```

In der Oberfläche steht der Assistent im Reiter *Readiness* unter den Monitoren. Nach
„Verbinden & Scannen“ zeigt er zu jedem offenen Test einen Fahrhinweis; die Schaltfläche
*Stand dieser Fahrt lesen* fragt zusätzlich den Stand der laufenden Fahrt ab, ohne die
ganze Diagnose zu wiederholen.

## Zwei Stände: seit dem Löschen und in dieser Fahrt

| Abfrage | Bedeutung | wird zurückgesetzt |
| --- | --- | --- |
| Mode 01 PID 01 | Stand seit dem Löschen der Fehlercodes | beim Löschen (Mode 04) |
| Mode 01 PID 41 | Stand in diesem Fahrzyklus | bei jedem Einschalten der Zündung |

Je Test nennt PID 41 drei Zustände: *abgeschlossen*, *noch offen* (der Test darf laufen,
ist aber nicht fertig) und *ausgesetzt* (die Bedingungen zum Start waren in dieser Fahrt
nicht erfüllt, z. B. Temperatur oder Tankfüllung). Weil der Stand dieser Fahrt beim
nächsten Start von vorn beginnt, am besten am Ende einer Fahrt bei laufendem Motor
prüfen. Meldet das Fahrzeug PID 41 nicht, zeigt der Assistent nur den Stand seit dem
Löschen.

## Fahrhinweise

Die Hinweise beschreiben typische Fahrten je Test, getrennt für Otto- und Dieselmotoren,
zum Beispiel für den Abgassensor beim Diesel: etwa 20 Minuten gleichmäßig über Land oder
Autobahn mit heißem Abgassystem, dazwischen Schubphasen (Gas wegnehmen, ohne zu bremsen).
Das sind Erfahrungswerte, keine Norm: SAE J1979 legt nur fest, wie der Status gemeldet
wird. Wann ein Test läuft, bestimmt jeder Hersteller selbst, und oft braucht es mehrere
Fahrten, darunter einen Kaltstart nach mehreren Stunden Standzeit. Sind Fehlercodes
gespeichert, laufen manche Tests erst, wenn die Ursache behoben ist.

Wie bei der Readiness selbst gilt: Das ist keine AU-Bewertung. Zur Abgasuntersuchung
gehört immer auch eine Messung am Endrohr.

## Was gesendet wird

Nur lesende Anfragen: Adapter initialisieren, Protokoll aushandeln (`0100`), dann `0101`
und `0141`. Beide stehen in der {doc}`../technik/befehle`; Aufbau der Antworten in
{doc}`../technik/dienste`. Im Demo-Modus ({doc}`demo`) ist der Abgassensor offen und in
dieser Fahrt noch nicht abgeschlossen; die Antwort auf `0141` ist dort ein erfundener
Beispielwert, weil sie am echten Auto nicht aufgezeichnet wurde.
