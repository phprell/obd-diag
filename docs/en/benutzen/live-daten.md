# Live data

Live data queries current values round by round (mode 01) and shows them, optionally
recording them as CSV.

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

:::{warning}
While driving, only a passenger should operate this.
:::

## Which values

116 values from 82 mode 01 PIDs according to SAE J1979, among them engine speed,
speed, temperatures, load, air flow, manifold and rail pressure, EGR, fuel trim, oxygen
sensors 1–8, torque, fuel level, fuel rate and odometer. The complete list with key,
formula and value range is in {doc}`../technik/pids`.

- Only values the vehicle reports as supported are requested (`0100`, `0120` … across
  all control units). `--list` shows them with their key.
- Without `--pids`: engine speed, speed, coolant temperature, load, intake air
  temperature and control module voltage, as far as supported.
- If one PID returns several values (sensor voltage and trim), it is queried only once
  per round.
- The sensors are numbered 1–8 in PID order. Which bank and position that is, is
  defined by the vehicle (PID `13` or `1D`).
- Not included are PIDs with a status byte whose structure is not unambiguous in free
  sources, among them boost pressure (`70`) and particulate filter (`7A`–`7C`).

## One round

- Each selected value is read once. If one cannot be read, “-” is shown (an empty cell
  in the CSV) and the query carries on.
- The battery voltage (`ATRV`) is read every tenth round. If the adapter shows less
  than 11.8 V, it is cross-checked with the control module voltage (PID 42): many
  adapters measure a few tenths of a volt too little behind a protection diode. If that
  is also below (or missing), values are only queried every 5 s to protect the battery.
  While throttled, the voltage is measured every round; once it is high enough again
  (engine started), polling continues at the normal rate right away.
- If the adapter reports a bus error for every value for three rounds (e.g. ignition
  off), the query ends with an error message. `NO DATA`, `?` and rejections do not
  count as bus errors.
- Only `01xx` and `ATRV` are sent.

## Recording

`--record` writes a CSV under `~/.local/share/obd-diag/recordings/`
(`live-YYYYMMDD-HHMMSS.csv`) or to the given file, which is never overwritten. The
format follows the language ({doc}`sprache`): UTF-8 with BOM; in English `,` as
separator and a decimal point, in German `;` and a decimal comma. First column
`Time (s)`, then `Name (unit)` per value, last `Battery voltage (V)`. Every row is
written immediately, so an abort loses nothing.
