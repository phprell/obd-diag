# Explaining trouble codes online

The offline catalog ({doc}`fehlercodes`) knows the standardised codes (SAE J2012), but
hardly any manufacturer-specific ones such as `P1xxx` or `U1xxx`. For such codes
obd-diag can optionally fetch a short explanation from the internet. This is **off by
default**.

```sh
obd-diag diagnose --port /dev/ttyUSB0 --online-codes
obd-diag scan --port /dev/ttyUSB0 --online-codes
```

In the user interface: menu *Options* → “Explain trouble codes online”. The setting is
stored per user and applies from the next scan.

## What is shown

Only codes **without** a catalog text are looked up. Each match shows the text from the
source (English), the file and a link to the line in the source:

```text
Stored:
  P1220  (no description in the catalog)
         Online, unverified: Fuel Quantity Actuator Y231
         Source: Wal33D/dtc-database (MIT), mercedes_codes.txt, https://github.com/…#L2
         Search the web: https://duckduckgo.com/?q=P1220+Mercedes-Benz
```

The explanation is also stored in the saved session (field `online` on the code) and in
the PDF report, there as “Online explanation (unverified)” with source.

:::{warning}
**Unverified.** The texts come from a freely available collection whose origin is not
documented, and they are often terse. They replace neither the manufacturer's
documentation nor a workshop. obd-diag therefore always shows them separately from the
catalog text and with their source.
:::

## Where the texts come from

The source is [Wal33D/dtc-database](https://github.com/Wal33D/dtc-database) (MIT
licence, which permits use and redistribution), pinned to one commit. It contains one
text file per manufacturer (e.g. `mercedes_codes.txt`) and a general one per code
family (`p_codes.txt`, `u_codes.txt` …).

- **Manufacturer-specific codes** are only looked up in the file of the manufacturer
  named by the VIN ({doc}`../technik/fin`). The same code means something else at
  another manufacturer: at Ford, `U1218` is a fault in the J1850 bus of the exterior
  lighting; on a Mercedes it can mean something completely different. Without a VIN
  (`scan`) or for a manufacturer without its own file they therefore remain without
  explanation.
- **Standardised codes** come from the manufacturer file if they are listed there,
  otherwise from the general file.
- The collection is small: for Mercedes-Benz it contains about 30 codes, all `P1xxx`.
  For many codes “Search the web” is all there is.

## What goes to the internet

- obd-diag downloads **whole files** of the source from `raw.githubusercontent.com`.
  The server only learns which file is needed (e.g. the Mercedes file), neither the
  trouble code nor the VIN.
- Each file is downloaded once and stored under `$XDG_CACHE_HOME/obd-diag/dtc-online/`
  (default `~/.cache/obd-diag/dtc-online/`). Because the commit is fixed, it never
  changes; after that nothing goes to the internet for it.
- Without a network or on errors the diagnosis carries on normally; the codes then
  simply have no online explanation.
- obd-diag never fetches “Search the web” (button in the user interface, link on the
  command line) itself. Only whoever opens the link in the browser sends code and
  manufacturer to the search engine. The button is there even without the online
  explanation.

Independently of this, `--online-vin` looks up the VIN at NHTSA vPIC
({doc}`sitzungen`); the two switches are separate.
