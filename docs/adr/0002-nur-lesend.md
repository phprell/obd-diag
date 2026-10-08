# ADR 0002: Nur lesend, mit Freigabeliste im Treiber

Status: angenommen (2026-10-07)

## Kontext
Das Tool wird an echte Fahrzeuge angeschlossen. Ein falscher Befehl kann Steuergeräte
verstellen: Routinen starten (z. B. Partikelfilter-Regeneration), Aktoren ansteuern,
Werte schreiben oder flashen. Herstellertools (Xentry, ODIS, ISTA) sichern solche
Vorgänge mit Vorbedingungen, Sicherheitszugang und definierten Abläufen ab; das kann
dieses Projekt nicht leisten. Der Nutzen liegt im Verstehen: Fehler lesen, erklären,
dokumentieren.

## Entscheidung
- Das Tool ist standardmäßig nur lesend. Einziger schreibender OBD-Befehl ist Mode 04
  (Fehlercodes löschen), nur über `services/clear.py` mit Vorbedingungen (Zündung an,
  Drehzahl 0 bei allen antwortenden Steuergeräten, Bordspannung nicht zu niedrig),
  Rückfrage und vorheriger Sicherung.
- `Elm327.command` ist der einzige Weg zum Adapter und prüft jeden Befehl gegen eine
  Freigabeliste (Adapter-Befehle und lesende OBD-Anfragen). Alles andere wird vor dem
  Senden abgewiesen (`ForbiddenCommandError`, bewusst kein `ElmError`, damit es kein
  Aufrufer als „Angabe fehlt“ abfängt). `04` ist nur in `with elm.allow_clear()`
  freigeschaltet, und das nutzt nur `clear_dtcs`.
- Keine Codierung, kein Flashen, keine Servicefunktionen (UDS `10`, `11`, `14`, `27`,
  `2E`, `2F`, `31`, `34`–`37`, `85`), auch nicht später mit UDS. Eine Erweiterung um
  lesende UDS-Dienste (`19`, `22`, `3E`) gibt die Freigabeliste einzeln frei.

## Folgen
- Neue Befehle müssen bewusst in die Freigabeliste; der Test dafür zeigt jede Änderung.
- Tests sichern die Grenze ab: Hypothesis über beliebigen Text, exakte Befehlsfolgen
  je Funktion, Löschen mit beliebig kaputten Antworten, Aufrufgraph per AST,
  Mutationstests für Treiber und Löschen. Ein Test, der dabei stört, wird nicht
  umgangen, sondern ernst genommen.
- Servicefunktionen bleiben Werkstatt oder lizenzierten Testern vorbehalten.
