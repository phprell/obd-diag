# Adapter and connection

obd-diag talks to adapters that behave like an ELM327: via USB as a serial port or via
classic Bluetooth (`/dev/rfcomm*`). Bluetooth LE only comes with v0.3
({doc}`../entwickeln/roadmap`).

## Which adapter

- Adapters with a genuine ELM327 or STN chip (e.g. OBDLink) are reliable.
- Cheap “v1.5” clones often identify themselves as ELM327 but do not support all
  commands or return garbage characters. obd-diag catches some of this
  ({doc}`../technik/antwortformate`) but guarantees nothing.
- **FORScan ELMconfig USB** (the adapter for the first test): has an MS-CAN/HS-CAN
  switch. For the standardised diagnostics it must be set to **HS-CAN**; MS-CAN is a
  Ford-specific comfort bus. The USB chip is usually a CH340.

## Baud rate

The baud rate applies between computer and adapter, not to the vehicle. The default is
38400 baud (`--baud`); many USB adapters also or only work with 115200. If
`obd-diag info` gets no response, the other baud rate is the first thing to try.

## Finding the adapter

```sh
obd-diag ports
```

lists USB serial adapters (`/dev/ttyUSB*`, `/dev/ttyACM*` and other devices with a USB
ID) and bound Bluetooth devices (`/dev/rfcomm*`). Built-in ports (`/dev/ttyS*`) are not
listed. A Bluetooth adapter is bound beforehand:

```sh
sudo rfcomm bind 0 <MAC address>    # then /dev/rfcomm0
```

## Checking the connection

```sh
obd-diag info --port /dev/ttyUSB0
```

```
Adapter:         ELM327 v1.5
Battery voltage: 12.4 V
```

`info` resets the adapter and reads its identification and the voltage at pin 16 of
the OBD socket. The vehicle is not addressed yet.

## Checking the adapter

```sh
obd-diag adapter --port /dev/ttyUSB0 --lang en
```

```
Adapter:         ELM327 v1.5
Description:     OBDII to RS232 Interpreter
Version:         v1.5: not in Elm Electronics' version list, the adapter is a clone
Verdict:         clone, command set like v1.4
Battery voltage: 12.4 V
Parameters:      all at factory settings
For UDS (v0.3):  likely yes: command set from v1.4, which includes the required commands ATSH (from v1.0) and ATCRA (from v1.3)

Commands checked:
  ATI:   yes (since v1.0: identification)
  AT@1:  yes (since v1.0: device description)
  ATRV:  yes (since v1.0: voltage)
  ATPPS: yes (since v1.1: summary of the programmable parameters)
  ATIGN: yes (since v1.4: ignition input)
  ATRD:  yes (since v1.4: read user memory)
  ATAMC: no (since v2.0: activity monitor count)
```

(Example with made-up responses; which commands the FORScan ELMconfig adapter really
knows will only be shown by running the check on it.)

The adapter check shows what the adapter can really do. Many adapters identify
themselves as “ELM327 v1.5” or “v2.1” but are clones with an incomplete command set. The
check asks for the identification (`ATI`), description (`AT@1`) and voltage (`ATRV`)
and tries commands that according to the datasheet's version list only came with a
certain firmware (`ATPPS` v1.1, `ATIGN` and `ATRD` v1.4, `ATAMC` v2.0). From that follow:

- **Version**: Elm Electronics never released a v1.5 (the version list jumps from v1.4b
  to v2.0); an adapter that reports it is a clone.
- **Verdict**: which version the command set really corresponds to and whether commands
  are missing that the reported version should have (“clone, …; missing ATAMC”).
- **Battery voltage**: below 5 V the adapter is probably not plugged into the car (it
  measures the voltage at pin 16 of the OBD socket), below 11.8 V the battery is weak.
- **Parameters**: whether someone changed the adapter's defaults (programmable
  parameters); obd-diag never changes them.
- **For UDS (v0.3)**: whether the adapter is likely to work for the planned control unit
  search. It needs `ATSH` and `ATCRA`; the check does not send those itself because
  they change the addressing. The verdict is therefore inferred from the rest of the
  command set; only the search itself will tell for sure.

The check only sends adapter commands, the vehicle is not addressed; none of the
commands changes the adapter permanently ({doc}`../technik/befehle`). `--json` prints
the result in machine-readable form. In the user interface it is under *Options → Check
adapter …*:

```{image} ../_static/screenshots/adapter-check-light.png
:alt: Adapter check dialog with version, verdict, voltage, UDS suitability and the commands checked
```
