# Installation

obd-diag needs Linux and Python 3.12 or newer. [uv](https://docs.astral.sh/uv/) is
recommended; it sets up the environment and dependencies from `uv.lock`.

```sh
git clone https://github.com/phprell/obd-diag.git
cd obd-diag
uv sync                                  # environment incl. user interface and dev tools
uv run python tools/build_dtc_db.py      # build the trouble code catalog (~9 s download from GitHub)
uv run obd-diag --version
```

Without uv:

```sh
python -m venv .venv
.venv/bin/pip install -e '.[gui]'
.venv/bin/python tools/build_dtc_db.py   # additionally needs pyyaml
```

The `gui` extra brings PySide6 for the user interface. Without it, a pure command line
installation stays small, for example on a Raspberry Pi.

## Trouble code catalog

The plain texts for the trouble codes (English and German, causes, symptoms, cost
range) are stored offline in `src/obd_diag/data/dtc_catalog.sqlite`. The file is not
checked in but built:

```sh
uv run python tools/build_dtc_db.py                     # downloads the data from GitHub
uv run python tools/build_dtc_db.py --source ../OBDex   # or from a local clone
```

The data source is [OBDex](https://github.com/foerbsnavi/OBDex) (data under
[CC0-1.0](https://creativecommons.org/publicdomain/zero/1.0/)), pinned to one commit
(`OBDEX_COMMIT` in the script). Without the catalog, obd-diag shows the codes without
description.

## Permissions for the serial port

USB adapters appear as `/dev/ttyUSB0` or `/dev/ttyACM0` and belong to the group `uucp`
(Arch) or `dialout` (Debian, Ubuntu). Either add the user to the group and log in again:

```sh
sudo usermod -aG uucp $USER      # Debian/Ubuntu: dialout
```

or install the udev rule from `packaging/`. It gives the logged-in user access to the
common USB serial chips (FTDI, CH340/CH341, CP210x):

```sh
sudo cp packaging/99-obd-diag.rules /etc/udev/rules.d/
sudo udevadm control --reload
```

## Checking the setup

```sh
obd-diag setup-check                     # all adapters found
obd-diag setup-check --port /dev/rfcomm0 # only this port
```

checks in turn whether the user interface (PySide6) and the trouble code catalog are
installed, which adapters are connected, whether the user may open them and at which
baud rate they answer (first 38400, then 115200). Without access, the check names the
device's group and the command to join it; if the user has joined the group but not
logged in again yet, it says so. If it finds no adapter and `brltty` is installed, it
points that out: on Ubuntu, this program for braille displays often claims adapters
with a CH340 chip.

```
  OK       User interface (PySide6) installed
  OK       Trouble code catalog present
  OK       /dev/ttyUSB0 (USB Serial (QinHeng)): access allowed
  OK       /dev/ttyUSB0: ELM327 v1.5 answers at 38400 baud
  Note     Before connecting to the car: switch the ignition on (the engine may stay off). On adapters with an MS/HS-CAN switch, select HS-CAN.

Result: ready. Continue with: obd-diag diagnose --port /dev/ttyUSB0 --baud 38400
```

Only the adapter commands every command starts with are sent (`ATZ`, `ATE0`, `ATL0`,
`ATS0`, `ATH0`, `ATSP0`); none of them reaches the vehicle, so the check also works
without a car. The exit code is 0 when an adapter answers.

The user interface opens the same check by itself on the first start and later under
*Options → Check setup …*; “Apply” enters port and baud rate at the top.
