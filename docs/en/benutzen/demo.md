# Try it without a car

Demo mode shows obd-diag with a built-in sample vehicle, without an adapter and without a
car: to get to know it, for demonstrations and for screenshots.

```sh
uv run obd-diag-gui --demo          # user interface, port "demo" is already selected
uv run obd-diag diagnose --demo     # command line; same as --port demo
uv run obd-diag live --demo
```

In the user interface the demo vehicle is also always the last entry of the port list
("demo"). It is never selected on its own, even when no adapter is plugged in. While
"demo" is selected, a blue notice above the list says that the data does not come from
your own car. The command line prints the same notice, and the adapter shows up as
`ELM327 v1.5 (Demo)` everywhere, including saved sessions, PDF and CSV.

```{image} ../_static/screenshots/demo-fehlercodes-light.png
:alt: User interface in demo mode: port demo, notice above the list, code U1218
:class: only-light
```

```{image} ../_static/screenshots/demo-fehlercodes-dark.png
:alt: User interface in demo mode: port demo, notice above the list, code U1218
:class: only-dark
```

## Where the data comes from

The demo vehicle is the Mercedes A 180 d (W177) from the tests on the real car
({doc}`erster-test`), with the engine running at standstill. Its responses come from the
traces of these tests:

- four control units, CAN 29 bit/500 kbit/s
- one stored code, U1218 (manufacturer-specific), with freeze frame
- readiness of a diesel, the exhaust gas sensor test is still incomplete
- VIN `WDD1770031J000000` (serial number redacted as in the traces)
- live data at idle: eleven recorded rounds that repeat

Not recorded are the values of PIDs 21, 30, 31, 49 and 66 (distance with warning lamp,
warm-ups and distance since clearing, accelerator pedal, air mass). The car reports them
as supported; in demo mode they hold made-up, plausible values. The used-car check is
therefore “inconspicuous” (40 warm-ups and 3000 km since clearing).

For the {doc}`batterie`, the demo vehicle's engine is off at first and starts by itself
about 3 s after the prompt. These voltages are made up but modelled on the traces: rest
12.55 V, a dip to 9.5 V according to the adapter, charging 14.8 V.

```{image} ../_static/screenshots/demo-live-daten-light.png
:alt: Live data in demo mode: engine speed around 860 rpm, coolant 62 °C
:class: only-light
```

```{image} ../_static/screenshots/demo-live-daten-dark.png
:alt: Live data in demo mode: engine speed around 860 rpm, coolant 62 °C
:class: only-dark
```

## What happens

Demo mode only replaces the adapter: instead of USB, a simulated ELM327
(`obd_diag.transport.demo`) answers byte for byte the way the real one did on the W177.
Driver, allowlist, decoding and user interface therefore run exactly as at the car,
including the trace with `--trace`. Requests without data are answered with `NO DATA`.

Clearing does not work in demo mode: it is locked anyway, and even when enabled it would
stop at the precondition "engine off", because the demo vehicle's engine is running.
