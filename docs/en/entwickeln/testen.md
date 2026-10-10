# Testing without a car

Almost everything is verified without hardware; in addition there is a regression test
from the first test on a real car (below). The tests are in four folders:

| Folder | Content |
| --- | --- |
| `tests/unit` | individual modules against `FakeTransport`; safety tests, Hypothesis |
| `tests/integration` | procedures against the ELM327-emulator on a pty, unplugged adapter |
| `tests/verification` | response handling against datasheet examples, real traces and python-OBD |
| `tests/ui` | user interface without a screen (`QT_QPA_PLATFORM=offscreen`) |

## Emulator

The [ELM327-emulator](https://github.com/Ircama/ELM327-emulator) provides a virtual
serial device:

```sh
uv run python tools/emulator.py                    # shows the pty, e.g. /dev/pts/5
uv run python tools/emulator.py --stored P0420,P0300 --pending P0171 --engine-off
uv run obd-diag diagnose --port /dev/pts/5
```

Version 4.0.0 deviates from the standard: no CAN count byte for trouble codes,
multi-frame responses without headers not in ELM format, mode 02 without frame number,
changing VIN, engine speed never 0. `tests/emulator_patches.py` corrects this (active in
all tests and used by `tools/emulator.py`), not the product code. `--engine-off`
reports engine speed 0 so that the clearing procedure can be tried out.

## Test helpers

- `tests/fakes.py`: `FakeTransport` answers from a table (`headers_on`, `later`,
  `after_clear`); `CAN_CAR`, `CAN_CAR_FULL` are ready-made vehicles.
- `tests/samples.py`: `full_session`, `minimal_session`.
- `tests/fixtures/traces/`: real traces from other programs with sources.
- `ReplayTransport` (`transport/trace.py`) plays back an own trace without an adapter.
  Every sent command must match the trace in order and wording.

## Regression test from the real car

`tests/verification/test_real_car.py` plays back the traces of the first test on the
Mercedes A 180 d (W177) (`tests/fixtures/traces/mercedes_w177/`, explained in
{doc}`../technik/mitschnitt-w177`) and checks the result: four control units, U1218,
readiness, freeze frame from the right control unit, voltage via PID 42, first `ATZ`
with `?`, no model year for Mercedes. Without the corrections made after the test it
fails.

If the command sequence changes (say, an additional query), the trace no longer fits.
Then insert the new line with a real response from a trace of the same session and note
that in the file header, as with `0142`. Own traces only go into the repository
redacted: replace the serial number of the VIN (positions 12 to 17) in frame `2:` of
the response to `0902` with `000000` ({doc}`../benutzen/erster-test`).

## Verification without hardware

- Examples from the ELM327 datasheet and user logs from python-OBD, ELMduino and
  AndrOBD run through the parsers.
- The DTC decoder is compared with that of python-OBD over all 65536 byte pairs, every
  PID formula over all byte values (`test_pid_differential.py`).
- Hypothesis round trips and fuzzing for frames, headers and responses.

## Mutation tests

```sh
uv run --with mutmut mutmut run       # targets in [tool.mutmut], about two minutes
uv run --with mutmut mutmut results
```

No mutant may survive in `services/clear.py`. Afterwards delete `mutants/`; the test
suite also creates `elm.log` (gitignored).
