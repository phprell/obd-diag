# Roadmap

| Version | Inhalt | Stand |
| --- | --- | --- |
| v0.1 | Fehlercodes mit Klartext, Readiness, Freeze Frame, FIN, sicheres Löschen, Sitzungen, PDF/CSV, Mitschnitt, Oberfläche | fertig |
| v0.2 | Live-Daten (116 Werte aus 82 PIDs), CSV-Aufzeichnung, Reiter „Live-Daten“, `obd-diag live` | fertig, Version 0.2.0 ({doc}`aenderungen`) |
| – | Test am echten Auto (W177, 2026-10-09: Lesen geprüft), danach Löschen freigeben | Löschen wartet auf Freigabe ({doc}`../benutzen/erster-test`) |
| v0.3+ | Bluetooth LE, OBDb-Profile (CC-BY-SA beachten), SocketCAN und UDS mit Steuergeräte-Erkennung, nur lesend | geplant, erst nach dem Test am Auto |

Am W177 bestätigt sind das CAN-Mode-03-Format ohne Header (mit Zählbyte) und das
Freeze-Frame-Format `02xx00`. Nur am echten Gerät zu klären bleiben: ob der ELM327 nach
`7F 04 78` weiter lauscht, das dunkle Design auf einem echten dunklen Desktop und
`list_ports` mit weiterer Hardware.

Servicefunktionen (Routinen, Codieren) bleiben bewusst draußen. Entwurf, Sicherheits-
konzept für UDS (Freigabeliste, gesperrte Dienste, Adresssuche nur im
Diagnosebereich) und Roadmap stehen im Designdokument „OBD-Diagnose –
Designvorschlag“.
