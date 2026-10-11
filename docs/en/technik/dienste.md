# OBD-II services

OBD-II according to SAE J1979 (ISO 15031-5) has “services” (modes). obd-diag only uses
the read services and the one writing service, mode 04. A positive response starts
with the mode plus `40` (`01` → `41`), a rejection with `7F` ({doc}`antwortformate`).

| Mode | Request | Response | Used for | Code |
| --- | --- | --- | --- | --- |
| 01 | `01 <PID>` | `41 <PID> <data>` | current values, supported PIDs, readiness | `protocol/pids.py`, `services/readiness.py` |
| 02 | `02 <PID> 00` | `42 <PID> 00 <data>` | freeze frame 00 | `protocol/obd.py` |
| 03 | `03` | `43 [count] <codes>` | stored trouble codes | `protocol/obd.py`, `dtc_decode.py` |
| 04 | `04` | `44` | clear trouble codes (locked) | `protocol/obd.py`, `services/clear.py` |
| 07 | `07` | `47 [count] <codes>` | pending trouble codes | like 03 |
| 09 | `0902` | `49 02 01 <17 characters>` | VIN | `services/vehicle.py` |
| 0A | `0A` | `4A [count] <codes>` | permanent trouble codes | like 03 |

The number of codes is only part of the response with CAN ({doc}`antwortformate`).

## Mode 01: current data

**Supported PIDs.** `0100` returns four bytes, one bit per PID: bit 7 of A is PID 01,
bit 0 of D is PID 20. If PID 20 is set, `0120` returns the next 32 PIDs, then `0140`
and so on. obd-diag asks all control units and merges the results.

```text
4100BE3FA813   → A=BE B=3F C=A8 D=13: PIDs 01, 03, 04, 05, 06, 07 and others supported
```

**Values.** Every PID has a fixed conversion, e.g. `410C1AF8` → (256 · 0x1A + 0xF8) / 4
= 1726 rpm. All 116 values with formula: {doc}`pids`.

**Readiness (PID 01).** Four data bytes A to D:

| Bits | Meaning |
| --- | --- |
| A7 | check engine light (MIL) on |
| A6 to A0 | number of stored codes of this control unit |
| B3 | 0 = spark ignition, 1 = diesel |
| B0 to B2 / B4 to B6 | misfire, fuel system, components: supported / incomplete |
| C, D | bit n: monitor n supported / incomplete; meaning depends on the engine type (catalyst, oxygen sensor, EGR … or NOx, particulate filter, boost pressure …) |

If several control units answer, the worst state counts per monitor, the MIL is on if
one of them reports it, and the code counts are added up.

## Mode 02: freeze frame

When a code is stored, the control unit records some values. obd-diag reads frame 00:
PID 02 (triggering code), 04 (load), 05 (coolant), 0C (engine speed), 0D (speed).

```text
020200 → 4202000420   42 02 00: response to PID 02, frame 00; 04 20: triggering code P0420
```

According to J1979 the frame number belongs in the request (`020C00`). If the vehicle
answers `020200` with `NO DATA` or `7F 02 12`, `0202` without frame number is tried
(like python-OBD); if that works, the whole freeze frame is read that way. Which form a
vehicle expects can only be found out on the real device.

## Mode 03, 07, 0A: trouble codes

A code consists of two bytes (SAE J2012):

```{mermaid}
flowchart LR
    B["04 20"] --> H["first byte 0x04 = 0000 0100"]
    H --> S["bits 7-6: 00 → P"]
    H --> D1["bits 5-4: 00 → 0"]
    H --> D2["bits 3-0: 0100 → 4"]
    B --> L["second byte 0x20 → 20"]
    S & D1 & D2 & L --> C["P0420"]
```

| Bits 7–6 of the first byte | Letter | Area |
| --- | --- | --- |
| `00` | P | powertrain |
| `01` | C | chassis |
| `10` | B | body |
| `11` | U | network |

Example: `C1 00` → bits 7–6 `11` = U, then `0`, `1`, `00` → **U0100**.

## Mode 04: clearing

Clears stored and pending codes, freeze frame and readiness. Permanent codes remain.
According to SAE a scan tool must ask for confirmation first (datasheet p. 35). Mode 04
is locked and only reachable through the procedure under
{doc}`../benutzen/fehlercodes`; the safeguards are described in {doc}`sicherheit`.

## Mode 09: VIN

`0902` returns the vehicle identification number, with CAN in several frames:

```text
014
0: 490201575657
1: 5A5A5A314B5A36
2: 57313233343536
```

0x014 = 20 bytes: `49 02` (response, InfoType 02), `01` (one data item), then 17 ASCII
characters **WVWZZZ1KZ6W123456**. Older protocols return five lines
`49 02 <1–5> <4 bytes>`, the first padded with `00` at the front. The request is
exactly `0902`; `09021` (with a wrong response count) can cause problems according to
the datasheet and is forbidden. The decoding is described in {doc}`fin`.
