# First test at the car

The first test only reads and records everything, so that output and trace can be
checked afterwards. On 2026-10-09 it ran on a Mercedes A 180 d (W177); what stood out
is described below under “Result on the W177”.

:::{note}
The target of the first test is a Mercedes A 180 d (W177) with the FORScan ELMconfig
USB adapter (switch set to HS-CAN, {doc}`adapter`).
:::

## Procedure

1. Engine off, ignition on. With a start/stop button: press it without pressing the
   brake.
2. Plug in the adapter and find it:

   ```sh
   obd-diag ports
   obd-diag info --port /dev/ttyUSB0
   ```

3. Full diagnosis, read-only, with trace:

   ```sh
   obd-diag diagnose --port /dev/ttyUSB0 --save --trace
   ```

   The session is stored under `~/.local/share/obd-diag/sessions/`, the trace under
   `~/.local/share/obd-diag/traces/`.
4. Optionally with the engine running, while stationary:

   ```sh
   obd-diag live --port /dev/ttyUSB0 --duration 30 --record --trace
   ```

5. `clear` stays locked. Only once output and trace have been checked is clearing
   enabled, and the trace becomes a regression test via `ReplayTransport`
   ({doc}`../entwickeln/testen`).

## Caution, independent of the software

obd-diag only sends read requests, one after the other ({doc}`../technik/sicherheit`).
What can still go wrong is in the surroundings:

- **Adapter switch:** set it to HS-CAN before plugging in; plug in and unplug with the
  ignition off. If the tool finds nothing (“UNABLE TO CONNECT”), do not experiment with
  the switch; stop and look at the trace.
- **Battery:** ignition on without the engine draws current. Keep the test short (a
  diagnosis takes seconds), then ignition off. Below 12 V, better test with the engine
  running or a charger connected.
- **Unplug the adapter:** it is connected to permanent positive (pin 16) and can drain
  the battery overnight or keep control units awake.
- **Cable and engine:** keep the cable away from the pedals, live data only while
  stationary, run the engine only outdoors.
- **Adapter firmware:** obd-diag cannot control what happens inside the adapter.
  According to the datasheet an ELM327 only sends to the car what is requested; cheap
  clones do not always stick to that and sometimes answer incorrectly. That is what the
  trace is for.

## What is sent back

- the output of `diagnose`,
- the session file (`session-….json`),
- the traces (`trace-….log`).

The trace contains the VIN. It stays local until it is deliberately shared.

## If something goes wrong

If the connection is interrupted during a query (adapter unplugged, plug pulled from
the car), obd-diag aborts with “Connection to … lost” or “no response from …”. After
that nothing more is sent, and half a diagnosis is not saved. More cases:
{doc}`probleme`.

The standardised diagnostics only see emissions-related control units (engine,
transmission). Airbag, ABS and comfort electronics need manufacturer-specific
diagnostics; that is not built in yet.

## Result on the W177

Protocol ISO 15765-4 (CAN 29/500), four control units respond. Read were one stored
code (U1218, manufacturer-specific, therefore without text in the catalog), readiness
(diesel, only the exhaust gas sensor incomplete), freeze frame and VIN; live data ran
while stationary. What every line of the trace means is explained under
{doc}`../technik/mitschnitt-w177`.

### Procedure on 2026-10-09

| Step | Result |
| --- | --- |
| `obd-diag ports` | `/dev/ttyUSB0` found, access via the group `uucp` works |
| `obd-diag info` | first start right after plugging in: aborted because the adapter rejected the first `ATZ` with `?`; second start: ELM327 v1.5, 11.5 V |
| `obd-diag diagnose --save --trace` | **failed:** the protocol search ends with `UNABLE TO CONNECT` because the ignition was still off. Aborted; only AT commands and one `0100` had been sent |
| Ignition on, `diagnose` again | complete: CAN 29/500, four control units, U1218, readiness, freeze frame, VIN; 11.2 V according to the adapter |
| `obd-diag live --duration 20 --record --trace` | four rounds 5 s apart (throttled because of 11.2 V), engine speed 0, coolant 19 °C, control unit 12.0 V |
| End | ignition off, adapter unplugged; nothing cleared |

The battery voltage according to the adapter dropped from 11.5 V (`info`) to 11.2 V
(`diagnose`) in the few minutes with the ignition on; a PDF report shortly before shows
11.0 V. So the test really should stay short or run with a charger.

With the ignition off, `UNABLE TO CONNECT` is the normal case, not a fault of adapter or
tool: the adapter itself answers (permanent positive at pin 16), the control units are
asleep. obd-diag then reports “No control unit responds. Switch the ignition on (the
engine may stay off), select HS-CAN on adapters with an MS/HS-CAN switch and try
again.” (since PR #8). So switch the ignition on and start again; if the switch is
already on HS-CAN, do not keep trying with it.

### Fixed

Fixed afterwards (PR #7):

- The adapter answered the first `ATZ` after plugging in with `?`; it is now repeated
  once.
- Three control units report `00 00` in the freeze frame (no freeze frame), one U1218;
  the code is now shown instead of “unknown”.
- The adapter measured 11.2 V, the engine control unit 12.0 V; with a low `ATRV` the
  control module voltage (PID 42) now counts.
- Position 10 of the VIN is the steering side at Mercedes, not a model year (“2001” was
  wrong).

This is what `obd-diag diagnose --lang en` looks like with this state for the W177
trace (played back via `ReplayTransport`, VIN serial number redacted, without a built
trouble code catalog):

```text
Vehicle:
  VIN:          WDD1770031J000000
  Check digit:  not mandatory (differs)
  Manufacturer: Mercedes-Benz
  Country:      Germany

Adapter:         ELM327 v1.5
Protocol:        ISO 15765-4 (CAN 29/500)
Battery voltage: 12.0 V

Stored:
  U1218  (no description in the catalog)

Readiness (Diesel engine):
  Check engine light (MIL): off
  Reported trouble codes: 1
  Misfire:                  not supported
  Fuel system:              complete
  Components:               complete
  NMHC catalyst:            complete
  NOx aftertreatment (SCR): complete
  Boost pressure:           complete
  Exhaust gas sensor:       incomplete
  Particulate filter:       complete
  EGR system:               complete
  All tests complete: no

Freeze frame (triggered by U1218):
  Engine load:           0 %
  Coolant temperature:   37 °C
  Engine speed:          0 rpm
  Speed:                 0 km/h
```

At the car itself the output still showed “battery voltage 11.2 V” with a warning,
model year “2001” and a freeze frame without triggering code. The emissions test note
below the readiness is omitted here.

### Sharing traces

Session, PDF report and trace contain the full VIN. Before they go into a ticket, a
chat or the repository:

- redact the response to `0902` in the trace: with CAN, positions 11 to 17 of the VIN
  are in frame `2:` as ASCII hex; replace its last six bytes with `30` (character `0`),
  as in `tests/fixtures/traces/mercedes_w177/diagnose.log`,
- do the same with the field `vin` (plain text) in the session file and, if the online
  lookup was on, empty the section `online`,
- do not share the PDF report; create it again from the redacted session
  (`obd-diag export`, {doc}`sitzungen`).

Timestamp, port and adapter identification in the trace header are not critical.
