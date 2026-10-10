# Reading and clearing trouble codes

## Reading

```sh
obd-diag scan --port /dev/ttyUSB0
```

`scan` resets the adapter, lets it search for the vehicle protocol, measures the
battery voltage (warning below 11.8 V) and reads three kinds of trouble codes:

| Type | OBD service | Meaning |
| --- | --- | --- |
| stored | Mode 03 | confirmed fault; may switch on the check engine light |
| pending | Mode 07 | detected in the current or last drive cycle, not confirmed yet |
| permanent | Mode 0A | only the control unit itself clears it, once it sees the fault as fixed over drive cycles |

Each code comes with the plain text from the offline catalog: title, explanation,
possible causes with likelihood, symptoms and a cost range. For codes without a catalog
text there is an optional short, unverified online explanation
({doc}`fehlercodes-online`). How a code is made from two bytes is explained under
{doc}`../technik/dienste`.

If a control unit answers with “mode not supported” (`7F 0A 11`), it has no codes of
this kind. Any other rejection is an error and is never shown as “no codes”
({doc}`../technik/antwortformate`).

## Clearing

:::{warning}
**Clearing is currently locked** (`CLEAR_ENABLED = False` in `services/clear.py`).
`obd-diag clear` aborts without opening the port, and the button in the user interface
stays grey. It will only be enabled once reading has been verified on a real car. The
procedure below describes how it works afterwards; the tests already check it
completely today.
:::

Clearing (mode 04) is the only writing action and always runs in this order:

```{mermaid}
flowchart TD
    A[Scan: read codes] --> B{Codes present, ignition on,<br>engine off, voltage ok?}
    B -- yes --> C{Confirmation given?}
    C -- yes --> D[Read freeze frame]
    D --> E[Write backup]
    E -- on disk --> F[Send mode 04 once]
    F --> G[Check scan]
    B -- no --> X[Abort, nothing sent]
    C -- no --> X
    E -- failed --> X
```

- **Preconditions:** if the engine speed cannot be read, clearing is refused. If
  several control units answer (engine, transmission), each must validly report 0. If
  `ATRV` cannot be read, only a known low voltage blocks.
- **Backup:** scan result, freeze frame (raw and decoded), time, adapter and protocol
  to `~/.local/share/obd-diag/backups/`. Existing backups are never overwritten.
- **Rejection:** `7F 04 22` (conditions not correct) aborts with a message; the backup
  remains. If the control unit reports `7F 04 78` (response pending), obd-diag waits up
  to 10 s without sending again. Mode 04 is never repeated.

Clearing also deletes the freeze frame and readiness. If the fault is not fixed, the
codes come back.
