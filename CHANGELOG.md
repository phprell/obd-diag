# Änderungen

Alle nennenswerten Änderungen an obd-diag. Versionen folgen [Semantic Versioning](https://semver.org/lang/de/);
vor 1.0 kann sich die Bedienung noch ändern.

## Unveröffentlicht

### Neu

- Readiness-Assistent: `obd-diag readiness` und in der Oberfläche eine Karte im Reiter
  *Readiness*. Liest neben dem Stand seit dem Löschen (PID 01) auch den Stand dieser
  Fahrt (PID 41: abgeschlossen, noch offen, ausgesetzt) und nennt zu jedem offenen Test,
  welche Fahrt ihn typischerweise abschließt, getrennt für Otto und Diesel. Nur lesende
  Anfragen (`0101`, `0141`); die Demo kennt sie mit einem erfundenen Beispielwert für PID 41.
- Sitzungsvergleich: `obd-diag compare ALT.json NEU.json` bzw. in der Oberfläche *Datei →
  Mit Sitzung vergleichen …* zeigt, was sich zwischen zwei gespeicherten Diagnosen
  geändert hat: neue, entfallene und weiterhin vorhandene Fehlercodes, Readiness-Monitore,
  Kontrollleuchte, Freeze Frame, FIN, Protokoll und Bordspannung (`--json` für Skripte).
  Arbeitet nur mit den Dateien, das Fahrzeug wird nicht angesprochen.

## 0.2.1 – 2026-10-11

Neu sind Einrichtungs- und Adapter-Check, ein Demo-Modus ohne Auto, die englische
Oberfläche und fertige Pakete (PyPI, AUR, Flatpak). Am Lesen vom Fahrzeug ändert sich nichts.

### Neu

- Einrichtung prüfen: `obd-diag setup-check` und in der Oberfläche *Optionen →
  Einrichtung prüfen …* (beim ersten Start von selbst). Findet die Adapter, prüft die
  Zugriffsrechte und nennt die nötige Gruppe (`uucp` auf Arch, `dialout` auf
  Debian/Ubuntu) samt Befehl, probiert 38400 und 115200 Baud (nur Adapter-Befehle) und
  erinnert an Zündung und HS-CAN-Schalter. Die Oberfläche übernimmt Port und Baudrate.
- Demo-Modus: `obd-diag-gui --demo`, Port „demo“ in der Liste oder `obd-diag … --demo`
  zeigt ein eingebautes Beispielfahrzeug ohne Adapter und ohne Auto. Ein simulierter
  ELM327 antwortet mit den Mitschnitten des Mercedes A 180 d aus den Tests am Auto
  (Fehlercode U1218, Readiness, Freeze Frame, FIN, Live-Daten im Leerlauf); ein Hinweis
  und die Adapterkennung `ELM327 v1.5 (Demo)` zeigen, dass die Daten nicht vom eigenen
  Auto stammen.
- Adapter-Check: `obd-diag adapter` bzw. „Optionen → Adapter prüfen …“ zeigt Kennung,
  Spannung und welche Befehle aus der Versionsliste des Datenblatts der Adapter
  wirklich beantwortet (`ATPPS` v1.1, `ATIGN`/`ATRD` v1.4, `ATAMC` v2.0), erkennt daran
  Nachbauten (eine echte v1.5 gab es nie), nennt geänderte Programmable Parameters und
  ob der Adapter voraussichtlich für die UDS-Steuergerätesuche (v0.3) taugt. Neu
  freigegeben sind dafür nur diese lesenden Adapter-Befehle (mit Quelle in
  `command_spec.yaml`); das Fahrzeug wird nicht angesprochen.

### Englisch

- Englische Oberfläche: Sprache unter „Optionen → Sprache / Language“ wählbar, Wechsel
  sofort ohne Neustart. Beim ersten Start gilt die Systemsprache (Deutsch nur bei
  deutschem System, sonst Englisch).
- Kommandozeile, PDF-Bericht und CSV mit `--lang de|en` (Standard: Systemsprache;
  die Oberfläche exportiert in ihrer Sprache); englische CSV mit Komma und Dezimalpunkt.
- Dokumentation zusätzlich auf Englisch unter https://phprell.github.io/obd-diag/en/ mit
  Sprachumschalter; die deutschen Adressen bleiben gleich.
- README auf Englisch, deutsche Fassung in README.de.md.
- Tests verhindern Texte, die nur in einer Sprache vorliegen.

### Installation

- Fertige Pakete: `pipx install 'obd-diag[gui]'` aus PyPI, AUR-Paket `obd-diag` und
  Flatpak-Bundle am GitHub-Release. Der Release-Workflow baut und prüft alle drei
  (auch bei jedem PR) und lädt auf PyPI hoch.
- Startmenü-Eintrag mit Symbol und AppStream-Daten; bei pipx-Installationen legt
  `obd-diag-gui --install-desktop` ihn an (`--remove-desktop` entfernt ihn).
- udev-Regel heißt jetzt `70-obd-diag.rules` und liegt in `src/obd_diag/share/`. Unter
  der alten Nummer 99 kam das Tag `uaccess` zu spät und wirkte nicht; Gruppe und
  Dateirechte ändert sie nicht mehr (die Gruppe `uucp` gibt es auf Debian nicht).
- Das sdist enthält keine Release-Notizen und Arbeitsdateien mehr.

## 0.2.0 – 2026-10-10

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
- Zweiter Test am 2026-10-10 bestätigt diese Korrekturen; Diagnose- und Live-Mitschnitte
  stimmen Byte für Byte mit J1979.
- Readiness hängt nicht mehr von der Reihenfolge ab, in der die Steuergeräte antworten.
  Vorher konnte ein Steuergerät ohne Abgasmonitore den Diesel als Ottomotor ausgeben und
  „Alle Tests abgeschlossen“ melden, obwohl der Abgassensor-Test offen war (#14).
  Widersprüchliche Freeze-Frame-Werte mehrerer Steuergeräte werden verworfen.
- Live-Daten: Nach dem Anlassen endet die Drosselung bei niedriger Spannung sofort,
  statt bis zu 50 s weiterzulaufen (#15).
- Die Mitschnitte beider Tests laufen als Regressionstest
  (`tests/verification/test_real_car.py`, FIN-Seriennummer geschwärzt).

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

- Bisher an einem Fahrzeug geprüft (zwei Tests am Mercedes W177).
- Live-Daten nehmen ohne Header die erste gültige Antwort; antworten mehrere
  Steuergeräte auf dieselbe PID, kann der Wert zwischen ihnen wechseln (am W177
  höchstens 11 1/min).
- Nur über USB-Seriell bzw. `/dev/rfcomm*`; Bluetooth LE folgt später.
- Nur die genormte Abgasdiagnose (OBD-II), keine Steuergeräte wie Airbag oder ABS.
- PIDs mit Statusbyte (u. a. 70 Ladedruck, 7A–7C Partikelfilter) fehlen noch.
