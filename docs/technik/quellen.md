# Quellen

| Kürzel | Quelle | Wofür |
| --- | --- | --- |
| ELM327DSJ | ELM327-Datenblatt „ELM327 OBD to RS232 Interpreter“, Firmware v2.1, © Elm Electronics 2014, 94 Seiten. Original bei [Elm Electronics](https://www.elmelectronics.com/); abgerufen 2026-10 über [diese Kopie](https://github.com/Obeisance/Arduino_OBD_interface_for_Torque/blob/master/ELM327DS.pdf). Seiten nach der Fußzeile „N of 94“. | jeder AT-Befehl, Antwortformate, Wartezeiten, Fehlermeldungen ({doc}`befehle`) |
| J1979 | SAE J1979 / ISO 15031-5, Diagnosedienste für abgasrelevante Systeme (kostenpflichtig); Angaben bestätigt über die Modusliste im Datenblatt (S. 31) und [Wikipedia: OBD-II PIDs](https://en.wikipedia.org/wiki/OBD-II_PIDs) | Modes, PID-Formeln, Readiness ({doc}`dienste`, {doc}`pids`) |
| J2012 | SAE J2012, Aufbau der Fehlercodes | P/C/B/U und vier Stellen aus zwei Bytes |
| ISO 15765-2/-4 | ISO-TP (Zerlegung langer Nachrichten) und OBD über CAN | Frames, PCI, Zählbyte ({doc}`antwortformate`) |
| ISO 14229-1 | UDS, negative Antworten (`7F`, Gründe `11`, `12`, `21`, `22`, `78`) | Ablehnungen |
| ISO 3779, ISO 3780, 49 CFR 565 | FIN, WMI, Prüfziffer, Modelljahr | {doc}`fin` |
| OBDex | [foerbsnavi/OBDex](https://github.com/foerbsnavi/OBDex), Daten CC0-1.0 | Klartexte der Fehlercodes |
| dtc-database | [Wal33D/dtc-database](https://github.com/Wal33D/dtc-database), MIT, Commit `04c43d7`; Herkunft der Texte nicht belegt | optionale, ungeprüfte Online-Erklärungen ({doc}`../benutzen/fehlercodes-online`) |
| python-OBD | [brendan-w/python-OBD](https://github.com/brendan-w/python-OBD), GPL-2.0, nur in Tests | Vergleich der DTC- und PID-Dekodierung |
| Mitschnitte | Nutzer-Logs aus python-OBD, ELMduino, AndrOBD, Quellen je Datei in `tests/fixtures/traces/` | Regressionstests ohne Hardware |

Das Datenblatt wird hier nur zitiert und verlinkt, nicht mit veröffentlicht. Die Zitate
sind kurze wörtliche Auszüge mit Seitenangabe.
