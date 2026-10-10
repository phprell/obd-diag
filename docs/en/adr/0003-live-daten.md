# ADR 0003: Live data as a long job in the single worker thread

Status: accepted (2026-10-08)

## Context
Live data (v0.2) queries mode 01 values continuously until the user stops. The user
interface runs all adapter actions in a thread pool with exactly one thread, so that
two actions never use the same port. A live run blocks this thread for minutes.

## Decision
- Live data runs as one job in the same thread (`backend.live_port`). Intermediate
  values reach the GUI thread via a Qt signal (queued) with a run number; values of an
  old run are discarded. Stopping goes through a `threading.Event` that `run_live`
  checks before every round and while waiting in steps of at most 0.1 s.
- Live data and diagnosis lock each other: while live data runs, scan, clearing and
  export are locked (`DiagnosisViewModel.blocked`), otherwise they would pile up in the
  one thread. Live data does not start while the diagnosis is busy. On exit, `app.py`
  stops live data before waiting for the pool.
- Only `01xx` and `ATRV` are sent. An unreadable value gives `None` for that round;
  connection errors abort, as do three consecutive rounds in which every value fails
  with an adapter error (`CAN ERROR`, `UNABLE TO CONNECT` ...: ignition off, bus gone).
  Below 11.8 V battery voltage values are only queried every 5 s to protect the
  battery; an unreadable voltage does not lift the throttling.
- The PID table (`protocol/pids.py`) has one entry (`PidSpec`) per value; several
  entries can share one PID (oxygen sensors, secondary trim, torque points, status
  byte PIDs `66`/`67`). `decode` gets the first `size` data bytes and may return
  `None` (status bit not set). Each PID is queried once per round. PIDs whose
  structure is not unambiguous in the free sources (`68`–`7F`, among them boost
  pressure `70` and particulate filter `7A`–`7C`) stay out.
- Curves scale to the values shown, with a minimum span; the full range of the
  standard (e.g. engine speed up to 16384 rpm) would make them look flat.

## Consequences
- No second thread, no synchronisation on the port; the lock is visible (greyed-out
  buttons) instead of a silent wait.
- The timing is testable with an injectable clock; a Hypothesis test checks that
  rounds never come faster than allowed.
