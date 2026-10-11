# Command line

All functions are available under one command `obd-diag` with subcommands. Commands
that use the adapter take `--port` (default `/dev/ttyUSB0`), `--baud` (default 38400)
and `--trace` for a trace ({doc}`sitzungen`). `--lang en` or `--lang de` sets the
language of all output ({doc}`sprache`).

| Command | Purpose | sends to the vehicle |
| --- | --- | --- |
| `obd-diag ports` | list adapters | nothing |
| `obd-diag info` | adapter identification and battery voltage | nothing (only adapter commands) |
| `obd-diag adapter` | adapter check: version, command set, UDS suitability ({doc}`adapter`) | nothing (only adapter commands) |
| `obd-diag scan` | read trouble codes | Mode 01, 03, 07, 0A |
| `obd-diag diagnose` | trouble codes, readiness, freeze frame, VIN, used-car check | Mode 01, 02, 03, 07, 09, 0A |
| `obd-diag readiness` | readiness assistant: incomplete tests since clearing and during this drive ({doc}`readiness`) | Mode 01 |
| `obd-diag used-car` | used-car check: cleared recently? ({doc}`gebrauchtwagen`) | Mode 01, 03, 07, 0A |
| `obd-diag vin` | read the VIN or decode one that is typed in | Mode 09 |
| `obd-diag live` | show and record live data | Mode 01 |
| `obd-diag export` | saved session as PDF/CSV | nothing |
| `obd-diag compare` | compare two saved sessions ({doc}`sitzungen`) | nothing |
| `obd-diag clear` | clear trouble codes (locked) | Mode 04 |

## Examples

```sh
obd-diag adapter --port /dev/ttyUSB0               # what can the adapter really do?
obd-diag scan --port /dev/ttyUSB0                  # table, language like the system
obd-diag scan --port /dev/ttyUSB0 --lang en --json # English, machine-readable
obd-diag diagnose --port /dev/ttyUSB0 --save --pdf report.pdf
obd-diag used-car --port /dev/ttyUSB0               # trouble codes cleared recently?
obd-diag vin WVWZZZ1KZ6W123456                     # decode only, without an adapter
obd-diag live --port /dev/ttyUSB0 --pids rpm,speed --interval 0.5 --record
obd-diag export ~/.local/share/obd-diag/sessions/session-20261007-143205.json --pdf report.pdf
```

With `--json` the standard output stays pure JSON; notes and paths of saved files go to
the error output. Exit code 0 means success, 1 an error.

## All commands and options

The following reference is generated from the program itself when the documentation
is built and is therefore always up to date with the code.

```{argparse}
:module: obd_diag.cli
:func: build_parser
:prog: obd-diag
```
