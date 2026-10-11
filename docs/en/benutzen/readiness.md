# Readiness assistant

After clearing trouble codes, replacing the battery or a repair, the emission system's
self-tests (readiness tests, monitors) are reset and only run again during suitable
drives. The readiness assistant shows which tests are still incomplete, which of them
already finished during the current drive, and what kind of drive typically completes
the rest.

```sh
obd-diag readiness --port /dev/ttyUSB0
obd-diag readiness --demo           # sample vehicle, no car needed
obd-diag readiness --port /dev/ttyUSB0 --json
```

In the graphical interface the assistant sits in the *Readiness* tab below the
monitors. After "Connect & scan" it shows a driving hint for every incomplete test; the
button *Read status of this drive* additionally asks for the status of the current
drive without repeating the whole diagnosis.

## Two states: since clearing and during this drive

| Request | Meaning | reset |
| --- | --- | --- |
| Mode 01 PID 01 | status since the trouble codes were cleared | when clearing (Mode 04) |
| Mode 01 PID 41 | status during this drive cycle | every time the ignition is switched on |

PID 41 reports three states per test: *complete*, *incomplete* (the test may run but has
not finished) and *suspended* (the conditions to start it were not met during this
drive, e.g. temperature or fuel level). Because the status of this drive starts over at
the next start, check it at the end of a drive with the engine running. If the vehicle
does not report PID 41, the assistant only shows the status since clearing.

## Driving hints

The hints describe typical drives per test, separately for petrol and diesel engines,
for example for the exhaust gas sensor on a diesel: about 20 minutes at a steady speed
on country roads or the motorway with a hot exhaust system, with overrun phases in
between (lift off the accelerator without braking). These are rules of thumb, not a
standard: SAE J1979 only defines how the status is reported. When a test runs is up to
each manufacturer, and it often takes several drives, including a cold start after the
car has stood for several hours. If trouble codes are stored, some tests only run once
the cause is fixed.

As with readiness itself: this is not an emissions test verdict. An official emissions
test always includes a tailpipe measurement.

## What is sent

Only read requests: initialise the adapter, negotiate the protocol (`0100`), then `0101`
and `0141`. Both are listed in the {doc}`../technik/befehle`; the answer layout is in
{doc}`../technik/dienste`. In demo mode ({doc}`demo`) the exhaust gas sensor is
incomplete and not yet complete during this drive; the answer to `0141` there is an
invented sample value because it was not recorded on the real car.
