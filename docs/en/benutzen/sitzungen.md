# Sessions, report and trace

## Full diagnosis

```sh
obd-diag diagnose --port /dev/ttyUSB0                   # table
obd-diag diagnose --port /dev/ttyUSB0 --save            # save session, path on stderr
obd-diag diagnose --port /dev/ttyUSB0 --pdf report.pdf --csv codes.csv
obd-diag diagnose --port /dev/ttyUSB0 --online-vin      # also look up the VIN at NHTSA vPIC
obd-diag diagnose --port /dev/ttyUSB0 --online-codes    # explain codes without catalog text online
```

`diagnose` reads in one pass, read-only:

1. Scan (adapter, protocol, battery voltage, codes from mode 03/07/0A). If it fails,
   `diagnose` aborts.
2. Readiness (mode 01 PID 01): check engine light, reported number of codes, engine
   type (petrol/diesel) and per monitor “complete”, “incomplete” or “not supported”,
   plus “All tests complete: yes/no”.
3. Freeze frame (mode 02, frame 00): triggering code, load, coolant temperature, engine
   speed, speed.
4. VIN (mode 09 PID 02) with offline decoding ({doc}`../technik/fin`).

If the vehicle does not answer one of parts 2 to 4, only that part is missing.

:::{note}
“All tests complete” is deliberately **not an emissions test verdict**. In Germany,
since 2018 the periodic emissions test (AU) again includes a tailpipe measurement for
all OBD vehicles; which incomplete monitors are tolerated depends on the vehicle and
the test equipment.
:::

## Sessions

A session (scan with plain texts, readiness, freeze frame, VIN, time) is saved as JSON
under `~/.local/share/obd-diag/sessions/session-YYYYMMDD-HHMMSS.json`. Existing files
are never overwritten (then `…-2.json`). The file carries
`"format": "obd-diag-session"` and `"version": 1`; loading rejects foreign or newer
formats.

The session is language-neutral apart from the trouble code texts: those are stored in
the language of the scan. Everything else (monitor names, labels) is shown in the
current language when the session is opened or exported.

## PDF report and CSV

```sh
obd-diag export ~/.local/share/obd-diag/sessions/session-20261007-143205.json \
    --pdf report.pdf --csv trouble-codes.csv
```

- **PDF** (A4, in the current language): vehicle, adapter, protocol, battery voltage,
  summary, readiness, trouble codes with explanation, causes, symptoms and cost range,
  freeze frame. Font: DejaVu Sans, Liberation Sans or Noto Sans if installed
  (embedded), otherwise Helvetica.
- **CSV**: one row per trouble code (code, type, title, description, causes, symptoms,
  MIL, emissions-related, repair effort, cost, date, VIN). UTF-8 with BOM; in English
  with `,` as separator, in German with `;`, so that a spreadsheet in that language
  opens it correctly with a double click.

## Trace (`--trace`)

With `--trace`, all commands that use the adapter record every sent and received line
with a timestamp:

```sh
obd-diag diagnose --port /dev/ttyUSB0 --trace            # ~/.local/share/obd-diag/traces/
obd-diag diagnose --port /dev/ttyUSB0 --trace car.log    # own file
```

```text
# obd-diag 0.0.1 Mitschnitt 2026-10-07T16:09:33+02:00 /dev/ttyUSB0 38400 Baud
    0.000 >> ATZ\r
    0.508 << ATZ\r\r\rELM327 v1.5\r\r>
    3.013 >> 03\r
    3.016 << 00A\r0: 430404200133\r1: 0300C100\r\r>
```

`>>` is sent, `<<` received, `!!` an error; control characters appear as `\r` or
`\xNN`. The header line is part of the file format and stays German (“Mitschnitt” =
trace). How to read the response in the last line is explained in
{doc}`../technik/antwortformate`. In the user interface, “Options → Record adapter
trace” switches this on for every action. The trace may contain the VIN and stays
local.
