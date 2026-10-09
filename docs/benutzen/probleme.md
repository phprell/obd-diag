# Häufige Probleme

| Meldung oder Verhalten | Wahrscheinliche Ursache | Was tun |
| --- | --- | --- |
| `obd-diag ports` findet nichts | Adapter nicht eingesteckt, Bluetooth nicht gebunden | `dmesg` prüfen; Bluetooth mit `sudo rfcomm bind 0 <MAC>` |
| „Permission denied“ beim Öffnen des Ports | Nutzer nicht in `uucp`/`dialout` | {doc}`installation`, Abschnitt Rechte |
| `info` hängt oder „keine Antwort von …“ | falsche Baudrate | `--baud 115200` bzw. `--baud 38400` versuchen |
| `UNABLE TO CONNECT`, keine Codes, kein Protokoll | Zündung aus, oder Schalter am FORScan-Adapter auf MS-CAN | Zündung an, Schalter auf HS-CAN ({doc}`adapter`) |
| „Bordspannung zu niedrig“ | Batterie unter 11,8 V (Adapter und Steuergerät messen beide zu wenig) | Batterie laden; Live-Daten fragen dann nur alle 5 s ab |
| „Verbindung zu … unterbrochen“ | Adapter abgezogen, Wackelkontakt, Bluetooth weg | neu verbinden; es wurde danach nichts gesendet |
| „Steuergerät lehnt Mode … ab (Antwort 7F …)“ | Steuergerät beschäftigt oder Bedingungen nicht erfüllt | später erneut; der Fehlerspeicher ist dann unbekannt, nicht leer |
| Codes ohne Beschreibung, „Fehlercode-Katalog fehlt“ | Katalog nicht gebaut | `uv run python tools/build_dtc_db.py` |
| „Löschen ist gesperrt“ | gewollt, bis zum Test am Auto | {doc}`fehlercodes` |
| Live endet nach drei Runden mit Fehler | Adapter meldet nur Busfehler, meist Zündung aus | Zündung an |
| Freeze Frame „keiner gespeichert“ | kein gespeicherter Code | normal |

Bei allem, was hier nicht steht: mit `--trace` wiederholen und den Mitschnitt ansehen
({doc}`sitzungen`).
