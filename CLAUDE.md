# obd-diag

Linux-OBD-II-Diagnose-Tool für ELM327-kompatible Adapter: Kommandozeile und
PySide6/QML-Oberfläche, Python 3.12+. Repo: github.com/phprell/obd-diag (privat).

Spezifikation und Roadmap: Claude-Docs-Dokument „OBD-Diagnose – Designvorschlag“
https://claude.ai/code/artifact/de949a8a-4f58-41b4-a629-6b9d238bdac7 (über den
Claude-Docs-Connector lesen; Architektur- und Roadmap-Änderungen dort nachziehen).
Die Ideen stammen aus einem Cowork-Projekt, auf das Claude Code keinen Zugriff hat.

## Regeln
- Strikte Schichten: transport → protocol → services → ui/cli. Keine Importe nach oben
  (transport importiert nichts aus services). Höhere Schichten kennen nur das
  `Transport`-Protocol, nie pyserial direkt.
- Standardmäßig nur lesend. Einziger schreibender OBD-Befehl ist Mode 04 (Löschen),
  nur über `services/clear.py`. Keine Codierung, kein Flashen, keine
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
- `uv run pytest` – ca. 875 Tests, ~2,5 min (Emulator-Tests sind langsam).
  **Exit-Code von pytest selbst prüfen**, nicht durch `| tail` (hat schon einmal
  einen roten Stand auf main gebracht).
- `uv run python tools/emulator.py [--stored P0420,P0300 --pending P0171 --engine-off]`
  – ELM327-emulator mit standardgemäßen Antworten; zeigt das pty (z. B. /dev/pts/5)
- `uv run obd-diag {info,scan,diagnose,vin,clear,ports,export} --port … [--trace]`
- `uv run obd-diag-gui`
- Mutationstests: `uv run --with mutmut mutmut run` (Ziele in `[tool.mutmut]`),
  danach `mutants/` löschen. Die Testsuite legt `elm.log` an (gitignored), löschen.

## Stand (2026-10-07)
Roadmap-Schritte 1–4 sind fertig = Funktionsumfang v0.1. Version steht noch auf
0.0.1, kein Release-Tag. **Nichts ist an einem echten Adapter/Auto getestet.**
Die Antwortverarbeitung ist ohne Hardware verifiziert (`tests/verification`:
Datenblatt-Beispiele, echte Nutzer-Logs aus python-OBD/ELMduino/AndrOBD, Vergleich
mit dem DTC-Decoder von python-OBD über alle 65536 Byte-Paare, Hypothesis-Round-
Trips und Fuzzing).

Funktionen: Fehlercodes (Mode 03/07/0A) mit deutschem Klartext, Readiness (Mode 01
PID 01, Otto + Diesel), Freeze Frame (Mode 02), FIN (Mode 09 PID 02) mit Offline-
Dekodierung, sicheres Löschen, Sitzungen als JSON, PDF-Bericht und CSV, Adapter-
Mitschnitt, GUI mit Tabs (Fehlercodes / Readiness / Freeze Frame / Fahrzeug), helles
und dunkles Design.

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
  (`read_dtcs`, `clear_dtcs`, `read_pid`, `read_freeze_frame`).
- `services/` – `diagnostics.py` (`scan`), `clear.py` (Löschen), `readiness.py`,
  `vehicle.py` (`read_vin`, `decode_vin`, `lookup_vpic`), `session.py`
  (`run_diagnosis`, JSON speichern/laden), `storage.py` (XDG-Pfade, atomares
  Schreiben ohne Überschreiben).
- `data/` – `dtc_catalog.py` (SQLite, read-only), `catalog_build.py`, `wmi.py`
  (WMI → Hersteller, ISO-3780-Länderbereiche; Quellen im Modul).
- `export/report.py` – PDF (ReportLab) und CSV.
- `ui/` – `app.py`, `window.py`, `backend.py`, `jobs.py`, `theme.py`, `viewmodels/`,
  `qml/`. `cli.py` – alle Befehle.

## Wichtige Entscheidungen und Fallstricke
- **Freigabeliste** (`protocol/elm327.py`): `Elm327.command` ist der einzige Weg zum
  Adapter und lässt nur `AT_COMMANDS` und lesende Anfragen (`01xx`, `02xx[00]`, `03`,
  `07`, `0A`, `0902`) durch; `04` nur in `with elm.allow_clear()` (nur `clear_dtcs`).
  Sonst `ForbiddenCommandError` (bewusst kein `ElmError`), gesendet wird nichts. Neue
  Befehle dort bewusst freigeben. `tests/unit/test_command_guard.py` prüft Liste,
  Byte-Format, exakte Befehlsfolge jeder Funktion (gegen J1979/Datenblatt geprüft)
  und dass sonst niemand an den Transport schreibt; das Format prüfen zusätzlich
  `FakeTransport` und eine Fixture für den seriellen Transport in allen Tests.
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
  Diese Garantien nicht aufweichen.
- **CAN-Zählbyte**: Bei CAN wird das Zählbyte nach dem Mode-Byte immer gelesen,
  Füllbytes danach werden ignoriert, zu wenige Codes → `ValueError`. Ob CAN, kommt
  aus `ATDPN` (unklar → nachfragen, notfalls Header-Form per `ATH1 0100`). Legacy-
  Antworten mit `can=True` zu lesen ist bewusst nicht mehr toleriert.
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
- Version 0.1.0 setzen und Release taggen (mit dem Nutzer absprechen).
- Nächste Roadmap-Schritte: v0.2 Live-Daten-Dashboard mit Aufzeichnung; v0.3+
  Bluetooth LE, OBDb-Profile (CC-BY-SA beachten), SocketCAN/UDS.
