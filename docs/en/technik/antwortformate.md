# Response formats

After cleaning up ({doc}`elm327`), hex text remains. How to read it depends on whether
headers are on, whether the vehicle speaks CAN and whether the response fits into one
CAN frame. All examples on this page are checked against the code by
`tests/unit/test_docs_examples.py`.

## Without headers (normal case, `ATH0`)

### Single-frame response

Each line is the complete response of one control unit. If two control units answer
(engine and transmission), there are two lines.

```text
410C1AF8
```

| Bytes | Meaning |
| --- | --- |
| `41` | positive response to mode 01 (mode + `40`) |
| `0C` | PID 0C, engine speed |
| `1A F8` | data A, B: (256 · 26 + 248) / 4 = **1726 rpm** |

### Multi-frame CAN response (ISO-TP)

If a response does not fit into one CAN frame (more than 7 bytes), the ELM327
assembles it itself and writes it like this (datasheet p. 42 ff., “Multiline
Responses”):

```text
00A
0: 430404200133
1: 0300C100
```

| Line | Meaning |
| --- | --- |
| `00A` | length of the payload: 10 bytes (hex) |
| `0: 43 04 04 20 01 33` | frame 0: mode byte `43` (response to `03`), count byte `04`, first two codes |
| `1: 03 00 C1 00` | frame 1: two more codes; padding would be cut to the length |

Result: four stored codes **P0420, P0133, P0300, U0100**. The frame numbers run from 0
to F and then start at 0 again. If a frame is missing or the order is wrong,
`split_messages` raises a `FrameSequenceError` instead of returning wrong data.

## With headers (`ATH1`)

If two control units send multi-frame responses at the same time, the ELM327 mixes
their frames without headers (datasheet p. 45, example `09 04`); assigning them is then
impossible. obd-diag detects this from gaps in the numbering or incomplete messages and
repeats the read request once with headers. Afterwards `ATH0` is set again; if that
fails, the session aborts so that nobody keeps reading wrongly.

With headers every line carries its sender, and the frames are assembled per control
unit according to ISO 15765-2:

```text
7E8 10 0A 43 04 04 20 01 33
7E9 04 43 01 01 71
7E8 21 03 00 C1 00 00 00 00
```

| Part | Meaning |
| --- | --- |
| `7E8`, `7E9` | CAN ID (11 bit) of the responding control unit: engine and transmission |
| `10 0A` | PCI first frame: type 1, length 0x00A = 10 bytes; then 6 data bytes |
| `21` | PCI consecutive frame number 1; up to 7 data bytes, padding at the end is cut off |
| `04` | PCI single frame with 4 data bytes |

Result, ordered by control unit address: `7E8` reports P0420, P0133, P0300, U0100;
`7E9` reports P0171.

| Format | Example | Header |
| --- | --- | --- |
| CAN 11 bit | `7E8 06 41 00 BE 3F A8 13` | 3 hex digits CAN ID |
| CAN 29 bit | `18 DA F1 10 06 41 00 BE 3F A8 13` | 4 bytes, OBD responses `18 DA F1 <source>` |
| J1850, ISO 9141, KWP | `48 6B 10 41 00 BE 3E B8 11 FA` | 3 header bytes (priority, target, source), 1 checksum byte at the end |

The checksum byte of older protocols is removed but not checked; the ELM327 discards
faulty messages itself or reports `<DATA ERROR`.

## Trouble codes: the count byte with CAN

With CAN the mode byte is always followed by a count byte (SAE J1979), then two bytes
per code. Bytes after the counted codes are padding and are ignored; if there are too
few, the response is broken (`ValueError`). Older protocols have no count byte and pad
every line with `00 00` to three codes.

| Protocol | Response to `03` | Codes |
| --- | --- | --- |
| CAN | `430204200133` | count byte `02`: P0420, P0133 |
| CAN, no codes | `4300` | count byte `00`: none |
| J1850/ISO 9141/KWP | `43042001330000` | P0420, P0133; `00 00` is padding |

Whether it is CAN comes from `ATDPN` ({doc}`elm327`). A response without count byte
with CAN is deliberately not tolerated. The ELM327-emulator leaves out the count byte;
the tests correct this, not the product code ({doc}`../entwickeln/testen`).

## Rejections (`7F`)

A control unit can reject a request with `7F <Mode> <reason>` (codes according to
ISO 14229-1 / ISO 15031-5).

| Response | Meaning | obd-diag |
| --- | --- | --- |
| `7F 0A 11`, `7F 0A 12` | mode not supported | “no codes of this kind from this control unit” |
| `7F 03 21` | busy, repeat later | error, never “no codes” |
| `7F 04 22` | conditions not correct (e.g. engine running) | clearing aborts, the backup remains |
| `7F 03 78` | response pending | keep reading up to 5 s without sending again; any later positive response counts as the announced one |
