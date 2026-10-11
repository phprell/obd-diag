# Control units and test results

Besides the VIN, the control units of the emissions diagnostics provide further
information (mode 09) and the measured values of their self-tests with limits
(mode 06). obd-diag reads both as part of every diagnosis; each also has its own command:

```sh
obd-diag ecus --port /dev/ttyUSB0           # mode 09 per control unit
obd-diag tests --port /dev/ttyUSB0          # mode 06 test results
obd-diag tests --demo --json                # sample vehicle, machine-readable
```

In the GUI the control units are listed in the *Vehicle* tab below the VIN, the test
results in the *Test results* tab. The PDF report and saved sessions contain both, the
CSV file does not.

## Control units (mode 09)

```
Control units (mode 09):
  18DAF110  ECM-EngineControl
    Calibration ID:                          A6519006400
    CVN:                                     9F3E61C2
    Monitoring counters:                     96 driving cycles, 240 ignitions
      NMHC catalyst:                         40 of 96 (0.42)
      Exhaust gas sensor:                    0 of 96 (0.00)
      …
  18DAF118  TCM-TransmissionCtl
    Calibration ID: A7229002000
    CVN:            0C55D1E7
```

For each control unit that answers (address from the CAN header):

| Item | Request | Meaning |
| --- | --- | --- |
| Name | `090A` | short name of the control unit, such as `ECM-EngineControl` (engine) or `TCM-TransmissionCtl` (gearbox) |
| Calibration ID | `0904` | identifies the software version; changes with a software update |
| CVN | `0906` | checksum over the calibration; a different CVN with the same calibration ID means the software was modified |
| Monitoring counters | `0908` (petrol), `090B` (diesel) | how often a test completed and how often its conditions were met |

The monitoring counters (in-use performance tracking) show whether a test runs at all
in everyday driving. The ratio is *completed / conditions*; depending on the test and the
regulation (EU, California), minimum ratios between 0.1 and 0.336 are required. A ratio of 0 means the test had the
opportunity but never finished. This often matches a readiness test that stays
incomplete ({doc}`readiness`). The counters run since production or the last software
update; clearing the trouble codes does not reset them.

Each control unit reports which items it provides (`0900`). obd-diag only requests
those and only the ones listed; the control unit's serial number and similar items are
left out.

## Test results (mode 06)

```{image} ../_static/screenshots/demo-testergebnisse-light.png
:alt: Test results tab in demo mode: seven tests of the engine control unit
:class: only-light
```

```{image} ../_static/screenshots/demo-testergebnisse-dark.png
:alt: Test results tab in demo mode: seven tests of the engine control unit
:class: only-dark
```

The control units regularly test the catalyst, oxygen sensors, exhaust gas
recirculation, particulate filter and other components themselves. Mode 06 returns the
last measured value of each of these tests and the limits within which the control
unit considers it OK. This shows how close a component is to failing before a trouble
code is set.

| Column | Meaning |
| --- | --- |
| Monitor | what is tested, e.g. *Catalyst bank 1* (standardised monitor ID) |
| Test | which test of the monitor; from number 80 the manufacturer defines what it measures |
| Value, limits | measured value with minimum and maximum, converted to the standardised unit |
| Result | *passed* (minimum ≤ value ≤ maximum), *failed* or *no result* |

obd-diag shows *no result* when the value and both limits are 0: usually the test has
not run since the codes were last cleared. A *failed* alone is not yet a fault; the
control unit only sets a trouble code according to its own logic (often two drives in
a row).

obd-diag reads mode 06 only on CAN vehicles (roughly from 2008). Older protocols use a
different format without units, which is not evaluated here. What the requests look
like is described in {doc}`../technik/dienste`.

:::{note}
The demo vehicle's answers to mode 09 and mode 06 are invented; there is no capture of
these services from a real car yet.
:::
