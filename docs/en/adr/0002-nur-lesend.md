# ADR 0002: Read-only, with an allowlist in the driver

Status: accepted (2026-10-07)

## Context
The tool is connected to real vehicles. A wrong command can misconfigure control units:
start routines (e.g. particulate filter regeneration), control actuators, write values
or flash. Manufacturer tools (Xentry, ODIS, ISTA) protect such operations with
preconditions, security access and defined procedures; this project cannot provide
that. The benefit lies in understanding: reading, explaining and documenting faults.

## Decision
- The tool is read-only by default. The only writing OBD command is mode 04 (clear
  trouble codes), only via `services/clear.py` with preconditions (ignition on, engine
  speed 0 at all responding control units, battery voltage not too low), confirmation
  and a prior backup.
- Until reading has been verified on a real vehicle, clearing is locked as well
  (`CLEAR_ENABLED = False`, addendum 2026-10-08). The lock applies in `clear_codes`
  before the first request; CLI and GUI do not even offer it. The tests of the clearing
  procedure keep running with clearing enabled, so that it is verified when it is
  enabled.
- `Elm327.command` is the only way to the adapter and checks every command against an
  allowlist (adapter commands and read OBD requests). Everything else is rejected
  before sending (`ForbiddenCommandError`, deliberately not an `ElmError`, so that no
  caller catches it as “value missing”). `04` is only enabled inside
  `with elm.allow_clear()`, and only `clear_dtcs` uses that.
- No coding, no flashing, no service functions (UDS `10`, `11`, `14`, `27`, `2E`, `2F`,
  `31`, `34`–`37`, `85`), not later with UDS either. An extension with read UDS
  services (`19`, `22`, `3E`) enables them individually in the allowlist.

## Consequences
- New commands must be added to the allowlist deliberately; the test for it shows
  every change.
- Tests secure the limit: Hypothesis over arbitrary text, exact command sequences per
  function, clearing with arbitrarily broken responses, call graph via AST, mutation
  tests for driver and clearing. A test that gets in the way is not bypassed but taken
  seriously.
- Service functions remain reserved for workshops or licensed testers.
