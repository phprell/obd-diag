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
