# Safety concept

obd-diag should not be able to change anything in the car. For that there is a hard
limit in the code and tests that check it from several sides
({doc}`../adr/0002-nur-lesend`).

## Allowlist

`Elm327.command` in `protocol/elm327.py` is the only way to the adapter. It only lets
through:

- adapter commands that send nothing to the vehicle: `ATZ`, `ATE0`, `ATL0`, `ATS0`,
  `ATH0`, `ATH1`, `ATSP0`, `ATRV`, `ATDP`, `ATDPN`;
- read OBD requests: `01xx`, `02xx` and `02xx00`, `03`, `07`, `0A`, `0902`;
- `04` (clearing) only inside `with elm.allow_clear()`, and only `obd.clear_dtcs`
  uses that.

Everything else raises `ForbiddenCommandError` **before** anything is sent, including
lower case, spaces or an appended second command. The exception is deliberately not an
`ElmError`, so that no caller catches it as “value not available”. Every entry is
backed by a datasheet page in the {doc}`befehle`.

## Pace

There is only ever one request in flight: `Elm327.command` sends and then reads up to
the prompt `>`; only then can the next one go out. If a control unit reports
`7F xx 78` (response pending), reading continues without sending again. Behind this is
a hard minimum gap: between the end of a response and the next request to the vehicle
(anything except `AT…`) there are at least `MIN_REQUEST_GAP` = 50 ms, so at most 20
requests per second, also after errors and timeouts and also when an adapter sends the
prompt too early. The standard would not even need a gap: according to ISO 15765-4 the
next request may follow immediately, and with K-Line the ELM327 keeps the minimum gap
P3 itself. Live data additionally queries at most one round every 0.1 s, below 11.8 V
only every 5 s. After a connection error or a missing response the procedure aborts
and sends nothing more.

## Clearing

```{mermaid}
flowchart TD
    G{CLEAR_ENABLED?} -- no --> X[Abort before opening the port]
    G -- yes --> S[Scan and preconditions]
    S --> F[Freeze frame]
    F --> B[Backup on disk]
    B --> M[Mode 04, exactly once]
    M --> K[Check scan]
```

- **Lock:** `CLEAR_ENABLED = False` in `services/clear.py` until reading has been
  verified on a real car. It is only enabled after an explicit decision.
- **Preconditions:** `0100` answers (ignition on), voltage not below 11.8 V (if `ATRV`
  shows less, the control module voltage PID 42 decides, if plausible), engine speed of
  every control unit validly 0. An unreadable engine speed is an abort.
- **Backup first:** mode 04 only goes out once the backup is completely on disk;
  existing backups are never overwritten.
- **Never repeated:** mode 04 is sent at most once, also with `7F 04 78`.

## How this is checked

| Guarantee | Test |
| --- | --- |
| allowlist == specification from the datasheet, exhaustively over all hex commands | `tests/verification/test_command_spec.py` |
| every command sent in any test is in the specification | `_commands_match_spec` in `tests/conftest.py` |
| arbitrary text is allowed or never sent (Hypothesis); exact command sequence of every function | `tests/unit/test_command_guard.py` |
| clearing with arbitrarily broken responses: `04` at most once, only with fulfilled preconditions and an existing backup | `tests/unit/test_write_safety.py` |
| for every error case before clearing: `04` is never sent | `tests/unit/test_clear.py` |
| live data with arbitrary responses: only `01xx` and `ATRV`, never `04` | `tests/unit/test_live_safety.py` |
| call graph (AST): only intended places reach `allow_clear`, `clear_dtcs`, `clear_codes`, `.transport` and pyserial; no sockets, no `os.write`, no `eval` | `tests/unit/test_write_safety.py` |
| the shipped lock works in CLI and user interface | `tests/unit/test_clear_disabled.py` |
| never a request before the previous response, at least 50 ms between requests to the vehicle, also after errors, for a whole diagnosis and arbitrary command sequences | `tests/unit/test_request_gap.py` |
| line format: upper case, digits, exactly one CR, also via the real serial transport | `FakeTransport` and fixture in `tests/conftest.py` |

Mutation tests on `services/clear.py` detect all mutants; in `elm327.py` and `obd.py`
only mutants of texts and timeouts survive on the clearing path.

## What is deliberately missing

No coding, no flashing, no manufacturer commands, no service functions (routines,
actuators, adaptation values), no mode 08 (controlling components). Later extensions
such as UDS remain read-only and go through their own allowlist
({doc}`../entwickeln/roadmap`).
