# PID formulas

All live values obd-diag knows, with the key for `obd-diag live --pids`, unit,
conversion according to SAE J1979 (service $01) and value range. The table is generated
from `PIDS` in `src/obd_diag/protocol/pids.py` when the documentation is built.

`tests/verification/test_pid_differential.py` compares every formula for every possible
byte value with python-OBD. Two deviations are intended and justified against J1979:

- **`32`** (evap system vapor pressure): 16-bit two's complement over A and B;
  python-OBD evaluates A and B separately.
- **`44`** (commanded air-fuel equivalence ratio): factor exactly 2/65536 instead of
  the rounded 0.0000305.

The PIDs **`68`** (intake air temperature per sensor), **`6C`** (throttle actuator),
**`6D`** (fuel rail pressure control), **`70`** (boost pressure) and **`85`** (AdBlue)
are reported as supported by the Mercedes A 180 d, but there are no responses from the
car yet: **not yet verified on the car**. Their structure follows J1979 according to
two independent descriptions that agree (OBDb signal list, DashLogic PID list). Each of
these PIDs starts with a status byte A that says which values the vehicle provides; if
the bit is missing, the value stays empty. A value only appears anyway if the vehicle
reports the PID.

Not included are bit fields without a measured value (`01`, `03`, `12`, `13`, `1C`,
`1D`, `1E`, `41`, `51`, `65`, `88`, `92`, the control status in `70`) and PIDs with a
status byte whose structure is not unambiguous in free sources or that the test car
does not report (among them `69` EGR, `7A`–`7C` particulate filter), as well as
`53`/`54`. They will only be added with the standard (J1979-DA) or real responses.

```{include} ../_gen/pids.md
```
