# Änderungen

Alle nennenswerten Änderungen an obd-diag. Versionen folgen [Semantic Versioning](https://semver.org/lang/de/);
vor 1.0 kann sich die Bedienung noch ändern.

## Unreleased

- Englische Oberfläche: Sprache unter „Optionen → Sprache / Language“ wählbar, Wechsel
  sofort ohne Neustart. Beim ersten Start gilt die Systemsprache (Deutsch nur bei
  deutschem System, sonst Englisch).
- Kommandozeile, PDF-Bericht und CSV mit `--lang de|en` (Standard: Systemsprache;
  die Oberfläche exportiert in ihrer Sprache); englische CSV mit Komma und Dezimalpunkt.
- Dokumentation zusätzlich auf Englisch unter https://phprell.github.io/obd-diag/en/ mit
  Sprachumschalter; die deutschen Adressen bleiben gleich.
- README auf Englisch, deutsche Fassung in README.de.md.
- Tests verhindern Texte, die nur in einer Sprache vorliegen.

## 0.2.0 – 2026-10-09

Erstes veröffentlichtes Release. Es umfasst den Funktionsumfang v0.1 (nie einzeln
veröffentlicht) und die Live-Daten aus v0.2.

### Am echten Auto geprüft

- Erster Test am 2026-10-09 an einem Mercedes A 180 d (W177) mit FORScan ELMconfig
  (USB, CH340), CAN 29 Bit/500 kBit/s, vier Steuergeräte: `info`, `diagnose` und `live`
  lesen richtig.
- Vier Befunde aus dem Test behoben (#7): Freeze Frame vom Steuergerät mit dem Code
  (vorher „unbekannter Code“), erstes `ATZ` mit `?` wird einmal wiederholt, Bordspannung
  per PID 42 gegengeprüft, wenn der Adapter unter 11,8 V misst, bei Mercedes kein
  Modelljahr aus FIN-Stelle 10.
- `UNABLE TO CONNECT` (Zündung aus) ergibt eine klare Meldung mit Hinweis (#8).
- Die Mitschnitte laufen als Regressionstest (`tests/verification/test_real_car.py`,
  FIN-Seriennummer geschwärzt).

### Lesen

- Fehlercodes: gespeichert, ausstehend und permanent (Mode 03, 07, 0A) mit deutschem
  Klartext, Ursachen und Symptomen aus einem Offline-Katalog (OBDex, CC0, 9533 Codes).
- Ablehnungen der Steuergeräte werden nicht mehr als „keine Codes“ gelesen; nur
  `7F <Mode> 11/12` heißt „Mode nicht unterstützt“. Auf `7F <Mode> 78` wird bis 5 s
  gewartet (#4).
- Readiness (Mode 01 PID 01, Otto und Diesel) mit Motorkontrollleuchte; Urteil „Alle
  Tests abgeschlossen“.
- Freeze Frame zum auslösenden Code (Mode 02).
- FIN (Mode 09 PID 02) mit Offline-Dekodierung (Hersteller, Land, Modelljahr ohne
  Gewähr), optional NHTSA vPIC (Opt-in).
- Fehlercodes ohne Katalogtext auf Wunsch online erklären (`--online-codes`, Option in
  der Oberfläche, standardmäßig aus; Quelle Wal33D/dtc-database, MIT). Es werden ganze
  Dateien geladen, Fehlercode und FIN gehen nie ins Netz (#11, ADR 0004).

### Live-Daten (v0.2)

- 116 Werte aus 82 Mode-01-PIDs nach SAE J1979, darunter die Lambdasonden 1–8,
  Nachkat-Trimm und Drehmomentstufen; jede Formel ist gegen python-OBD verglichen.
- `obd-diag live` (`--list`, `--pids`, `--interval`, `--duration`, `--record`) und Reiter
  „Live-Daten“ mit Kacheln und Verlaufskurve; Aufzeichnung als CSV.
- Unter 11,8 V Bordspannung wird nur alle 5 s abgefragt.

### Sicherheit

- Nur lesend: Freigabeliste in `Elm327.command`, jeder andere Befehl wird abgewiesen,
  bevor etwas gesendet wird. Jeder erlaubte Befehl ist mit Seite und Zitat aus dem
  ELM327-Datenblatt bzw. dem J1979-Service belegt und wird in allen Tests geprüft (#3).
- Mindestens 50 ms zwischen zwei Anfragen ans Fahrzeug, auch nach Fehlern (#6).
- **Löschen (Mode 04) ist gesperrt** (`CLEAR_ENABLED = False`). Der Ablauf mit
  Vorbedingungen (Motor aus bei allen Steuergeräten, Spannung), Sicherung und
  Kontroll-Scan ist fertig und getestet, wird aber erst nach einer bewussten Freigabe
  eingeschaltet.
- Abgezogener Adapter (auch zwischen zwei Befehlen) bricht mit Meldung ab, ohne halbe
  Sitzungen zu speichern.

### Bedienung und Ausgabe

- Kommandozeile `obd-diag` mit `info`, `scan`, `diagnose`, `vin`, `clear`, `live`,
  `ports`, `export`; Adapter-Mitschnitt mit `--trace`.
- Oberfläche `obd-diag-gui` (PySide6/QML, Extra `gui`) mit Reitern Fehlercodes,
  Readiness, Freeze Frame, Fahrzeug, Live-Daten; helles und dunkles Design.
- Diagnosesitzungen als JSON, PDF-Bericht und CSV.

### Dokumentation

- Website unter <https://phprell.github.io/obd-diag/> (#5, #9, #12): Benutzen, wie das
  Tool mit dem ELM327 spricht (Befehlsreferenz mit Datenblatt-Seiten, PID-Formeln,
  Sicherheitskonzept, der Mitschnitt vom W177 Zeile für Zeile), Entwickeln.

### Bekannte Einschränkungen

- Bisher an einem Fahrzeug geprüft. Die Korrekturen aus #7 und #8 sind gegen die
  Mitschnitte getestet, aber noch nicht in einem zweiten Test am Auto.
- Nur über USB-Seriell bzw. `/dev/rfcomm*`; Bluetooth LE folgt später.
- Nur die genormte Abgasdiagnose (OBD-II), keine Steuergeräte wie Airbag oder ABS.
- PIDs mit Statusbyte (u. a. 70 Ladedruck, 7A–7C Partikelfilter) fehlen noch.
