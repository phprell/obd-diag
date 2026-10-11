# Common problems

| Message or behaviour | Probable cause | What to do |
| --- | --- | --- |
| `obd-diag ports` finds nothing | adapter not plugged in, Bluetooth not bound | check `dmesg`; Bluetooth with `sudo rfcomm bind 0 <MAC>` |
| “Permission denied” when opening the port | user not in `uucp`/`dialout` | {doc}`installation`, section on permissions |
| `info` hangs or “no response from …” | wrong baud rate | try `--baud 115200` or `--baud 38400` |
| `UNABLE TO CONNECT`, no codes, no protocol | ignition off (exactly what happened in the first test on the W177), or the switch on the FORScan adapter set to MS-CAN | ignition on, switch to HS-CAN ({doc}`adapter`) |
| `info` shows a low voltage although the battery is fine | the adapter measures less at pin 16 than the control unit (W177: 11.2 instead of 12.0 V) | normal; with a low `ATRV` the control module voltage (PID 42) counts, if readable |
| First start after plugging in: error at `ATZ` (`?`) | garbage byte in the adapter right after plugging in | is now repeated once; otherwise simply start again |
| “Battery voltage too low” | battery below 11.8 V (adapter and, if readable, control unit both measure too little) | charge the battery; live data then only queries every 5 s |
| “Connection to … lost” | adapter unplugged, loose contact, Bluetooth gone | reconnect; nothing was sent after that |
| “Control unit rejects mode … (response 7F …)” | control unit busy or conditions not correct | try again later; the fault memory is then unknown, not empty |
| Codes without description, “Trouble code catalog missing” | catalog not built | `uv run python tools/build_dtc_db.py` |
| “Clearing is disabled” | intended, until the test at the car | {doc}`fehlercodes` |
| Live data ends with an error after three rounds | adapter only reports bus errors, usually ignition off | ignition on |
| Freeze frame “none stored” | no stored code | normal |

For anything not listed here: repeat with `--trace` and look at the trace
({doc}`sitzungen`).
