# ADR 0004: Online-Erklärungen für Fehlercodes ohne Katalogtext

Status: angenommen (2026-10-09)

## Kontext
Der Offline-Katalog (OBDex, CC0) deckt nur genormte Codes ab. Beim ersten Test am Auto
(Mercedes A 180 d, W177) meldete das Fahrzeug `U1218`, einen herstellerspezifischen
Code ohne Katalogtext. Gewünscht ist eine optionale kurze Erklärung aus dem Netz.
Bisher ging online nur die FIN an NHTSA vPIC, und das nur nach Opt-in.

## Entscheidung
- Standardmäßig aus; eingeschaltet per `--online-codes` (`scan`, `diagnose`) bzw.
  „Fehlercodes online erklären“ in der Oberfläche (QSettings). Nachgeschlagen werden
  nur Codes ohne Katalogtext (`services/dtc_online.py`, aufgerufen über
  `add_online_explanations`).
- Quelle ist [Wal33D/dtc-database](https://github.com/Wal33D/dtc-database) (MIT),
  auf einen Commit gepinnt. Websuchen oder Seiten wie obd-codes.com werden nicht
  automatisch abgefragt: Deren Bedingungen erlauben das nicht, und eine
  Suchmaschinen-API bräuchte einen eigenen Schlüssel.
- Geladen werden ganze Dateien je Hersteller bzw. Codefamilie. Ins Netz geht damit
  nur, welche Datei gebraucht wird, weder Code noch FIN. Wegen des gepinnten Commits
  werden sie dauerhaft gecacht (`~/.cache/obd-diag/dtc-online/<commit>/`).
- Herstellerspezifische Codes nur aus der Datei des Herstellers laut FIN, nie aus der
  gemischten `other_codes.txt` oder der Datei eines anderen Herstellers (dort bedeutet
  derselbe Code etwas anderes). Ohne Hersteller kein Nachschlagen.
- Texte erscheinen immer getrennt vom Katalogtext als „ungeprüft“, mit Quelle und Link
  auf die Zeile. Netzwerkfehler lassen die Erklärung weg, die Diagnose läuft weiter.
- Zusätzlich ein Link „Im Web suchen“ (DuckDuckGo), den obd-diag nie selbst abruft.

## Folgen
- Die Sammlung ist klein (Mercedes: rund 30 `P1xxx`-Codes) und ihre Herkunft nicht
  belegt. `U1218` am W177 bleibt ohne Erklärung; dafür gibt es den Suchlink. Eine
  bessere Quelle lässt sich in `dtc_online.py` austauschen, ohne CLI, GUI oder
  Sitzungsformat zu ändern.
- Kein Test lädt etwas aus dem Netz (`tests/conftest.py` sperrt `dtc_online.urlopen`).
