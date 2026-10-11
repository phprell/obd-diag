# Installation

obd-diag needs Linux. From version 0.2.1 on there are ready-made packages; all of them
already contain the trouble code catalog.

| Way | For whom | User interface |
|---|---|---|
| pipx (PyPI) | any distribution, also Raspberry Pi | with the extra `gui` |
| AUR | Arch Linux and derivatives | always, with start menu entry and udev rule |
| Flatpak | desktop, independent of the distribution | always, with start menu entry |
| Sources | development | with `uv sync` |

## pipx

[pipx](https://pipx.pypa.io/) installs programs from PyPI into their own environment
(Python 3.12 or newer):

```sh
pipx install 'obd-diag[gui]'     # with user interface
pipx install obd-diag            # command line only, for example on a Raspberry Pi
obd-diag --version
obd-diag-gui --install-desktop   # start menu entry (remove: --remove-desktop)
```

`--install-desktop` puts the desktop file and icon under `~/.local/share`. Update with
`pipx upgrade obd-diag`. Without PyPI, the wheel from a
[GitHub release](https://github.com/phprell/obd-diag/releases) works as well:
`pipx install 'obd-diag[gui] @ <address of the .whl file>'`.

## AUR

The package `obd-diag` uses PySide6, pyserial and ReportLab from Arch and also installs
the start menu entry, the icon and the udev rule (see below):

```sh
yay -S obd-diag                  # or another AUR helper
```

Without a helper: put the `PKGBUILD` from the GitHub release (or the AUR) into an empty
folder and run `makepkg -si` there. The template is in `packaging/aur/`.

## Flatpak

Every release contains `obd-diag.flatpak` (x86_64) with the user interface. Runtime and
PySide6 come from Flathub:

```sh
flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak install --user obd-diag.flatpak
flatpak run io.github.phprell.obd_diag
flatpak run --command=obd-diag io.github.phprell.obd_diag --version   # command line
```

The Flatpak may access all devices (`--device=all`), because Flatpak cannot grant serial
ports individually, and the network only for the optional online lookups. Access to
the port is still controlled by the system (see below). The manifest is in
`packaging/flatpak/`.

## From the sources

[uv](https://docs.astral.sh/uv/) is recommended; it sets up the environment and
dependencies from `uv.lock`.

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
range) are stored offline in `src/obd_diag/data/dtc_catalog.sqlite`. The packages
contain the file; in the sources it is not checked in but built:

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

or install the udev rule (the AUR package includes it). It gives the currently logged-in
user access to the common USB serial chips (FTDI, CH340/CH341, CP210x) without changing
group or file permissions. It is in the sources under `src/obd_diag/share/`:

```sh
curl -LO https://raw.githubusercontent.com/phprell/obd-diag/main/src/obd_diag/share/70-obd-diag.rules
sudo install -m 644 70-obd-diag.rules /etc/udev/rules.d/
sudo udevadm control --reload && sudo udevadm trigger
```

Then unplug the adapter and plug it in again.

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
