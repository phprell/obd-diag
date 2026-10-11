# Fehlercodes lesen und löschen

## Lesen

```sh
obd-diag scan --port /dev/ttyUSB0
```

`scan` setzt den Adapter zurück, lässt ihn das Fahrzeugprotokoll suchen, misst die
Bordspannung (Warnung unter 11,8 V) und liest drei Arten von Fehlercodes:

| Art | OBD-Dienst | Bedeutung |
| --- | --- | --- |
| gespeichert | Mode 03 | bestätigter Fehler; schaltet ggf. die Motorkontrollleuchte ein |
| ausstehend | Mode 07 | im aktuellen oder letzten Fahrzyklus erkannt, noch nicht bestätigt |
| permanent | Mode 0A | löscht nur das Steuergerät selbst, wenn es den Fehler in Fahrzyklen als behoben sieht |

Zu jedem Code kommt der Klartext aus dem Offline-Katalog: Titel, Erklärung, mögliche
Ursachen mit Wahrscheinlichkeit, Symptome und ein Kostenrahmen. Für Codes ohne
Katalogtext gibt es auf Wunsch eine kurze, ungeprüfte Online-Erklärung
({doc}`fehlercodes-online`). Wie ein Code aus zwei
Bytes entsteht, steht unter {doc}`../technik/dienste`.

Antwortet ein Steuergerät mit „Mode nicht unterstützt“ (`7F 0A 11`), hat es keine Codes
dieser Art. Jede andere Ablehnung ist ein Fehler und wird nie als „keine Codes“ gezeigt
({doc}`../technik/antwortformate`).

## Dringlichkeit

Jeder Code bekommt eine grobe Einordnung in vier Stufen, in der Kommandozeile unter dem
Code, in der Oberfläche als Etikett in der Liste und mit Begründung in der Detailansicht,
im PDF-Bericht beim Code und in der Kurzübersicht, in der CSV als letzte Spalte. Grundlage
sind nur die Art des Codes, die Kontrollleuchte (Readiness, PID 01; bei `scan` nicht
gelesen) und wenige Codes, deren Bedeutung nach SAE J2012 für alle Hersteller gleich ist.
Die erste zutreffende Regel gilt:

| Stufe | Wann |
| --- | --- |
| Möglichst nicht weiterfahren | `P0217` Kühlmittel-Übertemperatur, `P0218` Getriebeöl-Übertemperatur, `P0524` Öldruck zu niedrig |
| Bald in die Werkstatt | Verbrennungsaussetzer `P0300`–`P0312` oder Airbag `B0001`–`B0099`, gespeichert oder permanent; jeder permanente Code; gespeichert bei eingeschalteter Kontrollleuchte |
| Bei Gelegenheit prüfen | gespeichert, Kontrollleuchte aus oder nicht gelesen |
| Beobachten | nur ausstehend |

Das ist keine Ferndiagnose: Ob die Kontrollleuchte blinkt (Aussetzer, die den
Katalysator schädigen), liefert OBD nicht, und herstellerspezifische Codes wie `U1218`
fallen unter die allgemeinen Regeln. Bei spürbaren Auffälligkeiten eine Werkstatt fragen.

## Löschen

:::{warning}
**Löschen ist derzeit gesperrt** (`CLEAR_ENABLED = False` in `services/clear.py`).
`obd-diag clear` bricht ab, ohne den Port zu öffnen, und die Schaltfläche in der
Oberfläche bleibt grau. Freigegeben wird erst, wenn das Lesen am echten Auto geprüft
ist. Der Ablauf unten beschreibt, wie es danach läuft; die Tests prüfen ihn schon
heute vollständig.
:::

Löschen (Mode 04) ist die einzige schreibende Aktion und läuft immer in dieser
Reihenfolge:

```{mermaid}
flowchart TD
    A[Scan: Codes lesen] --> B{Codes da, Zündung an,<br>Motor aus, Spannung ok?}
    B -- ja --> C{Rückfrage bestätigt?}
    C -- ja --> D[Freeze Frame lesen]
    D --> E[Sicherung schreiben]
    E -- liegt auf der Platte --> F[Mode 04 einmal senden]
    F --> G[Kontroll-Scan]
    B -- nein --> X[Abbruch, nichts gesendet]
    C -- nein --> X
    E -- fehlgeschlagen --> X
```

- **Vorbedingungen:** Ist die Drehzahl nicht lesbar, wird abgelehnt. Antworten
  mehrere Steuergeräte (Motor, Getriebe), muss jedes gültig 0 melden. Ist `ATRV` nicht
  lesbar, blockiert nur eine bekannt niedrige Spannung.
- **Sicherung:** Scan-Ergebnis, Freeze Frame (roh und dekodiert), Zeitpunkt, Adapter
  und Protokoll nach `~/.local/share/obd-diag/backups/`. Vorhandene Sicherungen werden
  nie überschrieben.
- **Ablehnung:** `7F 04 22` (Bedingungen nicht erfüllt) bricht mit Meldung ab; die
  Sicherung bleibt. Meldet das Steuergerät `7F 04 78` (Antwort folgt), wartet obd-diag
  bis zu 10 s ohne erneutes Senden. Mode 04 wird nie wiederholt.

Mit dem Löschen gehen auch Freeze Frame und Readiness verloren. Ist der Fehler nicht
behoben, kommen die Codes wieder.
