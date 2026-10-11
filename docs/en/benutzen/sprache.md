# Language

obd-diag speaks English and German: user interface, command line, PDF report, CSV and
the trouble code texts from the catalog.

## Which language applies

- **User interface:** menu *Options → Sprache / Language*, then *Deutsch* or *English*.
  The menu name is bilingual so that it can be found in either language. The choice is
  stored per user (`~/.config/obd-diag/obd-diag.conf`) and applies immediately to all
  menus, tabs and messages. Trouble code texts from the catalog switch at the next
  scan.
- **Command line:** `--lang en` or `--lang de`, before or after the subcommand
  (`obd-diag --lang en scan` or `obd-diag scan --lang en`). It applies to all output,
  the help texts and the trouble code texts.
- **Default:** the system language as for other programs (`LANGUAGE`, `LC_ALL`,
  `LC_MESSAGES`, `LANG`). German if the system language is German, otherwise English.

```sh
obd-diag --lang en diagnose --port /dev/ttyUSB0 --pdf report.pdf
LANG=de_DE.UTF-8 obd-diag scan --port /dev/ttyUSB0     # German, from the system language
```

## What changes with the language

| | English | German |
| --- | --- | --- |
| Texts and trouble code catalog | English | German |
| Numbers in the user interface and the PDF | `12.4 V` | `12,4 V` |
| CSV (export and live recording) | `,` separator, decimal point | `;` separator, decimal comma |
| Date in the PDF report | `2026-10-07, 14:32` | `07.10.2026, 14:32 Uhr` |

Saved sessions are language-neutral except for the trouble code texts, which keep the
language of the scan ({doc}`sitzungen`). Traces, log messages and the developer
documentation in the code (docstrings, comments) stay German.

## Translations

The texts in the code are German; the English translations are in
`src/obd_diag/locale/en.po`. How to add new texts is described in
{doc}`../entwickeln/regeln`.
