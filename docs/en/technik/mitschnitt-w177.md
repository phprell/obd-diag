# Trace from the W177, line by line

This page goes through the traces of the first test on a real car: Mercedes A 180 d
(W177, diesel), adapter FORScan ELMconfig (ELM327 v1.5, CH340) at `/dev/ttyUSB0`,
38400 baud, engine off, ignition on, 2026-10-09. How the test went and what was fixed
afterwards is described under {doc}`../benutzen/erster-test`.

:::{note}
**Redacted.** The serial number of the VIN (positions 12 to 17) is replaced with
`000000`, in the hex bytes as well as in plain text. Otherwise the lines are unchanged.
All byte examples on this page are checked against the code by
`tests/unit/test_docs_examples.py`; the whole diagnosis trace runs as a regression test
(`tests/verification/test_real_car.py`, traces in
`tests/fixtures/traces/mercedes_w177/`).
:::

Each line of a trace (`--trace`) has the time since opening the port in seconds, the
direction (`>>` to the adapter, `<<` from the adapter) and the text, with `\r` for the
line break. `>` at the end is the prompt: the adapter is ready for the next command.
The format is described under {doc}`../benutzen/sitzungen`.

## Connection setup

```text
    0.016 >> ATZ\r
    0.817 << \r\rELM327 v1.5\r\r>
    0.817 >> ATE0\r
    0.825 << ATE0\rOK\r\r>
    0.826 >> ATL0\r
    0.834 << OK\r\r>
    0.834 >> ATS0\r
    0.843 << OK\r\r>
    0.843 >> ATH0\r
    0.852 << OK\r\r>
    0.852 >> ATSP0\r
    0.861 << OK\r\r>
    0.861 >> ATRV\r
    0.875 << 11.2V\r\r>
```

`ATZ` resets the adapter and takes about 0.8 s. The response to `ATE0` (echo off) still
includes the echo, after that no more. `ATL0`, `ATS0`, `ATH0` switch off linefeed,
spaces and headers, `ATSP0` selects the automatic protocol search ({doc}`elm327`).
`ATRV` is the voltage the adapter itself measures at pin 16: **11.2 V**.

Right after plugging in, the very first command looked different:

```text
    0.024 >> ATZ\r
    0.033 << ATZ\r?\r\r>
```

The adapter rejected the first `ATZ` with `?`, probably because of a garbage byte in
the adapter's buffer. Since this test, an `ATZ` answered with `?` is repeated once; the
second time it returned `ELM327 v1.5` as above.

## Protocol search and four control units

```text
    0.925 >> 0100\r
    1.255 << SEARCHING...\r410098180001\r410098180001\r410098180011\r410098180001\r\r>
    1.255 >> ATDPN\r
    1.264 << A7\r\r>
    1.264 >> ATDP\r
    1.282 << AUTO, ISO 15765-4 (CAN 29/500)\r\r>
```

The first `0100` starts the protocol search (`SEARCHING...`); after 0.3 s it is done.
Then **four lines** follow: four control units answer the request, each with its list
of supported PIDs. `ATDPN` reports `A7`: `A` means “found automatically”, `7` is
ISO 15765-4 CAN with 29-bit addresses and 500 kbit/s. Hence `can=True`, so the count
byte is read for trouble codes ({doc}`antwortformate`).

One line taken apart:

| Bytes | Meaning |
| --- | --- |
| `41 00` | positive response to mode 01, PID 00 |
| `98 18 00 01` | bit mask: PIDs `01`, `04`, `05`, `0C`, `0D` and `20` (next list) |
| `98 18 00 11` | the same, one of the control units additionally reports `1C` (OBD standard) |

The order of the four lines is not fixed: in the live trace the same response came as
`…0001`, `…0011`, `…0001`, `…0001`. Without headers a line can therefore not be
assigned to a control unit. obd-diag treats each line as a response of its own and
evaluates it by content, not by position.

Fewer and fewer control units answer the further lists (`0120` to `0180`, in the live
trace). Together the vehicle offers these PIDs:

| List | Responses | supported PIDs |
| --- | --- | --- |
| `0100` | 4 | `01`, `04`, `05`, `0C`, `0D`, `1C` (one), `20` |
| `0120` | 4 | `21`, `30`, `31`, `40` (three) |
| `0140` | 3 | `41`, `42`, `49` (one), `60` (two) |
| `0160` | 2 | `66`, `68`, `6C`, `6D`, `70` (one), `80` |
| `0180` | 2 | `85`, `88`, `92` |

obd-diag does not read `68`, `6C`, `6D`, `70`, `85`, `88` and `92` yet: they are PIDs
with a status byte whose structure is not unambiguous in free sources (list in the
docstring of `protocol/pids.py`). That the W177 offers `70` (boost pressure control),
for example, makes it a good candidate for settling the format later with real
responses.

## Control module voltage (PID 42)

```text
    1.290 >> 0142\r
    1.320 << 41422EDC\r\r>
```

Only one control unit answers. `2E DC` is 11996 mV, so **12.0 V**, a good 0.8 V more
than the 11.2 V from `ATRV`. The adapter measures at pin 16, often behind a protection
diode, and therefore shows less. Since this test obd-diag reads PID 42 when `ATRV` is
below 11.8 V and takes the control unit's value if it is plausible (5 to 30 V). This
query did not exist yet in the test itself; the line comes from the live trace of the
same session and is inserted at this point in the regression test.

## Trouble codes

```text
    1.332 >> 03\r
    1.562 << 4301D218\r4300\r4300\r4300\r\r>
    1.612 >> 07\r
    1.701 << 4700\r4700\r4700\r4700\r\r>
    1.752 >> 0A\r
    1.837 << 4A00\r\r>
```

| Response | Meaning |
| --- | --- |
| `43 01 D2 18` | mode 03, count byte `01`, one code: `D2 18` |
| `43 00` | three control units: no stored codes |
| `47 00` (four times) | no pending codes (mode 07) |
| `4A 00` | no permanent codes (mode 0A); only one control unit answers |

`D2 18` decoded: the upper two bits of `D` (`11`) give `U` (network), the next two
(`01`) the `1`, the rest `2 18`: **U1218**. The `1` in second place means
manufacturer-specific, which is why the catalog has no text for it
({doc}`../benutzen/fehlercodes`).

## Readiness (mode 01 PID 01)

```text
    1.887 >> 0101\r
    1.973 << 4101000EEB20\r410100040000\r4101000C0200\r4101010C0000\r\r>
```

Byte A is the check engine light (bit 7) and the number of codes (bits 0 to 6), B the
general monitors and the engine type (bit 3), C and D the engine-specific monitors
({doc}`dienste`).

| Response | MIL | Codes | Diesel | Monitors |
| --- | --- | --- | --- | --- |
| `00 0E EB 20` | off | 0 | yes | six complete, exhaust gas sensor incomplete |
| `00 04 00 00` | off | 0 | no | components only |
| `00 0C 02 00` | off | 0 | yes | components, NOx aftertreatment |
| `01 0C 00 00` | off | 1 | yes | components only |

The first is probably the engine control unit, the fourth the one with U1218 (it
reports the one code). The order of the lines means nothing, though: without headers
the responses come sorted differently for every request (in the second test
`00 04 00 00` was in front as well). obd-diag combines the four (`combine_readiness`):
the engine type is decided by the control units that support engine-specific monitors
(byte C not 0), here the three diesel lines. The second control unit does report bit
B3 = 0, but has no monitors in C/D and therefore only counts for the general monitors
from byte B. Per monitor the worst state counts, the MIL is on if one reports it, and the
codes are added up. Result: check engine light off, one code, diesel, everything
complete except the exhaust gas sensor, misfire not supported.

## Freeze frame (mode 02)

```text
    2.023 >> 020200\r
    2.091 << 4202000000\r4202000000\r4202000000\r420200D218\r\r>
    2.141 >> 020400\r
    2.201 << 42040000\r\r>
    2.251 >> 020500\r
    2.312 << 4205004D\r\r>
    2.362 >> 020C00\r
    2.422 << 420C000000\r\r>
    2.473 >> 020D00\r
    2.529 << 420D0000\r\r>
```

`020200` asks which code triggered the freeze frame (frame `00`). Three control units
answer `00 00`: no freeze frame. One answers `D2 18`, so U1218. Before this test
obd-diag took the first line and showed “unknown”; now the line with a code counts.

The values after that only come from this one control unit: load `00` = 0 %, coolant
`4D` = 77 − 40 = **37 °C**, engine speed `00 00` = 0, speed `00` = 0. So the code was
stored while stationary with a half-warm engine.

## VIN (mode 09 PID 02)

```text
    2.579 >> 0902\r
    2.656 << 014\r0:490201574444\r1:31373730303331\r2:4A303030303030\r\r>
```

A multi-frame ISO-TP response in ELM format ({doc}`antwortformate`): `014` is 20 bytes
of payload, namely `49 02 01` (response, PID, count) and the 17 characters of the VIN
in ASCII. Assembled: `WDD1770031J000000`.

| Positions | Value | Meaning |
| --- | --- | --- |
| 1 to 3 | `WDD` | manufacturer code (WMI): Mercedes-Benz, Germany |
| 4 to 9 | `177003` | vehicle description, including the model series 177 |
| 10 | `1` | at Mercedes the steering side, **not a model year** |
| 11 | `J` | at Mercedes the plant |
| 12 to 17 | `000000` | serial number, redacted here |

Before this test obd-diag showed the model year “2001” from position 10. European
Mercedes VINs do not encode a model year there, so obd-diag no longer shows one for
them ({doc}`fin`). A check digit (position 9) is not mandatory outside North America;
it “differs”, and that is not an error.

## Live data

`obd-diag live --duration 20 --record --trace`, engine off. Before the first round
`prepare_live` reads the PID lists `0100` to `0180` (above) and `ATRV` once. Of the
default values the W177 supports all except the intake air temperature (`0F`), which is
therefore dropped without a message. One round:

```text
    2.185 >> 010C\r
    2.254 << 410C0000\r410C0000\r410C0000\r410C0000\r\r>
    2.305 >> 010D\r
    2.373 << 410D00\r410D00\r410D00\r410D00\r\r>
    2.423 >> 0105\r
    2.488 << 41053B\r41053B\r41053B\r41053B\r\r>
    2.538 >> 0104\r
    2.602 << 410400\r410400\r410400\r410400\r\r>
    2.652 >> 0142\r
    2.709 << 41422EDC\r\r>
```

Engine speed 0, speed 0, coolant `3B` = 59 − 40 = **19 °C**, load 0 %, control module
voltage 12.0 V. There are a good 50 ms between two requests: that is the minimum gap
obd-diag keeps before every request to the vehicle ({doc}`sicherheit`).

The next rounds start at 7.1 s, 12.1 s and 17.1 s, so every 5 s instead of every
second. The reason was the throttling at low battery voltage: `ATRV` reported 11.2 V.
The control module voltage rose from 11.996 to 12.007 V over the four rounds. Since
this test, live data also cross-checks a low `ATRV` with PID 42; with 12.0 V from the
control unit it would not have been throttled.

## What the trace does not show

- **The first attempt.** With the ignition off, the protocol search after the first
  `0100` ended with `UNABLE TO CONNECT`; obd-diag aborted, only AT commands and this
  one `0100` had been sent. Only with the ignition on did the control units answer.
  Since PR #8 the message for this is “No control unit responds. Switch the ignition on
  (the engine may stay off), select HS-CAN on adapters with an MS/HS-CAN switch and try
  again.”
- **Which control unit is which.** That would need headers (`ATH1`): then every line
  carries its CAN address, for example `18 DA F1 10` ({doc}`antwortformate`). obd-diag
  only switches them on when multi-frame responses of several control units get mixed.
  That was not necessary here, because only the VIN is multi-frame and only one control
  unit sends it.
- **Clearing.** Mode 04 did not occur; clearing is still locked
  ({doc}`../benutzen/fehlercodes`).
