# Used-car check

When looking at a used car you want to know whether someone cleared the trouble codes
shortly before, so that the check engine light is off. The vehicle tells you, read-only:
when codes are cleared (mode 04), every emission control unit resets a few counters to
zero, and they only count up again with driving.

```sh
obd-diag used-car --port /dev/ttyUSB0          # just the check
obd-diag used-car --port /dev/ttyUSB0 --json   # machine-readable
obd-diag diagnose --port /dev/ttyUSB0 --pdf report.pdf    # full diagnosis, with the check
```

The full diagnosis (`obd-diag diagnose`, “Connect & Scan” in the user interface) always
includes the check. In the user interface it is on the “Used car” tab, in the PDF report
after the readiness. Best with the ignition on and the engine off, as for the diagnosis
({doc}`erster-test`).

```{image} ../_static/screenshots/gebrauchtwagen-light.png
:alt: Used car tab: verdict, findings and counters since clearing
:class: only-light
```

```{image} ../_static/screenshots/gebrauchtwagen-dark.png
:alt: Used car tab: verdict, findings and counters since clearing
:class: only-dark
```

## What is read

| Request | Meaning | when cleared |
| --- | --- | --- |
| `0130` | warm-ups since codes cleared (0 to 255) | to 0 |
| `0131` | distance since codes cleared in km | to 0 |
| `014E` | time since codes cleared in minutes | to 0 |
| `0121` | distance with the MIL on in km | to 0 |
| `014D` | time with the MIL on in minutes | to 0 |
| `0A` | permanent trouble codes | stay |
| `0101` | check engine light and readiness tests | tests incomplete again |

A warm-up as defined by SAE J1979: the coolant has risen by at least 22 °C since the
start and reached at least 70 °C. Not every vehicle reports all counters; the
Mercedes A 180 d from the tests on the car knows `0121`, `0130` and `0131`, but no times.
If several control units answer, the smallest value counts for the counters since
clearing and the largest for the MIL counters.

## How the verdict is reached

A vehicle is **suspicious** if at least one of these findings applies:

- cleared recently: fewer than 10 warm-ups, 200 km or 300 minutes since clearing
- the check engine light is on, or it was on since the codes were last cleared
  (distance or time with the MIL on above 0)
- permanent trouble codes: they cannot be cleared; the control unit removes them only
  once it no longer detects the fault itself

**Inconspicuous** means: the counters can be read and none of these findings applies.
**Cannot be assessed** means the vehicle reports none of the counters since clearing and
nothing else stands out. Incomplete readiness tests are listed as a note: they are
incomplete again after clearing, but can also be due to driving conditions.

:::{note}
The limits are a rule of thumb, not a standard. A workshop, a battery change or a new
control unit also reset the counters. A finding is a reason to ask the seller when and
why the codes were cleared, not proof of a hidden fault.
:::
