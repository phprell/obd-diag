# ADR 0003: Live-Daten als langer Job im einzigen Worker-Thread

Status: angenommen (2026-10-08)

## Kontext
Live-Daten (v0.2) fragen Mode-01-Werte fortlaufend ab, bis der Nutzer stoppt. Die
Oberfläche führt alle Adapter-Aktionen in einem Thread-Pool mit genau einem Thread
aus, damit nie zwei Aktionen denselben Port nutzen. Ein Live-Lauf blockiert diesen
Thread für Minuten.

## Entscheidung
- Live-Daten laufen als ein Job im selben Thread (`backend.live_port`). Zwischenwerte
  kommen über ein Qt-Signal (queued) mit Laufnummer in den GUI-Thread; Werte eines
  alten Laufs werden verworfen. Gestoppt wird über ein `threading.Event`, das
  `run_live` vor jeder Runde und beim Warten in Schritten von höchstens 0,1 s prüft.
- Live und Diagnose sperren sich gegenseitig: Solange Live läuft, sind Scan, Löschen
  und Export gesperrt (`DiagnosisViewModel.blocked`), sonst stauten sie sich im einen
  Thread. Live startet nicht, solange die Diagnose beschäftigt ist. Beim Beenden
  stoppt `app.py` Live vor dem Warten auf den Pool.
- Gesendet werden nur `01xx` und `ATRV`. Ein nicht lesbarer Wert ergibt `None` für
  diese Runde; Verbindungsfehler brechen ab, ebenso drei Runden nacheinander, in denen
  jeder Wert an einem Adapterfehler scheitert (`CAN ERROR`, `UNABLE TO CONNECT` ...:
  Zündung aus, Bus weg). Unter 11,8 V Bordspannung wird nur alle 5 s abgefragt, um die
  Batterie zu schonen; eine nicht lesbare Spannung hebt die Drosselung nicht auf.
- Die PID-Tabelle (`protocol/pids.py`) hat einen Eintrag (`PidSpec`) je Wert; mehrere
  Einträge können dieselbe PID haben (Lambdasonden, Nachkat-Trimm, Drehmomentstufen,
  Statusbyte-PIDs `66`/`67`). `decode` bekommt die ersten `size` Datenbytes und darf
  `None` liefern (Statusbit nicht gesetzt). Je Runde wird jede PID einmal abgefragt.
  PIDs, deren Aufbau in den freien Quellen nicht eindeutig ist (`68`–`7F`, darunter
  Ladedruck `70` und Partikelfilter `7A`–`7C`), bleiben draußen.
- Kurven skalieren nach den gezeigten Werten mit Mindestspanne; der volle Bereich der
  Norm (z. B. Drehzahl bis 16384 1/min) ließe sie flach erscheinen.

## Folgen
- Kein zweiter Thread, keine Synchronisation am Port; die Sperre ist sichtbar
  (ausgegraute Schaltflächen) statt eines stillen Wartens.
- Die Taktung ist mit injizierbarer Uhr testbar; ein Hypothesis-Test prüft, dass
  Runden nie schneller kommen als erlaubt.
