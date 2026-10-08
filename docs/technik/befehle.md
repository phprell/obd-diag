# Befehlsreferenz

Das ist die vollständige Liste dessen, was obd-diag an den Adapter senden darf, mit
Seite und wörtlichem Zitat aus dem ELM327-Datenblatt (ELM327DSJ, Firmware v2.1;
Seitenzahlen nach der Fußzeile „N of 94“). Alles andere wird vor dem Senden abgewiesen
({doc}`sicherheit`).

Die Tabellen werden beim Bauen der Dokumentation aus
`tests/fixtures/command_spec.yaml` erzeugt. Dieselbe Datei prüfen die Tests gegen die
Freigabeliste im Code (`tests/verification/test_command_spec.py`, erschöpfend über alle
Hex-Befehle) und gegen jeden Befehl, der in irgendeinem Test gesendet wird. Die Liste
hier kann also weder vom Code noch von den Tests abweichen.

Befehle stehen so, wie sie auf der Leitung stehen: Großbuchstaben, keine Leerzeichen,
danach ein CR. Reguläre Ausdrücke (`01[0-9A-F]{2}`) stehen für eine Gruppe von
Befehlen, z. B. `010C`.

```{include} ../_gen/befehle.md
```
