# Command reference

This is the complete list of what obd-diag may send to the adapter, with page and
verbatim quote from the ELM327 datasheet (ELM327DSJ, firmware v2.1; page numbers
according to the footer “N of 94”). Everything else is rejected before sending
({doc}`sicherheit`).

The tables are generated from `tests/fixtures/command_spec.yaml` when the
documentation is built. The tests check the same file against the allowlist in the
code (`tests/verification/test_command_spec.py`, exhaustively over all hex commands)
and against every command sent in any test. So the list here can deviate neither from
the code nor from the tests.

Commands are written as they appear on the line: upper case, no spaces, followed by a
CR. Regular expressions (`01[0-9A-F]{2}`) stand for a group of commands, e.g. `010C`.

```{include} ../_gen/befehle.md
```
