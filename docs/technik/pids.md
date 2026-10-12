# PID-Formeln

Alle Live-Werte, die obd-diag kennt, mit Schlüssel für `obd-diag live --pids`, Einheit,
Umrechnung nach SAE J1979 (Service $01) und Wertebereich. Die Tabelle wird beim Bauen
aus `PIDS` in `src/obd_diag/protocol/pids.py` erzeugt.

`tests/verification/test_pid_differential.py` vergleicht jede Formel für jeden
möglichen Bytewert mit python-OBD. Zwei Abweichungen sind gewollt und gegen J1979
begründet:

- **`32`** (Dampfdruck Tankentlüftung): 16-Bit-Zweierkomplement über A und B;
  python-OBD wertet A und B einzeln aus.
- **`44`** (Lambda-Sollwert): Faktor genau 2/65536 statt des gerundeten 0,0000305.

Die PIDs **`68`** (Ansauglufttemperatur je Sensor), **`6C`** (Drosselklappensteller),
**`6D`** (Raildruck-Regelung), **`70`** (Ladedruck) und **`85`** (AdBlue) meldet der
Mercedes A 180 d als unterstützt, Antworten darauf vom Auto gibt es aber noch nicht:
**am Fahrzeug noch nicht geprüft**. Ihr Aufbau folgt J1979 nach zwei unabhängigen
Darstellungen, die übereinstimmen (Signalliste von OBDb, PID-Liste von DashLogic).
Jede dieser PIDs beginnt mit einem Statusbyte A, das sagt, welche Werte das Fahrzeug
liefert; fehlt das Bit, bleibt der Wert leer. Ein Wert kommt ohnehin nur, wenn das
Fahrzeug die PID meldet.

Nicht enthalten sind Bitfelder ohne Messwert (`01`, `03`, `12`, `13`, `1C`, `1D`,
`1E`, `41`, `51`, `65`, `88`, `92`, der Regelstatus in `70`) und PIDs mit Statusbyte,
deren Aufbau in freien Quellen nicht eindeutig ist oder die das Testauto nicht meldet
(u. a. `69` AGR, `7A`–`7C` Partikelfilter), außerdem `53`/`54`. Sie werden erst mit
der Norm (J1979-DA) oder echten Antworten aufgenommen.

```{include} ../_gen/pids.md
```
