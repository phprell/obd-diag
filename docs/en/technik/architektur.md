# Architecture

obd-diag is built in four layers. Each layer only knows the ones below it, never the
ones above ({doc}`../adr/0001-schichtenarchitektur`).

```{mermaid}
flowchart TB
    UI["<b>ui/, cli.py</b><br>view models, QML, commands"]
    SVC["<b>services/</b><br>scan, run_diagnosis, clear_codes, run_live"]
    PROT["<b>protocol/</b><br>Elm327 with allowlist, frames, headers, obd, pids"]
    TRANS["<b>transport/</b><br>Transport, SerialTransport, trace"]
    DATA["<b>data/</b><br>catalog, WMI"]
    EXP["<b>export/</b><br>PDF, CSV"]
    UI --> SVC --> PROT --> TRANS
    SVC --> DATA
    UI --> EXP
    TRANS --> ADAPTER(["ELM327 adapter"])
```

| Layer | Task | Main parts |
| --- | --- | --- |
| `transport/` | write bytes and read up to the prompt `>` | `Transport` (Protocol with `open`, `close`, `write`, `read_until`), `SerialTransport`, `list_ports`, trace and replay |
| `protocol/` | send commands, clean up and decode responses | `Elm327` with allowlist, `split_messages`, `parse_header_response`, `read_dtcs`, `read_freeze_frame`, `PIDS` |
| `services/` | whole procedures: scan, diagnosis, clearing, live | `scan`, `run_diagnosis`, `clear_codes`, `run_live`, `read_vin`, sessions and storage |
| `ui/`, `cli.py` | display and operation | view models, QML, `obd-diag` commands |

The translation module `obd_diag.i18n` belongs to no layer and imports nothing from the
package; every layer may use it ({doc}`../benutzen/sprache`).

## Rules checked by tests

- **No imports upwards.** `transport` imports nothing from `protocol` or `services`;
  higher layers only know the `Transport` protocol, never pyserial.
- **One way to the adapter.** Only `Elm327.command` writes to the transport, and only
  commands on the allowlist ({doc}`sicherheit`). An architecture test checks via the
  AST who reaches `.transport`, `.write(` and pyserial.
- **Exchangeable transport.** Tests run against `FakeTransport`, the ELM327-emulator on
  a pty or `ReplayTransport` with real traces ({doc}`../entwickeln/testen`).

## User interface

The user interface is QML with Python view models (`DiagnosisViewModel`,
`LiveViewModel`). Every job runs in a single worker thread, opens port and catalog
there and closes them again (SQLite connections are bound to their thread). Because
there is only one thread, two actions never run on the adapter at the same time. Live
data is one long job in this thread; values come back via a signal, stopping goes
through a `threading.Event`.
