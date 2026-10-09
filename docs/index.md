# obd-diag

OBD-II-Diagnose für Linux über ELM327-kompatible Adapter, als Kommandozeile und als
Desktop-Oberfläche. obd-diag liest Fehlercodes und erklärt sie auf Deutsch, zeigt
Readiness, Freeze Frame, FIN und Live-Daten und schneidet auf Wunsch die gesamte
Kommunikation mit dem Adapter mit.

:::{important}
**Nur lesend, Löschen gesperrt.** Der einzige schreibende Befehl ist das Löschen der
Fehlercodes (Mode 04); es ist ausgeschaltet, bis es ausdrücklich freigegeben wird. Das
Lesen ist bisher an einem Fahrzeug geprüft (Mercedes A 180 d, W177, siehe
{doc}`benutzen/erster-test`). Die Antwortverarbeitung ist ohne
Hardware gegen das ELM327-Datenblatt, echte Mitschnitte anderer Programme und
python-OBD geprüft ({doc}`entwickeln/testen`).
:::

## Was obd-diag kann

| Funktion | OBD-Dienst | Seite |
| --- | --- | --- |
| Fehlercodes lesen (gespeichert, ausstehend, permanent) mit Klartext | Mode 03, 07, 0A | {doc}`benutzen/fehlercodes` |
| Readiness (Monitore, Motorkontrollleuchte) | Mode 01 PID 01 | {doc}`benutzen/oberflaeche` |
| Freeze Frame zum auslösenden Fehlercode | Mode 02 | {doc}`technik/dienste` |
| FIN lesen und offline dekodieren | Mode 09 PID 02 | {doc}`technik/fin` |
| Live-Daten: 116 Werte aus 82 PIDs, CSV-Aufzeichnung | Mode 01 | {doc}`benutzen/live-daten` |
| Sitzungen speichern, PDF-Bericht, CSV | – | {doc}`benutzen/sitzungen` |
| Fehlercodes löschen (gesperrt) | Mode 04 | {doc}`benutzen/fehlercodes` |

Was obd-diag nicht kann: Steuergeräte außerhalb der genormten Abgasdiagnose (Airbag,
ABS, Komfort), Codierung, Flashen, Servicefunktionen. Das ist eine bewusste
Entscheidung ({doc}`adr/0002-nur-lesend`).

## Wo anfangen

- **Am Auto:** {doc}`benutzen/installation`, dann {doc}`benutzen/adapter` und
  {doc}`benutzen/erster-test`.
- **Verstehen, was über die Leitung geht:** {doc}`technik/elm327` und
  {doc}`technik/antwortformate`, jeder Befehl mit Datenblatt-Seite in der
  {doc}`technik/befehle`.
- **Mitentwickeln:** {doc}`entwickeln/regeln` und {doc}`entwickeln/testen`.

```{toctree}
:caption: Benutzen
:maxdepth: 1
:hidden:

benutzen/installation
benutzen/adapter
benutzen/erster-test
benutzen/oberflaeche
benutzen/cli
benutzen/fehlercodes
benutzen/live-daten
benutzen/sitzungen
benutzen/probleme
```

```{toctree}
:caption: Wie es funktioniert
:maxdepth: 1
:hidden:

technik/architektur
technik/elm327
technik/antwortformate
technik/dienste
technik/befehle
technik/pids
technik/sicherheit
technik/fin
technik/quellen
```

```{toctree}
:caption: Entwickeln
:maxdepth: 1
:hidden:

entwickeln/regeln
entwickeln/testen
entwickeln/entscheidungen
entwickeln/roadmap
api/index
```
