# obd-diag

Linux-OBD-II-Diagnose-Tool für ELM327-kompatible Adapter: Kommandozeile und
PySide6/QML-Oberfläche, Python 3.12+. Repo: github.com/phprell/obd-diag (privat).

Entscheidungen: `docs/adr/` (0001 Schichten, 0002 nur lesend/Freigabeliste, 0003
Live-Daten). Nutzer-Doku: README.md (u. a. „Sicherheit: was das Tool senden kann“,
„Erster Test am Auto“, „Live-Daten“).

Spezifikation und Roadmap: Claude-Docs-Dokument „OBD-Diagnose – Designvorschlag“
https://claude.ai/code/artifact/de949a8a-4f58-41b4-a629-6b9d238bdac7 (über den
Claude-Docs-Connector lesen; Architektur- und Roadmap-Änderungen dort nachziehen).
Die Ideen stammen aus einem Cowork-Projekt, auf das Claude Code keinen Zugriff hat.

## Regeln
- Strikte Schichten: transport → protocol → services → ui/cli. Keine Importe nach oben
  (transport importiert nichts aus services). Höhere Schichten kennen nur das
  `Transport`-Protocol, nie pyserial direkt.
- Standardmäßig nur lesend. Einziger schreibender OBD-Befehl ist Mode 04 (Löschen),
  nur über `services/clear.py`. **Löschen ist gesperrt** (`CLEAR_ENABLED = False`),
  bis das Lesen am echten Auto geprüft ist; nur nach ausdrücklicher Freigabe durch den
  Nutzer einschalten. Keine Codierung, kein Flashen, keine
  Herstellerbefehle.
- Fehlercode-Texte offline (SQLite). Online nur NHTSA vPIC, nur nach Opt-in.
- Parser halten sich strikt an SAE J1979 / ISO 15765 / ELM327-Datenblatt; Abweichungen
  des Emulators werden in den Tests korrigiert, nicht im Produktcode.
- Sprache in UI, Doku, Kommentaren, Commit-Messages: Deutsch. ruff meldet Gedanken-
  striche (–) in Python-Strings: `:`/`-` nehmen oder gezieltes `noqa: RUF001`.

## Befehle
- `uv sync` – Umgebung; danach Katalog bauen: `uv run python tools/build_dtc_db.py`
  (lädt OBDex-YAML von GitHub, ~9 s; `--source DIR` für einen lokalen Klon)
- `uv run ruff format . && uv run ruff check . && uv run mypy` (strict)
- `uv run pytest` – ca. 1600 Tests, ~3,5 min (Emulator-Tests und der PID-Vergleich mit
  python-OBD sind langsam).
  **Exit-Code von pytest selbst prüfen**, nicht durch `| tail` (hat schon einmal
  einen roten Stand auf main gebracht).
- `uv run python tools/emulator.py [--stored P0420,P0300 --pending P0171 --engine-off]`
  – ELM327-emulator mit standardgemäßen Antworten; zeigt das pty (z. B. /dev/pts/5)
- `uv run obd-diag {info,scan,diagnose,vin,clear,live,ports,export} --port … [--trace]`
  (`live --list`, `--pids rpm,speed`, `--interval`, `--duration`, `--record [DATEI.csv]`)
- `uv run obd-diag-gui`
- Mutationstests: `uv run --with mutmut mutmut run` (Ziele in `[tool.mutmut]`),
  danach `mutants/` löschen. Die Testsuite legt `elm.log` an (gitignored), löschen.

## Stand (2026-10-08)
Roadmap-Schritte 1–4 sind fertig = Funktionsumfang v0.1. v0.2 (Live-Daten) ist in
main (PR #1). Version 0.2.0, noch kein Release-Tag (Tag erst nach Absprache, am
besten nach dem Test am Auto). **Nichts ist an einem echten Adapter/Auto getestet.**
Die Antwortverarbeitung ist ohne Hardware verifiziert (`tests/verification`:
Datenblatt-Beispiele, echte Nutzer-Logs aus python-OBD/ELMduino/AndrOBD, Vergleich
mit dem DTC-Decoder von python-OBD über alle 65536 Byte-Paare, Hypothesis-Round-
Trips und Fuzzing).

Funktionen: Fehlercodes (Mode 03/07/0A) mit deutschem Klartext, Readiness (Mode 01
PID 01, Otto + Diesel), Freeze Frame (Mode 02), FIN (Mode 09 PID 02) mit Offline-
Dekodierung, sicheres Löschen, Sitzungen als JSON, PDF-Bericht und CSV, Adapter-
Mitschnitt, GUI mit Tabs (Fehlercodes / Readiness / Freeze Frame / Fahrzeug /
Live-Daten), helles und dunkles Design. Live-Daten: 116 Werte aus 82 Mode-01-PIDs
(auch Lambdasonden 1–8, Nachkat-Trimm, Drehmomentstufen, 66/67), CSV-Aufzeichnung,
Kacheln mit Verlaufskurve, CLI `obd-diag live`.

## Aufbau (src/obd_diag)
- `transport/` – `base.py` (Protocol, `TransportError`/`TransportTimeout`),
  `serial.py` (pyserial; leert den Eingangspuffer vor jedem Write), `discovery.py`
  (`list_ports`: USB-Seriell + /dev/rfcomm*), `trace.py` (`TracingTransport`,
  `FileTracingTransport`, `ReplayTransport`, `open_serial`).
- `protocol/` – `elm327.py` (Befehl senden, Antwort bereinigen: Echo, SEARCHING,
  BUS INIT, Störbytes; `query` gibt `None` bei NO DATA; `query_with_headers`,
  `read_more`, `protocol()` mit CAN-Erkennung), `frames.py` (ISO-TP ohne Header:
  `00A` / `0:` / `1:`; `FrameSequenceError` bei vermischten Frames), `headers.py`
  (Antworten mit Headern, ISO-TP je Steuergerät), `dtc_decode.py`, `obd.py`
  (`read_dtcs`, `clear_dtcs`, `read_pid`, `read_rpms`, `read_freeze_frame`), `pids.py`
  (`PIDS`: Schlüssel → `PidSpec` je Wert, mehrere Werte können eine PID teilen;
  `decode` darf `None` liefern; `read_supported_pids` über alle Steuergeräte,
  `read_values` je PID mit einer Anfrage, `read_value` nur für Tests).
- `services/` – `diagnostics.py` (`scan`), `clear.py` (Löschen), `readiness.py`,
  `vehicle.py` (`read_vin`, `decode_vin`, `lookup_vpic`), `session.py`
  (`run_diagnosis`, JSON speichern/laden), `storage.py` (XDG-Pfade, atomares
  Schreiben ohne Überschreiben), `live.py` (`prepare_live`, `select_pids`, `run_live`
  mit injizierbarer Uhr, `LiveRecorder` CSV unter `recordings/`).
- `data/` – `dtc_catalog.py` (SQLite, read-only), `catalog_build.py`, `wmi.py`
  (WMI → Hersteller, ISO-3780-Länderbereiche; Quellen im Modul).
- `export/report.py` – PDF (ReportLab) und CSV.
- `ui/` – `app.py`, `window.py`, `backend.py` (`live_port`), `jobs.py`, `theme.py`,
  `viewmodels/` (`diagnosis.py`, `live.py`), `qml/` (`LiveView.qml` u. a.).
  `cli.py` – alle Befehle.

## Wichtige Entscheidungen und Fallstricke
- **Freigabeliste** (`protocol/elm327.py`): `Elm327.command` ist der einzige Weg zum
  Adapter und lässt nur `AT_COMMANDS` und lesende Anfragen (`01xx`, `02xx[00]`, `03`,
  `07`, `0A`, `0902`) durch; `04` nur in `with elm.allow_clear()` (nur `clear_dtcs`).
  Sonst `ForbiddenCommandError` (bewusst kein `ElmError`), gesendet wird nichts. Neue
  Befehle dort bewusst freigeben. `tests/unit/test_command_guard.py` prüft Liste,
  Byte-Format, exakte Befehlsfolge jeder Funktion (gegen J1979/Datenblatt geprüft)
  und dass sonst niemand an den Transport schreibt; das Format prüfen zusätzlich
  `FakeTransport` und eine Fixture für den seriellen Transport in allen Tests.
  `tests/fixtures/command_spec.yaml` nennt je erlaubtem Befehl Seite und Zitat aus dem
  ELM327-Datenblatt (ELM327DSJ, v2.1) bzw. den J1979-Service, dazu verbotene Beispiele.
  `tests/verification/test_command_spec.py` prüft Freigabeliste == Spezifikation
  (erschöpfend über alle Hex-Befehle), `_commands_match_spec` in `tests/conftest.py` jeden
  in irgendeinem Test gesendeten Befehl. Neue Befehle dort mit Quelle eintragen. Ausnahme
  nur `@pytest.mark.foreign_commands` (fremde Mitschnitte in `test_traces.py`).
- **Löschen** (`clear_codes`): Scan → Vorbedingungen (`0100` antwortet, Spannung
  ≥ 11,8 V falls lesbar, Drehzahl genau 0, unlesbare Drehzahl = Abbruch) → Freeze
  Frame → Sicherung (`~/.local/share/obd-diag/backups`, nie überschreiben) → erst dann
  Mode 04 → Kontroll-Scan. Drehzahl: jedes auf `010C` antwortende Steuergerät muss
  gültig 0 melden (`read_rpms`). Tests prüfen für jeden Fehlerfall, dass `04` nie
  gesendet wird; `tests/unit/test_write_safety.py` zusätzlich per Hypothesis mit
  beliebig kaputten Antworten (04 höchstens einmal, nur bei erfüllten Vorbedingungen
  und vorhandener Sicherung) und per AST, wer `allow_clear`/`clear_dtcs`/`clear_codes`/
  `.transport`/pyserial erreichen darf. Mutationstests von `clear.py`: alle erkannt;
  in `elm327.py`/`obd.py` überleben beim Löschpfad nur Text- und Timeout-Mutanten.
  Diese Garantien nicht aufweichen. Wegen der Sperre schaltet `tests/conftest.py`
  Löschen für alle Tests frei; `@pytest.mark.clear_disabled` (z. B.
  `tests/unit/test_clear_disabled.py`) prüft die ausgelieferte Sperre.
- **CAN-Zählbyte**: Bei CAN wird das Zählbyte nach dem Mode-Byte immer gelesen,
  Füllbytes danach werden ignoriert, zu wenige Codes → `ValueError`. Ob CAN, kommt
  aus `ATDPN` (unklar → nachfragen, notfalls Header-Form per `ATH1 0100`). Legacy-
  Antworten mit `can=True` zu lesen ist bewusst nicht mehr toleriert.
- **Ablehnungen beim Fehlercode-Lesen**: `7F <Mode> 11/12` (Mode nicht unterstützt) heißt
  „keine Codes von diesem Steuergerät“; jede andere Ablehnung ist `NegativeResponseError`,
  nie eine leere Liste. Bei `7F <Mode> 78` wird ohne erneutes Senden bis
  `DTC_PENDING_TIMEOUT` (5 s) weitergelesen; ohne Header zählt jede spätere positive
  Antwort als die angekündigte.
- **Wartezeiten**: `Elm327` wartet 10 s je Befehl (der ELM327 wartet nach `78` selbst
  bis 5 s, Datenblatt S. 45), das erste `0100` nach `ATSP0` 30 s (Protokollsuche).
- **Header-Rückfall**: Vermischen sich mehrteilige Antworten mehrerer Steuergeräte
  (ATH0), wird die Anfrage mit `ATH1` wiederholt und je CAN-ID zusammengesetzt; Codes
  dann nach Steuergerät sortiert. Scheitert das Zurückschalten auf `ATH0`, wird ein
  `TransportError` geworfen (bricht ab), damit niemand weiter falsch liest.
- **ELM327-emulator 4.0.0** weicht ab: kein CAN-Zählbyte, mehrteilige Frames ohne
  Header nicht im ELM-Format, Mode 02 ohne Frame-Nummer, wechselnde FIN, Drehzahl
  ≠ 0. Korrekturen in `tests/emulator_patches.py` (autouse in `tests/conftest.py`,
  auch genutzt von `tools/emulator.py`). Der Emulator kommt per `[tool.uv.sources]`
  aus dem Git-Tag v4.0.0, weil das PyPI-sdist eine falsche Version meldet.
- **GUI**: Jeder Job öffnet Port und Katalog im Worker-Thread und schließt sie wieder
  (SQLite-Verbindungen sind threadgebunden). Ein Thread im Pool, also nie zwei Jobs
  am Adapter. Einstellungen (NHTSA, Mitschnitt, Design) in QSettings. Tests laufen
  offscreen; `tests/ui/conftest.py` setzt XDG/QSettings auf temporäre Ordner.
  Live-Daten laufen als ein langer Job im selben Thread; Werte kommen per
  `_LiveRelay` (queued) mit Laufnummer, Stopp über `threading.Event`. Solange Live
  läuft, ist `DiagnosisViewModel.blocked` gesetzt (Scan, Löschen, Export gesperrt,
  sonst stauten sie sich im einen Thread); Live startet nicht, solange die Diagnose
  `busy` ist. `app.py` stoppt Live vor `runner.wait()`.
- **Live-Daten**: nur `01xx` und `ATRV`. Ein nicht lesbarer Wert ergibt `None` in der
  Runde, `TransportError` bricht ab, ebenso `MAX_FAILED_ROUNDS` (3) Runden, in denen
  jeder Wert an einem Adapterfehler scheitert (`?`/`NO DATA`/`7F` zählen nicht). Unter
  11,8 V nur alle 5 s abfragen; unlesbares `ATRV` hebt die Drosselung nicht auf.
  Auswahlfehler sind `SelectionError` (ein `ValueError`), auch „kein Standardwert
  unterstützt“; die GUI unterscheidet sie so von kaputten Antworten. Vor der ersten
  Verbindung schickt die GUI `skip_unsupported=True` (Liste zeigt noch alle PIDs). Kurven
  skalieren nach den gezeigten Werten (der volle J1979-Bereich ließe sie flach).
  Die CSV-Methode heißt `LiveRecorder.add`, nicht `write`: der Architekturtest sucht
  `.write(`-Aufrufe, und er wird nicht umgangen, sondern ernst genommen.
  Formeln: `tests/verification/test_pid_differential.py` vergleicht jeden Bytewert mit
  python-OBD. Bewusste Abweichungen nach J1979: `32` (16-Bit-Zweierkomplement, python-
  OBD wertet A und B einzeln aus) und `44` (2/65536 statt gerundet 0,0000305).
- **Serieller Transport**: Ein-/Ausgabefehler (abgezogener Adapter, EIO) werden zu
  `TransportError` („Verbindung … unterbrochen“), auch `termios.error`, das pyserial
  beim Leeren des Puffers bzw. Umstellen des Timeouts wirft (abgezogen zwischen zwei
  Befehlen). Der Architekturtest erlaubt `termios` daher nur in `serial.py` und nur
  als `termios.error`. `close` schluckt Fehler, damit sie
  die eigentliche Meldung nicht verdecken. `tests/integration/test_disconnect.py`:
  Adapter am pty, der mitten in Diagnose bzw. Live verschwindet oder verstummt.
- **PDF**: bettet DejaVu/Liberation/Noto ein, falls installiert, sonst Helvetica.
  Schriften laufen unterschiedlich breit; PDF-Tests vergleichen Text daher ohne
  Zeilenumbrüche. In der CI ist es DejaVu.
- **Modelljahr** (FIN-Stelle 10) ist außerhalb Nordamerikas mehrdeutig:
  `model_year_alternatives`, Anzeige „2026 oder 1996 (ohne Gewähr)“.
- **Readiness**: Urteil heißt „Alle Tests abgeschlossen“, nicht „AU-bereit“ – es gibt
  keine belegbare allgemeine AU-Regel (AU seit 2018 immer mit Endrohrmessung).
  `ReadinessStatus.all_complete`, `ready` ist Alias.
- **Testhilfen**: `tests/fakes.py` (`FakeTransport` mit `headers_on`, `later`,
  `after_clear`; `CAN_CAR`, `CAN_CAR_FULL`), `tests/samples.py` (`full_session`,
  `minimal_session`), `tests/fixtures/traces/` (echte Mitschnitte mit Quellen).
- Arbeit mit parallelen Agents: Worktrees unter `.claude/worktrees/` (gitignored).
  Bewährt: Schnittstellen vorher als Stubs committen, Agents nur in getrennten
  Dateien arbeiten lassen, danach selbst zusammenführen und alles prüfen.

## Offen
- **Test am echten Auto** (wichtigster Punkt). Ablauf: Motor aus, Zündung an,
  `obd-diag ports`, `info`, dann `diagnose --save --trace`; noch kein `clear`, bis
  Ausgabe und Mitschnitt geprüft sind. Aus dem Mitschnitt mit `ReplayTransport` einen
  Regressionstest machen.
- Nur am echten Gerät prüfbar: CAN-Mode-03-Format ohne Header, Freeze-Frame-Format
  (`02xx00` vs. `02xx`), ob der ELM327 nach `7F 04 78` weiter lauscht, das dunkle
  Design auf einem echten dunklen Desktop, `list_ports` mit echter Hardware.
- Nordamerika-Modelljahrregel (Stelle 7) gilt rechtlich nur bis 10.000 lb, wird aber
  auf alle FINs mit 1–5 angewendet. CAN-Plausibilitätsgrenze „ab 2000“ ist geschätzt.
- Wird `ATRV` nicht unterstützt, ist Löschen trotzdem erlaubt (nur bekannte niedrige
  Spannung blockiert) – bewusst, ggf. mit dem Nutzer klären.
- Release-Tag v0.2.0 setzen (mit dem Nutzer absprechen).
- Nicht aufgenommen sind PIDs mit Statusbyte, deren Aufbau in freien Quellen nicht
  eindeutig ist (68–7F, u. a. 70 Ladedruck, 7A–7C Partikelfilter); Liste im Docstring
  von `protocol/pids.py`. Erst mit der Norm (J1979-DA) oder echten Antworten angehen.
- Nächste Roadmap-Schritte: v0.3+ Bluetooth LE, OBDb-Profile (CC-BY-SA beachten),
  SocketCAN/UDS mit Steuergeräte-Erkennung. Entwurf samt Sicherheitskonzept
  (Freigabeliste, gesperrte Dienste, Adresssuche nur im Diagnosebereich) steht im
  Designdokument; erst nach dem Test am Auto umsetzen. Servicefunktionen (Routinen,
  Codieren) bleiben bewusst draußen.
