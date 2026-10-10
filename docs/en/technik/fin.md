# VIN decoding

The VIN (vehicle identification number, German “FIN”) comes from mode 09 PID 02
({doc}`dienste`) or is typed in (`obd-diag vin WVWZZZ1KZ6W123456`). The decoding runs
offline in `services/vehicle.py`.

| Positions | Content | How obd-diag reads them |
| --- | --- | --- |
| 1–3 | WMI, manufacturer identifier | table of common WMIs in `data/wmi.py` (checked against Wikipedia and NHTSA vPIC) |
| 1–2 | region and country | ISO 3780 ranges (ISO overview 2021) |
| 9 | check digit | according to ISO 3779 / 49 CFR 565.15 |
| 10 | model year | the code repeats every 30 years |

- **Valid** are exactly 17 characters from 0–9 and A–Z without I, O, Q.
- **Check digit:** mandatory only in North America (VIN starts with 1–5) and China
  (`L`), there “correct” or “incorrect”. Otherwise “correct” if it happens to match or
  is used voluntarily, and “not mandatory” if not; that is not an error then.
- **Model year:** in North America position 7 decides (digit: 1980–2009, letter:
  2010–2039). Otherwise the most recent year up to at most one year in the future is the
  best guess, and the year 30 years earlier is named as well: “2026 or 1996 (without
  guarantee)”. If the VIN comes from the vehicle, years that do not fit the protocol are
  dropped (OBD-II not before 1994, CAN according to ISO 15765-4 not before 2000). Not
  all European manufacturers use position 10 as model year. At Mercedes-Benz (WMI WDB,
  WDC, WDD, WDF, W1K, W1N, W1V) position 10 is the steering side (`1` = left) and
  position 11 the plant; there the tool shows no model year (on the A 180 d, W177, it
  would otherwise have said “2001”).

## Online lookup (opt-in only)

The VIN stays on the computer. Only with `--online-vin` or the switch “Look up VIN
online (NHTSA)” does it go to the US database
[NHTSA vPIC](https://vpic.nhtsa.dot.gov/api/); model, model year, body, engine, fuel,
plant and similar are taken over. The response is stored per VIN under
`~/.cache/obd-diag/vpic/`, so a VIN is only queried once. Network errors are ignored.
For European models the details are often incomplete.
