# obd-diag

**English** · [Deutsch](README.de.md)

OBD-II diagnostics for Linux with ELM327-compatible adapters: read and explain trouble
codes, freeze frame, readiness, VIN, live data with recording, as a command line tool
and a desktop application (PySide6/QML), in English or German. Read-only by default:
the only writing command is clearing the trouble codes, and only after checks and a
backup.

> **Clearing is disabled for now** (`CLEAR_ENABLED` in `services/clear.py`). Reading
> has been verified on a real vehicle since 2026-10-09; clearing will only be enabled
> after a deliberate decision. Until then `obd-diag clear` aborts without opening the
> port, and the button in the user interface stays grey. The procedure below describes
> how it works once enabled.

Design and roadmap: [OBD-Diagnose – Designvorschlag](https://claude.ai/code/artifact/de949a8a-4f58-41b4-a629-6b9d238bdac7) (German)

Full documentation (usage, how it works, development):
<https://phprell.github.io/obd-diag/en/>, German at <https://phprell.github.io/obd-diag/>
(sources in `docs/en/` and `docs/`, see [Documentation](#documentation)).

## Status

- **v0.1** (roadmap steps 1–4, in `main`): trouble codes (mode 03/07/0A) with plain
  text, readiness, freeze frame, VIN with offline decoding, safe clearing, diagnosis
  sessions as JSON, PDF report and CSV, adapter trace, user interface with light and
  dark theme.
- **v0.2** (version 0.2.0, changes in [CHANGELOG.md](CHANGELOG.md), German): live data
  according to SAE J1979 (116 values from 82 PIDs, including all oxygen sensors), CSV
  recording, tab “Live data” and `obd-diag live`.
- **English and German** (not released yet, see CHANGELOG): user interface
  (*Options → Sprache / Language*), command line (`--lang en|de`), PDF report, CSV,
  trouble code texts and documentation. The default is the system language: German if
  it is German, otherwise English.
- **Tested on a real car** (2026-10-09, Mercedes A 180 d W177, adapter FORScan
  ELMconfig, CAN 29 bit/500 kbit/s, four control units): `info`, `diagnose` and `live`
  read correctly. Four findings from the test are fixed (first `ATZ` with `?`, freeze
  frame from the right control unit, voltage cross-checked via PID 42, no model year
  for Mercedes), plus a clear hint on `UNABLE TO CONNECT` (ignition off). The traces
  run as a regression test (`tests/verification/test_real_car.py`, VIN redacted).
  Details: “First test at the car” below.
- Without hardware, the response handling is additionally verified against the
  datasheet, third-party traces and python-OBD (see “Development”).
- Planned (v0.3+): Bluetooth LE, community profiles, fault memory of all control units
  via UDS (read-only), see the design document.

## Documentation

The website (Sphinx with MyST, Furo and Mermaid) is in `docs/en/` in English and in
`docs/` in German, with the same file names: installation, adapter, first test at the
car, user interface, language, CLI reference, trouble codes, live data, common
problems; plus how the tool talks to the ELM327 (response formats with byte examples,
command reference with datasheet quotes, PID formulas, safety concept, the W177 trace
line by line) and the API reference.

```sh
uv run --group docs sphinx-build -W docs docs/_build          # then docs/_build/index.html
uv run --group docs sphinx-build -W docs/en docs/_build/en    # English
```

`tools/docs_tables.py` generates the command and PID tables from
`tests/fixtures/command_spec.yaml` and `PIDS` when building, the CLI options come from
`cli.build_parser`, and `tests/unit/test_docs_examples.py` checks the byte examples.
`uv run python tools/docs_screenshots.py` creates the pictures of the user interface
(emulator, no screen, English with `--lang en`); they are checked in. Every page exists
in both languages; `tests/unit/test_docs_i18n.py` checks that the structure matches.

CI builds the website for every PR (HTML as the artifact “dokumentation” on the run)
and publishes it on every push to `main` at <https://phprell.github.io/obd-diag/>
(`.github/workflows/docs.yml`, GitHub Pages with source “GitHub Actions”).

### Translations

Texts in the code are German and go through `tr("…")` from `obd_diag.i18n` (`qsTr` in
QML); the English ones are in `src/obd_diag/locale/en.po`.
`uv run python tools/translations.py` adds new texts (`--todo` lists missing ones,
`--fill` enters them). `tests/unit/test_i18n.py` fails while a translation is missing
or a German text is output without `tr`. Comments, docstrings, log messages and commit
messages stay German.

## Development

```sh
uv sync                     # environment incl. dev tools
uv run pytest               # unit and emulator tests
uv run ruff check . && uv run ruff format --check .
uv run mypy
```

Without uv: `python -m venv .venv && .venv/bin/pip install -e '.[gui]' pytest pytest-qt pypdf ruff mypy types-pyserial types-reportlab ELM327-emulator hypothesis pyyaml`.

`tests/verification` checks the response handling without an adapter against real
traces (own ones from the Mercedes W177 in `tests/fixtures/traces/mercedes_w177/`, plus
the ELM327 datasheet and user logs from python-OBD/ELMduino/AndrOBD, sources in
`tests/fixtures/traces/`), against the DTC decoder of python-OBD and with Hypothesis
round-trip and fuzz tests. The live data formulas are compared with python-OBD for
every byte value (`test_pid_differential.py`); the two deviations (PID `32` and `44`)
are justified against J1979 there. python-OBD is GPL-2.0 and only used in tests.

Mutation tests (allowlist, clearing, live data, storage, scan, session, catalog, trace;
configuration under `[tool.mutmut]` in `pyproject.toml`, about two minutes):
`uv run --with mutmut mutmut run`, then `uv run --with mutmut mutmut results`. No
mutant may survive in `services/clear.py`. In `protocol/elm327.py`, `protocol/obd.py`,
`protocol/pids.py` and `services/live.py` only mutants of log and error texts or
equivalent ones (e.g. `"utf-8"` → `"UTF-8"`) survive; none of them changes whether or
how often something is sent. Tests that inspect the source code itself do not run
under mutmut. The folder `mutants/` is only a working copy.

The GUI tests (`tests/ui`) run without a screen (`QT_QPA_PLATFORM=offscreen`, set by
`tests/ui/conftest.py`) and are skipped if PySide6 is missing.

Release: set the version in `pyproject.toml` and `src/obd_diag/__init__.py`, write a
section `## X.Y.Z – date` in `CHANGELOG.md`, and after the merge push the tag `vX.Y.Z`
on `main`. `.github/workflows/release.yml` then checks tag and versions, builds the
catalog, sdist and wheel and creates the GitHub release with the changelog section.

### Trouble code catalog

The plain texts for the trouble codes (English/German, causes, symptoms, cost range)
are stored offline in `src/obd_diag/data/dtc_catalog.sqlite`. The file is not checked
in but built, before the first start and before `uv build`:

```sh
uv run python tools/build_dtc_db.py                     # downloads the data from GitHub
uv run python tools/build_dtc_db.py --source ../OBDex   # or from a local checkout
```

The data source is [OBDex](https://github.com/foerbsnavi/OBDex) (data under
[CC0-1.0](https://creativecommons.org/publicdomain/zero/1.0/), code MIT), pinned to one
commit (`OBDEX_COMMIT` in the script). Source, commit, licence and build time are in the
catalog's `meta` table. A test against the real catalog only runs if it is built.

### Testing without a car

The [ELM327-emulator](https://github.com/Ircama/ELM327-emulator) provides a virtual
serial device. `tools/emulator.py` starts it with standard-compliant responses
(otherwise the emulator leaves out the count byte of trouble codes with CAN and does
not answer the freeze frame according to SAE J1979, see `tests/emulator_patches.py`):

```sh
uv run python tools/emulator.py                    # shows the pty, e.g. /dev/pts/5
uv run python tools/emulator.py --stored P0420,P0300 --pending P0171 --engine-off
uv run obd-diag diagnose --port /dev/pts/5
```

`--engine-off` reports engine speed 0 so that clearing can be tried out. Running
`uv run elm -s car` directly works for `info`, but not for trouble codes.

### Real adapter

```sh
obd-diag info --port /dev/ttyUSB0
```

Supported are ELM327-compatible adapters via USB and classic Bluetooth
(`/dev/rfcomm*`). Bluetooth LE only comes with v0.3. Cheap “v1.5” clones are often
faulty; adapters with a genuine ELM327 or STN chip (e.g. OBDLink) are more reliable.

Permissions: on Arch the group is called `uucp` (Debian/Ubuntu: `dialout`):
`sudo usermod -aG uucp $USER`, or install the udev rule from `packaging/`.

### First test at the car

1. Engine off, ignition on (with a start/stop button: press it without pressing the
   brake).
2. `obd-diag ports` shows the adapter, `obd-diag info --port …` version and battery
   voltage.
3. `obd-diag diagnose --port … --save --trace`: reads everything, read-only, and
   records the communication.
4. Optionally with the engine running: `obd-diag live --port … --duration 30 --record --trace`.
5. `clear` stays locked. The trace becomes a regression test via `ReplayTransport`
   (redact the VIN first, see “Trace”).

On 2026-10-09 this procedure ran on a Mercedes A 180 d (W177): CAN 29/500, four control
units, one stored code (U1218, manufacturer-specific), readiness, freeze frame, VIN and
live data while stationary. The first attempt ended with `UNABLE TO CONNECT` because
the ignition was still off; the tool then says “No control unit responds. Switch the
ignition on (the engine may stay off), select HS-CAN on adapters with an MS/HS-CAN
switch and try again.” The battery voltage according to the adapter dropped to 11.2 V
within a few minutes: keep the test short or connect a charger. The whole procedure and
the trace line by line are in the documentation (“First test at the car”, “Trace from
the W177”).

Caution, independent of the software:

- Set the adapter switch to HS-CAN **before** plugging in, and plug in and unplug with
  the ignition off. If the tool finds nothing (“UNABLE TO CONNECT”), do not experiment
  with the switch; stop and look at the trace.
- Ignition on without the engine draws current from the battery: keep the test short (a
  diagnosis takes seconds), then ignition off. If the battery voltage is below 12 V,
  better test with the engine running or a charger.
- Unplug the adapter after the test: it is connected to permanent positive (pin 16) and
  can drain the battery overnight or keep control units awake.
- Route the cable so that it does not catch on the pedals; live data while stationary,
  run the engine only outdoors.

If the connection is interrupted during a query (adapter unplugged, plug pulled from
the car, Bluetooth gone), the tool aborts with a message (“Connection to … lost” or “no
response from …”). After that nothing more is sent, half a diagnosis is not saved, and
a live recording keeps all complete rounds. If the adapter reports bus errors instead
(ignition off), live data ends after three rounds without response.

The standardised diagnostics only see emissions-related control units (engine,
transmission). Airbag, ABS, comfort electronics etc. need manufacturer-specific
diagnostics; that is not built in yet (planned: read-only via UDS).

### Safety: what the tool can send

The tool should not be able to change anything in the car. For that there is a hard
limit in the code and tests that check it:

- **Allowlist** (`protocol/elm327.py`): `Elm327.command` is the only way to the adapter.
  Only what is listed there gets through: adapter commands (`ATZ`, `ATE0`, `ATRV` …)
  that send nothing to the vehicle, and read OBD requests (`01xx`, `02xx[00]`, `03`,
  `07`, `0A`, `0902`). Everything else is rejected with `ForbiddenCommandError`
  **before** sending, including lower case, spaces or an appended second command.
- **Clearing** (`04`) is only enabled inside `clear_dtcs` and only runs through the
  procedure under “Clearing trouble codes”.
- **No coding, no flashing, no service functions** (routines, actuators, adaptation
  values). This is a deliberate decision (`docs/en/adr/0002-nur-lesend.md`).
- **Pace:** there is only ever one request in flight; the next one only goes out when
  the adapter has reported completion with the prompt `>` (with 7F-78 responses,
  reading continues without sending again). Behind this is a hard minimum gap of 50 ms
  between a response and the next request to the vehicle (`MIN_REQUEST_GAP`), so at
  most 20 requests per second, even if an adapter sends the prompt too early. Live data
  additionally queries at most one round every 0.1 s. After a connection error or a
  missing response nothing more is sent.

This is checked as follows:

- Allowlist with Hypothesis: arbitrary text is either allowed or never sent; for every
  function the exact command sequence is in the test, checked against the datasheet
  and J1979.
- Clearing with arbitrarily broken responses: `04` goes out at most once, and only if
  all preconditions are demonstrably fulfilled and the backup is on disk.
- Live data with arbitrary responses: only reading, never `04`.
- Call graph (AST): only the intended places reach `allow_clear`, `clear_dtcs`,
  `clear_codes`, the transport and pyserial; no sockets, no `os.write`, no
  `eval`/`getattr`.
- Every sent command has exactly the ELM327 format on the line (upper case, digits, one
  CR), also in the emulator tests via the real serial transport.
- Minimum gap with a simulated clock: never a request before the previous response,
  never two requests to the vehicle closer than 50 ms, also after errors and timeouts,
  for a full diagnosis and arbitrary command sequences (Hypothesis).

See `tests/unit/test_command_guard.py`, `test_write_safety.py`, `test_live_safety.py`,
`test_request_gap.py`.

### Reading trouble codes

```sh
obd-diag scan --port /dev/ttyUSB0              # table, language like the system
obd-diag scan --port /dev/ttyUSB0 --lang en    # everything in English
obd-diag scan --port /dev/ttyUSB0 --json       # machine-readable
```

`scan` shows adapter, vehicle protocol and battery voltage (with a warning below
11.8 V) and lists stored (mode 03), pending (mode 07) and permanent (mode 0A) trouble
codes with plain text from the offline catalog. Without the catalog the codes appear
without description (build it with `uv run python tools/build_dtc_db.py`). It only
reads, nothing is cleared.

With `--online-codes` (also for `diagnose`; in the user interface *Options* → “Explain
trouble codes online”, off by default), codes without catalog text get a short,
unverified explanation from [Wal33D/dtc-database](https://github.com/Wal33D/dtc-database)
(MIT) with source and link, plus a link for a web search. Whole files per manufacturer
are downloaded; trouble code and VIN never leave the computer. Details in the
documentation under “Explaining trouble codes online”.

If the adapter (`ATRV`) measures below 11.8 V, the control module voltage (PID 42)
counts if it is plausible: many adapters measure a few tenths of a volt too little
behind a protection diode (on the Mercedes W177: adapter 11.2 V, control unit 12.0 V).
This also applies to clearing and live data.

The emulator sets no codes by default; the tests specify them via the lists
`DTC_STORED`, `DTC_PENDING` and `DTC_PERMANENT` in `elm.obd_message` (see
`tests/integration/test_scan_emulator.py`).

### Finding adapters

```sh
obd-diag ports
```

lists USB serial adapters (`/dev/ttyUSB*`, `/dev/ttyACM*` and other devices with a USB
ID) and bound Bluetooth devices (`/dev/rfcomm*`, e.g. after `sudo rfcomm bind 0 <MAC>`).
Built-in ports (`/dev/ttyS*`) are not listed.

### Clearing trouble codes

**Currently disabled** (see above). The tests still check the procedure completely, so
that it is right when it is enabled.

Technically the tool can only send allowed commands: adapter commands (`AT…`) and read
OBD requests (`01xx`, `02xx00`, `03`, `07`, `0A`, `0902`). `04` (clearing) is only
enabled inside the clearing procedure; everything else is rejected before sending
(`ForbiddenCommandError`).

```sh
obd-diag clear --port /dev/ttyUSB0          # shows the codes and asks for confirmation
obd-diag clear --port /dev/ttyUSB0 --yes    # without asking
```

`clear` is the only writing action (mode 04) and follows fixed rules:

1. Read first: a scan as with `scan`. If there are no stored or pending codes, nothing
   is sent. Mode 04 does not clear permanent codes (mode 0A); they only disappear once
   the control unit sees the fault as fixed over drive cycles.
2. Preconditions: the control unit answers (ignition on), the battery voltage is not
   below 11.8 V (cross-checked with PID 42 if `ATRV` is low) and the engine speed is 0
   (engine off). If several control units answer (e.g. engine and transmission), each
   must validly report 0. If an engine speed cannot be read, clearing is refused as
   well.
3. Confirmation: the codes are listed, and they are only cleared after typing `yes`
   (`ja` in German), or with `--yes`.
4. Backup: scan result, freeze frame (mode 02: triggering code, load, coolant
   temperature, engine speed, speed, raw and decoded), time, adapter and protocol as
   JSON in `$XDG_DATA_HOME/obd-diag/backups/` (default
   `~/.local/share/obd-diag/backups/`). The file is written completely before mode 04
   is sent; existing backups are never overwritten.
5. Only then mode 04, followed by a check scan. If the control unit refuses
   (`7F 04 22`: conditions not correct), `clear` aborts with an error message; the
   backup remains.

If a step before clearing fails, mode 04 is not sent. Clearing also deletes the freeze
frame and the readiness status; if the fault is not fixed, the codes come back.

In the emulator the engine runs by default (`010C` returns changing engine speeds from
1303 rpm), so `clear` refuses. The tests set the engine speed to 0 via
`emulator.answer["RPM"]` (see `tests/integration/test_clear_emulator.py`).

### Full diagnosis

```sh
obd-diag diagnose --port /dev/ttyUSB0                   # table
obd-diag diagnose --port /dev/ttyUSB0 --json            # session as JSON
obd-diag diagnose --port /dev/ttyUSB0 --save            # save session, path on stderr
obd-diag diagnose --port /dev/ttyUSB0 --pdf report.pdf --csv codes.csv
obd-diag diagnose --port /dev/ttyUSB0 --online-vin      # also look up the VIN at NHTSA vPIC
```

`diagnose` reads in one pass, read-only (never mode 04):

1. Scan as with `scan` (adapter reset, protocol, battery voltage, codes from mode
   03/07/0A). If it fails, `diagnose` aborts.
2. Readiness (mode 01 PID 01): MIL, reported number of codes, engine type
   (petrol/diesel) and per monitor “complete”, “incomplete” or “not supported”, plus
   “All tests complete: yes/no” (yes if no supported monitor is incomplete). If several
   control units answer, the worst state counts per monitor, the MIL is on if one
   control unit reports it, and the code counts are added up.

   This is deliberately **not an emissions test verdict** (formerly “AU-bereit”): since
   the German emissions test guideline of 2017 (Verkehrsblatt 19/2017, Leitfaden 5.01,
   from 2018-01-01) the periodic emissions test (AU) again includes a tailpipe
   measurement besides the OBD check for all OBD vehicles; incomplete monitors do not
   automatically lead to a fail. There is no general, citable list of which incomplete
   monitors are tolerated; that depends on the vehicle and the test procedure of the
   AU device. Sources (German): Hella Gutmann,
   [Informationen zum Leitfaden 5.01](https://www.hella-gutmann.com/fileadmin/user_upload/Download-Dateien/X_Downloads/downloads_instructions/downloads_manuals_quickstarts/DE/BD0059_HG4_Info_Leitfaden_5-01.pdf)
   (12/2017); on the rules from 2010 (readiness not set → exhaust measurement instead
   of a defect) the [summary at werner-austen.de](http://www.werner-austen.de/plaintext/informationen/regelung-abgasuntersuchung-112010/index.php).
   In JSON the field is called `all_complete`; `ready` remains as the old name.
3. Freeze frame (mode 02, frame 00): triggering code, load, coolant temperature, engine
   speed, speed. Without a stored code it is empty and shown as “none stored”.
4. VIN (mode 09 PID 02, CAN multi-frame or older protocols with five lines) and its
   offline decoding.

If the vehicle does not answer readiness, freeze frame or VIN (or answers unusably),
only that part is missing. `--save` stores the session as described below, `--pdf` and
`--csv` export directly (like `obd-diag export`). With `--json` stdout stays pure JSON;
paths of saved files go to stderr.

### VIN

```sh
obd-diag vin --port /dev/ttyUSB0          # read from the vehicle and decode
obd-diag vin WVWZZZ1KZ6W123456            # decode only, without an adapter
obd-diag vin WVWZZZ1KZ6W123456 --json
```

The decoding is offline:

- valid: 17 characters, only 0-9 and A-Z without I, O, Q;
- check digit (position 9, ISO 3779 / 49 CFR 565): mandatory only in North America (VIN
  starts with 1-5) and China (`L`), there “correct”/“incorrect”; otherwise “correct” if
  it happens to match or is used voluntarily, and “not mandatory (differs)” if not, which
  is not an error;
- manufacturer from a table of common manufacturer identifiers (WMI, positions 1-3,
  `src/obd_diag/data/wmi.py`, checked against Wikipedia and NHTSA vPIC, sources there),
  country from the ISO 3780 region ranges of positions 1-2 (ISO overview 2021);
- model year from position 10. The code repeats every 30 years; in North America
  position 7 decides (digit: 1980-2009, letter: 2010-2039), otherwise the most recent
  year up to at most one year in the future is the best guess, and the year 30 years
  earlier is named as well: “2026 or 1996 (from position 10, without guarantee)”. If the
  VIN comes from the vehicle, older years that do not fit the OBD protocol are dropped
  (OBD-II protocols: not before 1994; CAN according to ISO 15765-4: not before 2000).
  Not all European manufacturers use position 10 as model year, so the value is given
  without guarantee there. At Mercedes-Benz (WMI WDB, WDC, WDD, WDF, W1K, W1N, W1V)
  position 10 is the steering side, so no model year is shown. In JSON the second year
  is in `model_year_alternatives`.

**Privacy:** the VIN stays on the computer. Only with `--online-vin` is it sent to the
NHTSA database [vPIC](https://vpic.nhtsa.dot.gov/api/) (USA); model, model year, body,
cylinders, displacement, fuel, plant and similar are taken over. The response is stored
per VIN under `$XDG_CACHE_HOME/obd-diag/vpic/` (default `~/.cache/obd-diag/vpic/`), so a
VIN is only queried once. Network errors are ignored. vPIC mainly knows vehicles for the
US market; for European models the details are often incomplete.

In the emulator `0101` and `0902` answer as the standard says (the VIN rotates between
three examples); it expects mode 02 without frame number, so the freeze frame stays
empty there. `tests/integration/test_diagnosis_emulator.py` adjusts this for the tests.

### Live data

```sh
obd-diag live --port /dev/ttyUSB0 --list                      # supported values
obd-diag live --port /dev/ttyUSB0                             # usual values, Ctrl+C stops
obd-diag live --port /dev/ttyUSB0 --pids rpm,speed,fuel_rail_pressure --interval 0.5
obd-diag live --port /dev/ttyUSB0 --duration 60 --record      # record 60 s as CSV
```

```
Time (s)  Engine speed (rpm)  Speed (km/h)  Coolant temperature (°C)  …  Voltage (V)
     0.0                1726            50                        86  …         14.1
     1.0                1731            51                        86  …         14.1
```

- **Values:** 116 values from 82 mode 01 PIDs according to SAE J1979
  (`protocol/pids.py`), among them engine speed, speed, coolant, intake air and oil
  temperature, load, air flow, manifold and rail pressure, EGR, commanded equivalence
  ratio, fuel trim (also secondary), oxygen sensors 1–8 (narrow and wide band), torque
  points, fuel level, fuel rate and odometer. If one PID returns several values (e.g.
  sensor voltage and trim), it is queried only once per round. The sensors are
  numbered 1–8 in PID order; which bank and position that is, is defined by the vehicle
  (PID `13` or `1D`). Only values the vehicle reports as supported are requested
  (`0100`, `0120` … across all control units). Without `--pids`: engine speed, speed,
  coolant temperature, load, intake air temperature and control module voltage, as far
  as supported.
- **Not included:** PIDs with a status byte whose structure changed between editions of
  the standard or is not unambiguous in free sources, among them boost pressure (`70`)
  and particulate filter (`7A`–`7C`); list in the docstring of `protocol/pids.py`.
- **Procedure:** round by round every value is read once; if one cannot be read, “-” is
  shown (an empty cell in the CSV) and the query carries on. If the adapter reports a
  bus error for every value for three rounds (e.g. ignition off), it ends with an error
  message. The battery voltage is read every tenth round; below 11.8 V (after a
  cross-check with the control unit) values are only queried every 5 s and the voltage
  is measured every round until it is high enough again.
- **Recording:** CSV under `$XDG_DATA_HOME/obd-diag/recordings/`
  (`live-YYYYMMDD-HHMMSS.csv`) or in the given file, which is never overwritten. Format
  as for the export: UTF-8 with BOM, in English `,` and a decimal point (in German `;`
  and a decimal comma); first column `Time (s)`, then `Name (unit)` per value, last
  `Battery voltage (V)`. Every row is written immediately, so an abort loses nothing.
- **Read-only:** only `01xx` and `ATRV` are sent.

While driving, only a passenger should operate this. Against the emulator,
`tools/emulator.py` returns randomly changing values with the engine running.

### Diagnosis sessions and export

A diagnosis session (scan with plain texts, readiness, freeze frame, VIN, time) is saved
as JSON under `$XDG_DATA_HOME/obd-diag/sessions/` (default
`~/.local/share/obd-diag/sessions/`), file name `session-YYYYMMDD-HHMMSS.json` after the
time of the diagnosis; existing files are never overwritten (then `…-2.json` etc.). The
file carries `"format": "obd-diag-session"` and `"version": 1`, the part `scan` has the
same form as `obd-diag scan --json`. Loading rejects foreign or newer formats with a
message. Trouble code texts are stored in the language of the scan; everything else is
shown in the current language.

A saved session can be converted:

```sh
obd-diag export ~/.local/share/obd-diag/sessions/session-20261007-143205.json \
    --pdf report.pdf --csv trouble-codes.csv
```

- **PDF** (A4, in the current language): vehicle (VIN, check digit, manufacturer,
  country, model year), adapter, protocol and battery voltage (with a warning for low
  voltage), summary, readiness with “All tests complete: yes/no”, trouble codes by type
  with explanation, causes with likelihood, symptoms and cost range, freeze frame.
  Missing parts appear as “not available”. Created with
  [ReportLab](https://www.reportlab.com/) (BSD licence). Font: DejaVu Sans, Liberation
  Sans or Noto Sans if installed (embedded), otherwise Helvetica from the PDF standard
  set.
- **CSV**: one row per trouble code with the columns Code, Type (Stored, Pending,
  Permanent), Title, Description, Causes, Symptoms, MIL, Emissions-related, Repair
  effort, Cost, Cost from (EUR), Cost to (EUR), Date, VIN. Several causes/symptoms are
  separated by ` | ` within one cell. Encoding UTF-8 with BOM; in English with `,` as
  separator, in German with German column names and `;`, so that a German Excel opens
  the file correctly with a double click.

### User interface

```sh
uv run obd-diag-gui
```

The user interface is installed via the extra `gui` (`pip install 'obd-diag[gui]'`);
without it a headless installation (e.g. on a Raspberry Pi) stays small.

Choose port and baud rate at the top (the list shows adapters that were found, a path
can also be typed in) and press “Connect & Scan”. This reads trouble codes, readiness,
freeze frame and VIN in one pass (read-only). The vehicle (manufacturer and VIN) then
appears at the top right, below it five tabs:

- **Trouble codes**: on the left the codes grouped into stored, pending and permanent,
  on the right the explanation of the selected code with causes, symptoms and cost
  range.
- **Readiness**: “All tests complete” (green) or “Not all tests complete” (yellow) with
  the incomplete tests, check engine light, and each monitor as complete, incomplete or
  not supported.
- **Freeze frame**: triggering code and the values when the code was stored (engine
  load, coolant temperature, engine speed, speed).
- **Vehicle**: VIN, manufacturer, country, model year, check digit; optionally details
  from NHTSA vPIC (model, engine …).
- **Live data**: choose values (the ones the vehicle supports appear after the first
  start), interval 0.5/1/2 s, “Record (CSV)”, then Start. One tile per value with the
  current value, smallest/largest value seen and a curve of the last 120 values; the
  curve scales to the values shown. At the top battery voltage, run time and rounds.
  While live data runs, scan, clearing and export are locked (only one action runs on
  the adapter at a time); conversely, live data does not start during a diagnosis.

If the control unit does not answer a part, the tab shows “Not available”. At the
bottom: adapter, protocol, battery voltage (red when low).

*Options → Sprache / Language* switches the language (Deutsch or English, applies
immediately; trouble code texts from the next scan). Without a stored choice the system
language applies.

“Look up VIN online (NHTSA)” (menu *Options* or tab *Vehicle*) is off by default. When
switched on, only the VIN goes to the US authority NHTSA at the next scan; the setting is
stored per user (`~/.config/obd-diag/obd-diag.conf`).

Menu *File* and buttons at the bottom:

- **Save session** (Ctrl+S) stores the diagnosis as JSON (see above) and shows the
  path.
- **Open session …** (Ctrl+O) shows a saved session for viewing only; clearing is not
  possible then (“only with a connected vehicle”).
- **Report as PDF …** (Ctrl+P) and **Export CSV …** ask for the target (suggestion
  `obd-report-YYYYMMDD-HHMM.pdf` or `.csv` in the Documents folder) and write in the
  background.

The file dialogs come from the desktop (xdg-desktop-portal or GTK); if neither is
available, Qt uses its own dialog.

“Clear trouble codes …” is currently locked (grey, the tooltip gives the reason). Once
enabled, it asks first (ignition on, engine off; codes and freeze frame are backed up;
the readiness for the emissions test is reset), then shows the path of the backup and
reads the diagnosis again, so readiness and freeze frame show the state after clearing.

Against the emulator:

```sh
uv run python tools/emulator.py   # shows the pty, e.g. /dev/pts/5
uv run obd-diag-gui               # enter /dev/pts/5 in the port field, connect
```

Every action opens the port, works in a background thread and closes it again; the user
interface stays responsive meanwhile.

### Trace (`--trace`)

All commands that use the adapter (`info`, `scan`, `diagnose`, `vin`, `clear`, `live`)
record every sent and received line with a timestamp when given `--trace`:

```sh
uv run obd-diag diagnose --port /dev/ttyUSB0 --trace            # ~/.local/share/obd-diag/traces/
uv run obd-diag diagnose --port /dev/ttyUSB0 --trace car.log    # own file
```

```
# obd-diag 0.2.0 Mitschnitt 2026-10-07T16:09:33+02:00 /dev/ttyUSB0 38400 Baud
    0.000 >> ATZ\r
    0.508 << ATZ\r\r\rELM327 v1.5\r\r>
    3.013 >> 03\r
    3.016 << 00A\r0: 430404200133\r1: 0300C100\r\r>
```

Control characters appear as `\r`, `\xNN`; `>>` is sent, `<<` received, `!!` an error.
The header line is part of the file format and stays German (“Mitschnitt” = trace). In
the user interface “Options → Record adapter trace” switches this on for every action
(one file per action). The trace may contain the VIN and stays local; before sharing or
checking it in, redact the VIN in the response to `0902` (instructions in the
documentation under “First test at the car”, “Sharing traces”). `ReplayTransport` in
`transport/trace.py` plays it back without an adapter, e.g. as a test fixture.

### Protocol details

The adapter runs without headers (`ATH0`), without spaces and without echo. The tool
handles deviations of real adapters and control units like this:

- **Mixed multi-frame responses:** if two control units send multi-frame CAN messages
  at the same time, the ELM327 mixes their frames without headers (datasheet ELM327DS
  p. 45). If splitting detects this (gaps in the frame numbering, incomplete message),
  the read request (mode 03/07/0A, VIN `0902`) is repeated once with `ATH1`, the frames
  are assembled per CAN ID according to ISO 15765-2 (11 bit `7E8 …`, 29 bit
  `18 DA F1 10 …`), then `ATH0` again (`protocol/headers.py`). The codes are then
  ordered by control unit address (7E8 before 7E9), otherwise in order of arrival.
  Older protocols with headers (`48 6B 10 … <checksum>`) are split as well; the
  checksum byte is removed but not checked. Mixing does not occur there (one line per
  message).
- **Freeze frame:** requested according to SAE J1979 with frame number (`020C00`). If
  the vehicle answers `020200` with `NO DATA` or `7F 02 12`, `0202` without frame
  number is tried (like python-OBD) and, if that works, the whole freeze frame is read
  that way. The keys in `raw` (backup, session) show the format used.
- **Clearing with `7F 04 78`** (control unit reports “response pending”): it waits up to
  10 s for `44` or a rejection without sending again. If nothing comes, clearing counts
  as not confirmed (hint to `obd-diag scan`, the backup remains). Mode 04 is never
  repeated.
- **Unclear protocol number** (`ATDPN` reports `0`, `?` or similar): `ATDPN` is asked
  again; if it remains unclear, `0100` is sent once with headers, and their form shows
  whether it is CAN 11 bit, CAN 29 bit or an older protocol. The protocol name then
  carries the addition “according to headers …”.
- **Echo:** a first line that equals the command regardless of case and spaces
  (`at dpn` for `ATDPN`) is removed.

## Structure

```
src/obd_diag/
├── transport/   # byte channel to the adapter (protocol, USB serial, adapter discovery)
├── protocol/    # ELM327 commands with allowlist, OBD-II decoding, PID table
├── services/    # procedures: scan, clearing, save/load session, live data
├── data/        # DTC catalog (SQLite), WMI table for the VIN
├── export/      # PDF report and CSV of a session
├── ui/          # desktop user interface: view models (Python) and QML
├── locale/      # en.po: English translation of the texts
├── i18n.py      # tr/N_/trn, setting the language
└── cli.py
```
