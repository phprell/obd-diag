<div align="center">

# obd-diag

**See what your car's check engine light is about, on Linux.**

Plug a cheap ELM327 adapter into the OBD-II port, and obd-diag reads the trouble codes,
explains them in plain words and shows what the engine is doing right now.
Desktop app and command line, in English and German, no account and no cloud.

[![CI](https://github.com/phprell/obd-diag/actions/workflows/ci.yml/badge.svg)](https://github.com/phprell/obd-diag/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/obd-diag)](https://pypi.org/project/obd-diag/)
[![Python](https://img.shields.io/pypi/pyversions/obd-diag)](https://pypi.org/project/obd-diag/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](https://github.com/phprell/obd-diag/blob/main/LICENSE)
[![Docs](https://img.shields.io/badge/docs-phprell.github.io-informational)](https://phprell.github.io/obd-diag/en/)

**English** · [Deutsch](https://github.com/phprell/obd-diag/blob/main/README.de.md)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/phprell/obd-diag/main/.github/assets/readme/used-car-dark-en.png">
  <img src="https://raw.githubusercontent.com/phprell/obd-diag/main/.github/assets/readme/used-car-light-en.png" alt="obd-diag desktop app: used-car check of a Mercedes A 180 d, verdict inconspicuous" width="820">
</picture>

</div>

## Why obd-diag

- **Read-only by design.** obd-diag only asks, it never changes anything in the car.
  Every command passes an allowlist taken from the ELM327 datasheet and SAE J1979;
  anything else is refused before it reaches the adapter. No coding, no flashing, no
  service functions.
- **Answers, not hex.** Trouble codes come with an offline description and a rough
  urgency (from "keep an eye on it" to "avoid driving").
- **Buying a used car?** The used-car check shows whether someone cleared the trouble
  codes shortly before the viewing, using the counters that clearing resets.
- **Tested on a real car** (Mercedes A 180 d) and, without hardware, against the
  datasheet, third-party adapter logs and python-OBD, with around 2000 automated tests.

## What it shows

| | |
| --- | --- |
| **Trouble codes** | stored, pending and permanent, with description and urgency |
| **Readiness** | which self-tests have run, and which drive completes the missing ones |
| **Used-car check** | were the codes cleared recently? warm-ups and distance since then |
| **Live data** | 140 values (speed, temperatures, boost, rail pressure, AdBlue …) as tiles with curves, recorded to CSV, with warning thresholds |
| **Freeze frame** | engine conditions at the moment a code was set |
| **Vehicle** | VIN decoded offline, control units, calibration IDs, self-test results |
| **Battery test** | resting voltage, dip while cranking, charging voltage |
| **Reports** | PDF report, CSV, saved sessions you can compare later |

<table>
  <tr>
    <td width="50%">
      <picture>
        <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/phprell/obd-diag/main/.github/assets/readme/live-data-dark-en.png">
        <img src="https://raw.githubusercontent.com/phprell/obd-diag/main/.github/assets/readme/live-data-light-en.png" alt="Live data tiles with curves">
      </picture>
    </td>
    <td width="50%">
      <picture>
        <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/phprell/obd-diag/main/.github/assets/readme/readiness-dark-en.png">
        <img src="https://raw.githubusercontent.com/phprell/obd-diag/main/.github/assets/readme/readiness-light-en.png" alt="Readiness monitors, one test incomplete">
      </picture>
    </td>
  </tr>
</table>

## Try it without a car

The demo mode plays back a recorded Mercedes A 180 d, no adapter needed:

```sh
pipx install 'obd-diag[gui]'
obd-diag-gui --demo
```

## Install

```sh
pipx install 'obd-diag[gui]'              # any distribution (PyPI); without [gui]: command line only
yay -S obd-diag                           # Arch Linux (AUR)
flatpak install --user obd-diag.flatpak   # bundle from the GitHub release
```

Needs Linux and Python 3.12 or newer. Details, udev rule and serial port permissions:
[Installation](https://phprell.github.io/obd-diag/en/benutzen/installation.html).

## At the car

You need an ELM327-compatible adapter (USB or Bluetooth serial) and a car with OBD-II
(petrol from 2001, diesel from 2004 in the EU; 1996 in the US).

```sh
obd-diag setup-check                      # finds the adapter, checks permissions and baud rate
obd-diag diagnose --port /dev/ttyUSB0     # ignition on, engine off
obd-diag live --port /dev/ttyUSB0         # live data, Ctrl+C to stop
obd-diag-gui                              # or all of it in the desktop app
```

Before the first drive with a laptop on the passenger seat, read
[First test at the car](https://phprell.github.io/obd-diag/en/benutzen/erster-test.html).

## What it does not do

obd-diag talks to the standardised emissions diagnosis (OBD-II). It does not reach
airbag, ABS or comfort modules, and it does not code, flash or reset service intervals.
Clearing trouble codes is built in, with checks and a backup, but stays locked for
now. Next on the roadmap: Bluetooth LE adapters and
reading the fault memory of all control units via UDS, still read-only.

## Documentation

Everything else is on the website, in [English](https://phprell.github.io/obd-diag/en/)
and [German](https://phprell.github.io/obd-diag/): every command and option, the user
interface, common problems, how the tool talks to the adapter (with a real trace
explained line by line) and the safety concept.

## Contributing

Bug reports and traces from other cars are very welcome, especially from makes other
than Mercedes. `--trace` records the adapter communication; it contains the VIN, so
please remove it before sharing.

```sh
uv sync && uv run python tools/build_dtc_db.py
uv run pytest
```

Rules, tests and translations: [Contributing](https://phprell.github.io/obd-diag/en/entwickeln/regeln.html),
changes: [CHANGELOG.md](https://github.com/phprell/obd-diag/blob/main/CHANGELOG.md) (German).

## License

[MIT](https://github.com/phprell/obd-diag/blob/main/LICENSE). Trouble code texts from
[OBDex](https://github.com/foerbsnavi/OBDex).
