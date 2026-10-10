# ADR 0004: Online explanations for trouble codes without catalog text

Status: accepted (2026-10-09)

## Context
The offline catalog (OBDex, CC0) only covers standardised codes. In the first test at
the car (Mercedes A 180 d, W177) the vehicle reported `U1218`, a manufacturer-specific
code without catalog text. An optional short explanation from the internet is wanted.
So far only the VIN went online, to NHTSA vPIC, and only after opt-in.

## Decision
- Off by default; switched on with `--online-codes` (`scan`, `diagnose`) or “Explain
  trouble codes online” in the user interface (QSettings). Only codes without catalog
  text are looked up (`services/dtc_online.py`, called via `add_online_explanations`).
- The source is [Wal33D/dtc-database](https://github.com/Wal33D/dtc-database) (MIT),
  pinned to one commit. Web searches or sites such as obd-codes.com are not queried
  automatically: their terms do not allow it, and a search engine API would need its
  own key.
- Whole files per manufacturer or code family are downloaded. So the only thing that
  goes to the internet is which file is needed, neither code nor VIN. Because of the
  pinned commit they are cached permanently (`~/.cache/obd-diag/dtc-online/<commit>/`).
- Manufacturer-specific codes only come from the file of the manufacturer according to
  the VIN, never from the mixed `other_codes.txt` or another manufacturer's file
  (where the same code means something else). Without a manufacturer, no lookup.
- Texts always appear separately from the catalog text as “unverified”, with source
  and a link to the line. Network errors leave the explanation out, the diagnosis
  carries on.
- In addition a “Search the web” link (DuckDuckGo) that obd-diag never fetches itself.

## Consequences
- The collection is small (Mercedes: about 30 `P1xxx` codes) and its origin is not
  documented. `U1218` on the W177 remains without explanation; that is what the search
  link is for. A better source can be swapped in within `dtc_online.py` without
  changing CLI, GUI or session format.
- No test downloads anything from the internet (`tests/conftest.py` blocks
  `dtc_online.urlopen`).
