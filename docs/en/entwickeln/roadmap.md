# Roadmap

| Version | Content | Status |
| --- | --- | --- |
| v0.1 | trouble codes with plain text, readiness, freeze frame, VIN, safe clearing, sessions, PDF/CSV, trace, user interface | done |
| v0.2 | live data (116 values from 82 PIDs), CSV recording, tab “Live data”, `obd-diag live` | done, version 0.2.0 ({doc}`aenderungen`) |
| – | test on a real car (W177, 2026-10-09: reading verified), then enable clearing | clearing waits for approval ({doc}`../benutzen/erster-test`) |
| v0.3+ | Bluetooth LE, OBDb profiles (mind CC-BY-SA), SocketCAN and UDS with control unit discovery, read-only | planned, only after the test at the car |

Confirmed on the W177 are the CAN mode 03 format without headers (with count byte) and
the freeze frame format `02xx00`. Still to be settled on real hardware: whether the
ELM327 keeps listening after `7F 04 78`, the dark theme on a real dark desktop and
`list_ports` with further hardware.

Service functions (routines, coding) deliberately stay out. Draft, safety concept for
UDS (allowlist, locked services, address search only in the diagnostic range) and
roadmap are in the design document “OBD-Diagnose – Designvorschlag” (German).
