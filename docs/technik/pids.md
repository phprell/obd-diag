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

Nicht enthalten sind Bitfelder ohne Messwert (`01`, `03`, `12`, `13`, `1C`, `1D`,
`1E`, `41`, `51`, `65`) und PIDs mit Statusbyte, deren Aufbau sich zwischen Ausgaben
der Norm geändert hat oder in freien Quellen nicht eindeutig ist (`68`–`7F`, u. a.
`70` Ladedruck und `7A`–`7C` Partikelfilter), außerdem `53`/`54`. Sie werden erst mit
der Norm (J1979-DA) oder echten Antworten aufgenommen.

```{include} ../_gen/pids.md
```
