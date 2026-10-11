# Architektur

obd-diag ist in vier Schichten gebaut. Jede Schicht kennt nur die darunter, nie die
darüber ({doc}`../adr/0001-schichtenarchitektur`).

```{mermaid}
flowchart TB
    UI["<b>ui/, cli.py</b><br>View-Models, QML, Befehle"]
    SVC["<b>services/</b><br>scan, run_diagnosis, clear_codes, run_live"]
    PROT["<b>protocol/</b><br>Elm327 mit Freigabeliste, frames, headers, obd, pids"]
    TRANS["<b>transport/</b><br>Transport, SerialTransport, Mitschnitt"]
    DATA["<b>data/</b><br>Katalog, WMI"]
    EXP["<b>export/</b><br>PDF, CSV"]
    UI --> SVC --> PROT --> TRANS
    SVC --> DATA
    UI --> EXP
    TRANS --> ADAPTER(["ELM327-Adapter"])
```

| Schicht | Aufgabe | Wichtigste Teile |
| --- | --- | --- |
| `transport/` | Bytes schreiben und bis zum Prompt `>` lesen | `Transport` (Protocol mit `open`, `close`, `write`, `read_until`), `SerialTransport`, `list_ports`, Mitschnitt und Wiedergabe |
| `protocol/` | Befehle senden, Antworten bereinigen und dekodieren | `Elm327` mit Freigabeliste, `split_messages`, `parse_header_response`, `read_dtcs`, `read_freeze_frame`, `PIDS` |
| `services/` | ganze Abläufe: Scan, Diagnose, Löschen, Live | `scan`, `run_diagnosis`, `clear_codes`, `run_live`, `read_vin`, Sitzungen und Ablage |
| `ui/`, `cli.py` | Anzeige und Bedienung | View-Models, QML, `obd-diag`-Befehle |

Das Übersetzungsmodul `obd_diag.i18n` gehört zu keiner Schicht und importiert nichts aus
dem Paket; jede Schicht darf es benutzen ({doc}`../benutzen/sprache`).

## Regeln, die Tests prüfen

- **Keine Importe nach oben.** `transport` importiert nichts aus `protocol` oder
  `services`; höhere Schichten kennen nur das `Transport`-Protocol, nie pyserial.
- **Ein Weg zum Adapter.** Nur `Elm327.command` schreibt an den Transport, und nur
  Befehle der Freigabeliste ({doc}`sicherheit`). Ein Architekturtest prüft per AST, wer
  `.transport`, `.write(` und pyserial erreicht.
- **Austauschbarer Transport.** Tests laufen gegen `FakeTransport`, den
  ELM327-emulator am pty oder `ReplayTransport` mit echten Mitschnitten
  ({doc}`../entwickeln/testen`).

## Oberfläche

Die Oberfläche ist QML mit Python-View-Models (`DiagnosisViewModel`,
`LiveViewModel`). Jeder Job läuft in einem einzigen Worker-Thread, öffnet Port und
Katalog dort und schließt sie wieder (SQLite-Verbindungen sind threadgebunden). Weil
es nur einen Thread gibt, laufen nie zwei Aktionen gleichzeitig am Adapter. Live-Daten
sind ein langer Job in diesem Thread; Werte kommen per Signal zurück, gestoppt wird über
ein `threading.Event`.
