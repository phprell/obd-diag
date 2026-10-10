# Sources

| Abbreviation | Source | Used for |
| --- | --- | --- |
| ELM327DSJ | ELM327 datasheet “ELM327 OBD to RS232 Interpreter”, firmware v2.1, © Elm Electronics 2014, 94 pages. Original at [Elm Electronics](https://www.elmelectronics.com/); retrieved 2026-10 via [this copy](https://github.com/Obeisance/Arduino_OBD_interface_for_Torque/blob/master/ELM327DS.pdf). Pages according to the footer “N of 94”. | every AT command, response formats, timeouts, error messages ({doc}`befehle`) |
| J1979 | SAE J1979 / ISO 15031-5, diagnostic services for emissions-related systems (paid); details confirmed via the mode list in the datasheet (p. 31) and [Wikipedia: OBD-II PIDs](https://en.wikipedia.org/wiki/OBD-II_PIDs) | modes, PID formulas, readiness ({doc}`dienste`, {doc}`pids`) |
| J2012 | SAE J2012, structure of the trouble codes | P/C/B/U and four digits from two bytes |
| ISO 15765-2/-4 | ISO-TP (splitting long messages) and OBD over CAN | frames, PCI, count byte ({doc}`antwortformate`) |
| ISO 14229-1 | UDS, negative responses (`7F`, reasons `11`, `12`, `21`, `22`, `78`) | rejections |
| ISO 3779, ISO 3780, 49 CFR 565 | VIN, WMI, check digit, model year | {doc}`fin` |
| OBDex | [foerbsnavi/OBDex](https://github.com/foerbsnavi/OBDex), data CC0-1.0 | plain texts of the trouble codes |
| dtc-database | [Wal33D/dtc-database](https://github.com/Wal33D/dtc-database), MIT, commit `04c43d7`; origin of the texts not documented | optional, unverified online explanations ({doc}`../benutzen/fehlercodes-online`) |
| python-OBD | [brendan-w/python-OBD](https://github.com/brendan-w/python-OBD), GPL-2.0, only in tests | comparison of the DTC and PID decoding |
| Traces | user logs from python-OBD, ELMduino, AndrOBD, sources per file in `tests/fixtures/traces/` | regression tests without hardware |

The datasheet is only quoted and linked here, not republished. The quotes are short
verbatim excerpts with page number.
