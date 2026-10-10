# obd-diag

OBD-II diagnostics for Linux with ELM327-compatible adapters, as a command line tool
and as a desktop application. obd-diag reads trouble codes and explains them in
English or German, shows readiness, freeze frame, VIN and live data, and can record
the entire communication with the adapter.

:::{important}
**Read-only, clearing locked.** The only writing command is clearing the trouble codes
(mode 04); it is switched off until it is explicitly enabled. Reading has so far been
verified on one vehicle (Mercedes A 180 d, W177, see {doc}`benutzen/erster-test`).
Without hardware, the response handling is verified against the ELM327 datasheet, real
traces from other programs and python-OBD ({doc}`entwickeln/testen`).
:::

## What obd-diag can do

| Function | OBD service | Page |
| --- | --- | --- |
| Read trouble codes (stored, pending, permanent) with plain text | Mode 03, 07, 0A | {doc}`benutzen/fehlercodes` |
| Explain codes without a catalog text online (optional, unverified) | – | {doc}`benutzen/fehlercodes-online` |
| Readiness (monitors, check engine light) | Mode 01 PID 01 | {doc}`benutzen/oberflaeche` |
| Freeze frame for the triggering trouble code | Mode 02 | {doc}`technik/dienste` |
| Read the VIN and decode it offline | Mode 09 PID 02 | {doc}`technik/fin` |
| Live data: 116 values from 82 PIDs, CSV recording | Mode 01 | {doc}`benutzen/live-daten` |
| Save sessions, PDF report, CSV | – | {doc}`benutzen/sitzungen` |
| Clear trouble codes (locked) | Mode 04 | {doc}`benutzen/fehlercodes` |

What obd-diag cannot do: control units outside the standardised emissions diagnostics
(airbag, ABS, comfort), coding, flashing, service functions. This is a deliberate
decision ({doc}`adr/0002-nur-lesend`).

## Where to start

- **At the car:** {doc}`benutzen/installation`, then {doc}`benutzen/adapter` and
  {doc}`benutzen/erster-test`.
- **Understand what goes over the wire:** {doc}`technik/elm327` and
  {doc}`technik/antwortformate`, every command with its datasheet page in the
  {doc}`technik/befehle`. A real trace, explained line by line:
  {doc}`technik/mitschnitt-w177`.
- **Contribute:** {doc}`entwickeln/regeln` and {doc}`entwickeln/testen`.

This documentation exists in English and German (link at the top of the sidebar). The
program itself speaks both languages: {doc}`benutzen/sprache`.

```{toctree}
:caption: Usage
:maxdepth: 1
:hidden:

benutzen/installation
benutzen/adapter
benutzen/erster-test
benutzen/oberflaeche
benutzen/cli
benutzen/sprache
benutzen/fehlercodes
benutzen/fehlercodes-online
benutzen/live-daten
benutzen/sitzungen
benutzen/probleme
```

```{toctree}
:caption: How it works
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
technik/mitschnitt-w177
technik/quellen
```

```{toctree}
:caption: Development
:maxdepth: 1
:hidden:

entwickeln/regeln
entwickeln/testen
entwickeln/entscheidungen
entwickeln/roadmap
entwickeln/aenderungen
api/index
```
