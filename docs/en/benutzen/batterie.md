# Battery and cranking test

The test measures the battery voltage at three moments and rates it: at rest (state of
charge), while cranking (how far the voltage drops, which shows the battery's starting
power) and at idle (whether the alternator charges). You start the engine yourself as
soon as obd-diag asks you to. Only read commands are sent ({doc}`../technik/sicherheit`).

```sh
obd-diag battery --port /dev/ttyUSB0
obd-diag battery --demo              # try the procedure without a car
```

In the GUI: *Options → Battery and cranking test …*, then *Start test*. The dialog shows
the instruction in large type, along with the voltage and the time left.

## Procedure

1. **Prepare:** vehicle stationary, gear in P or neutral, parking brake on. Ignition on,
   engine off, lights and blower off. If the engine is already running, the test stops.
2. **Rest (3 s):** obd-diag reads the voltage from the adapter (`ATRV`) about 50 times a
   second and the voltage from the engine control unit (PID 42).
3. **Cranking:** prompt “Start the engine now”. obd-diag waits up to 30 s
   (`--start-timeout`); while cranking it reads only the voltage, as densely as possible.
4. **Charging (10 s):** keep the engine running. After 5 s to settle, the charging voltage
   is measured for 5 s.

The whole test takes about 15 s plus the time until you start the engine.

## Rating

| Measurement | good | noticeable | poor |
| --- | --- | --- | --- |
| Resting voltage | from 12.2 V | 11.8 to 12.2 V | below 11.8 V |
| Lowest value while cranking | from limit + 0.4 V | limit to + 0.4 V | below the limit |
| Charging voltage at idle | 13.5 to 15.0 V | 12.8 to 13.5 V | below 12.8 or above 15.0 V |

- **Rest:** at rest a 12 V lead-acid battery has about 12.6 V when full, 12.4 V at three
  quarters and 12.2 V at half charge; below 12.0 V it is largely empty. With the ignition
  on, the control units draw current, so the voltage is somewhat lower than with no load
  at all. Shortly after a drive it reads too high (surface charge): the test is most
  meaningful after the car has stood for a few hours.
- **Cranking:** the limit comes from the classic load test, in which a battery must not
  drop below 9.6 V at 21 °C. In the cold a lower limit applies (16 °C 9.5 V, 10 °C 9.4 V,
  4 °C 9.3 V, −1 °C 9.1 V, −7 °C 8.9 V, −12 °C 8.7 V, −18 °C 8.5 V). obd-diag takes the
  coolant temperature before the start as the battery temperature; with a warm engine it
  is higher and the limit therefore the strictest. The first 0.1 s of cranking (inrush
  current of the starter) does not count. A value that is too low can also be caused by
  the starter or the cables.
- **Charging:** vehicles with battery management (start-stop, regulated alternator) often
  charge lower on purpose; 12.8 to 13.5 V is normal there. Below 12.8 V the alternator is
  not charging, above 15.0 V the regulator charges too high.

The values are guide values for 12 V lead-acid batteries (including AGM and EFB), not
figures from the vehicle manufacturer. The overall result is the worst of the three
ratings.

## Adapter and control unit

The adapter measures the voltage at pin 16 of the diagnostic socket, often behind a
protection diode. On the Mercedes A 180 d ({doc}`erster-test`) it always showed about
0.8 V less than the engine control unit. If the control unit reports its voltage
(PID 42), obd-diag adds the difference at rest to all adapter values and takes resting
and charging voltage directly from the control unit; the note below the result states the
correction.

Why not only the control unit: with the minimum gap of 50 ms, every request to the
vehicle takes more than 0.1 s, `ATRV` on the FORScan adapter only about 15 ms. Cranking
is often over within a second. `ATRV` is an adapter command and does not reach the
vehicle.

If the voltage drops so low that the adapter restarts, obd-diag sets it up again and
notes this; the cranking value is then rated as noticeable.
