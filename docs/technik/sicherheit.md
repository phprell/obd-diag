# Sicherheitskonzept

obd-diag soll am Auto nichts verändern können. Dafür gibt es eine harte Grenze im Code
und Tests, die sie von mehreren Seiten prüfen ({doc}`../adr/0002-nur-lesend`).

## Freigabeliste

`Elm327.command` in `protocol/elm327.py` ist der einzige Weg zum Adapter. Er lässt nur
durch:

- Adapter-Befehle, die nichts ans Fahrzeug senden: `ATZ`, `ATE0`, `ATL0`, `ATS0`,
  `ATH0`, `ATH1`, `ATSP0`, `ATRV`, `ATDP`, `ATDPN`;
- lesende OBD-Anfragen: `01xx`, `02xx` und `02xx00`, `03`, `07`, `0A`, `0902`;
- `04` (Löschen) nur innerhalb von `with elm.allow_clear()`, und das nutzt nur
  `obd.clear_dtcs`.

Alles andere wirft `ForbiddenCommandError`, **bevor** etwas gesendet wird, auch
Kleinschreibung, Leerzeichen oder ein angehängter zweiter Befehl. Die Ausnahme ist
bewusst kein `ElmError`, damit sie kein Aufrufer als „Angabe nicht verfügbar“
abfängt. Jeder Eintrag ist in der {doc}`befehle` mit Datenblatt-Seite belegt.

## Tempo

Es ist immer nur eine Anfrage unterwegs: `Elm327.command` sendet und liest dann bis zum
Prompt `>`, erst danach kann die nächste hinaus. Meldet ein Steuergerät `7F xx 78`
(Antwort folgt), wird ohne erneutes Senden weitergelesen. Dahinter steht eine harte
Mindestpause: zwischen dem Ende einer Antwort und der nächsten Anfrage ans Fahrzeug
(alles außer `AT…`) liegen mindestens `MIN_REQUEST_GAP` = 50 ms, also höchstens 20
Anfragen je Sekunde, auch nach Fehlern und Timeouts und auch, wenn ein Adapter den
Prompt zu früh schickt. Normgerecht wäre auch keine Pause nötig: nach ISO 15765-4 darf
die nächste Anfrage sofort folgen, bei K-Line hält der ELM327 die Mindestpause P3
selbst ein. Live-Daten fragen zusätzlich höchstens alle 0,1 s eine Runde ab, unter
11,8 V nur alle 5 s. Nach einem Verbindungsfehler oder einer ausbleibenden Antwort
bricht der Ablauf ab und sendet nichts mehr.

## Löschen

```{mermaid}
flowchart TD
    G{CLEAR_ENABLED?} -- nein --> X[Abbruch vor dem Öffnen des Ports]
    G -- ja --> S[Scan und Vorbedingungen]
    S --> F[Freeze Frame]
    F --> B[Sicherung auf der Platte]
    B --> M[Mode 04, genau einmal]
    M --> K[Kontroll-Scan]
```

- **Sperre:** `CLEAR_ENABLED = False` in `services/clear.py`, bis das Lesen am echten
  Auto geprüft ist. Freigegeben wird nur nach ausdrücklicher Entscheidung.
- **Vorbedingungen:** `0100` antwortet (Zündung an), Spannung nicht unter 11,8 V,
  Drehzahl jedes Steuergeräts gültig 0. Eine unlesbare Drehzahl ist ein Abbruch.
- **Sicherung zuerst:** Mode 04 geht erst hinaus, wenn die Sicherung vollständig auf der
  Platte liegt; vorhandene Sicherungen werden nie überschrieben.
- **Nie wiederholt:** Mode 04 wird höchstens einmal gesendet, auch bei `7F 04 78`.

## Wie das geprüft wird

| Garantie | Test |
| --- | --- |
| Freigabeliste == Spezifikation aus dem Datenblatt, erschöpfend über alle Hex-Befehle | `tests/verification/test_command_spec.py` |
| jeder in irgendeinem Test gesendete Befehl steht in der Spezifikation | `_commands_match_spec` in `tests/conftest.py` |
| beliebiger Text ist freigegeben oder wird nie gesendet (Hypothesis); exakte Befehlsfolge jeder Funktion | `tests/unit/test_command_guard.py` |
| Löschen mit beliebig kaputten Antworten: `04` höchstens einmal, nur bei erfüllten Vorbedingungen und vorhandener Sicherung | `tests/unit/test_write_safety.py` |
| für jeden Fehlerfall vor dem Löschen: `04` wird nie gesendet | `tests/unit/test_clear.py` |
| Live-Daten mit beliebigen Antworten: nur `01xx` und `ATRV`, nie `04` | `tests/unit/test_live_safety.py` |
| Aufrufgraph (AST): nur vorgesehene Stellen erreichen `allow_clear`, `clear_dtcs`, `clear_codes`, `.transport` und pyserial; keine Sockets, kein `os.write`, kein `eval` | `tests/unit/test_write_safety.py` |
| ausgelieferte Sperre wirkt in CLI und Oberfläche | `tests/unit/test_clear_disabled.py` |
| nie eine Anfrage vor der vorigen Antwort, mindestens 50 ms zwischen Anfragen ans Fahrzeug, auch nach Fehlern, für eine ganze Diagnose und beliebige Befehlsfolgen | `tests/unit/test_request_gap.py` |
| Leitungsformat: Großbuchstaben, Ziffern, genau ein CR, auch über den echten seriellen Transport | `FakeTransport` und Fixture in `tests/conftest.py` |

Mutationstests auf `services/clear.py` erkennen alle Mutanten; in `elm327.py` und
`obd.py` überleben beim Löschpfad nur Mutanten an Texten und Wartezeiten.

## Was bewusst fehlt

Keine Codierung, kein Flashen, keine Herstellerbefehle, keine Servicefunktionen
(Routinen, Aktoren, Anlernwerte), kein Mode 08 (Bauteile ansteuern). Spätere
Erweiterungen wie UDS bleiben lesend und laufen über eine eigene Freigabeliste
({doc}`../entwickeln/roadmap`).
