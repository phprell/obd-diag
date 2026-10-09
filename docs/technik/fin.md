# FIN-Dekodierung

Die FIN (Fahrzeug-Identifizierungsnummer, englisch VIN) kommt aus Mode 09 PID 02
({doc}`dienste`) oder wird eingegeben (`obd-diag vin WVWZZZ1KZ6W123456`). Die
Dekodierung läuft offline in `services/vehicle.py`.

| Stellen | Inhalt | Wie obd-diag sie liest |
| --- | --- | --- |
| 1–3 | WMI, Herstellerkennung | Tabelle häufiger WMIs in `data/wmi.py` (geprüft gegen Wikipedia und NHTSA vPIC) |
| 1–2 | Region und Land | ISO-3780-Bereiche (ISO-Übersicht 2021) |
| 9 | Prüfziffer | nach ISO 3779 / 49 CFR 565.15 |
| 10 | Modelljahr | Code wiederholt sich alle 30 Jahre |

- **Gültig** sind genau 17 Zeichen aus 0–9 und A–Z ohne I, O, Q.
- **Prüfziffer:** Pflicht nur in Nordamerika (FIN beginnt mit 1–5) und China (`L`),
  dort „stimmt“ oder „stimmt nicht“. Sonst „stimmt“, wenn sie zufällig oder freiwillig
  passt, und „nicht vorgeschrieben“ andernfalls; das ist dann kein Fehler.
- **Modelljahr:** In Nordamerika entscheidet Stelle 7 (Ziffer: 1980–2009, Buchstabe:
  2010–2039). Sonst gilt das jüngste Jahr bis höchstens ein Jahr in der Zukunft als
  beste Schätzung, und das 30 Jahre ältere wird mitgenannt: „2026 oder 1996 (ohne
  Gewähr)“. Kommt die FIN aus dem Fahrzeug, fallen Jahre weg, die zum Protokoll nicht
  passen (OBD-II nicht vor 1994, CAN nach ISO 15765-4 nicht vor 2000). Europäische
  Hersteller nutzen Stelle 10 nicht alle als Modelljahr. Bei Mercedes-Benz (WMI WDB,
  WDC, WDD, WDF, W1K, W1N, W1V) ist Stelle 10 die Lenkung (`1` = links) und Stelle 11
  das Werk; dort zeigt das Tool kein Modelljahr (am A 180 d, W177, stand sonst „2001“).

## Online-Abfrage (nur nach Opt-in)

Die FIN bleibt auf dem Rechner. Nur mit `--online-vin` bzw. dem Schalter „FIN online
nachschlagen (NHTSA)“ geht sie an die US-Datenbank
[NHTSA vPIC](https://vpic.nhtsa.dot.gov/api/); übernommen werden Modell, Modelljahr,
Karosserie, Motor, Kraftstoff, Werk u. Ä. Die Antwort wird je FIN unter
`~/.cache/obd-diag/vpic/` gespeichert, eine FIN wird also nur einmal abgefragt.
Netzwerkfehler werden ignoriert. Für europäische Modelle sind die Angaben oft
lückenhaft.
