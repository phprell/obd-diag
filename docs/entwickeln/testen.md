# Testen ohne Auto

Bis zum ersten Test am echten Fahrzeug ist alles ohne Hardware geprüft. Die Tests
liegen in vier Ordnern:

| Ordner | Inhalt |
| --- | --- |
| `tests/unit` | einzelne Module gegen `FakeTransport`; Sicherheitstests, Hypothesis |
| `tests/integration` | Abläufe gegen den ELM327-emulator am pty, abgezogener Adapter |
| `tests/verification` | Antwortverarbeitung gegen Datenblatt-Beispiele, echte Mitschnitte und python-OBD |
| `tests/ui` | Oberfläche ohne Bildschirm (`QT_QPA_PLATFORM=offscreen`) |

## Emulator

Der [ELM327-emulator](https://github.com/Ircama/ELM327-emulator) stellt ein virtuelles
serielles Gerät bereit:

```sh
uv run python tools/emulator.py                    # zeigt das pty an, z. B. /dev/pts/5
uv run python tools/emulator.py --stored P0420,P0300 --pending P0171 --engine-off
uv run obd-diag diagnose --port /dev/pts/5
```

Version 4.0.0 weicht vom Standard ab: kein CAN-Zählbyte bei Fehlercodes, mehrteilige
Frames ohne Header nicht im ELM-Format, Mode 02 ohne Frame-Nummer, wechselnde FIN,
Drehzahl nie 0. Das korrigiert `tests/emulator_patches.py` (in allen Tests aktiv und
von `tools/emulator.py` genutzt), nicht der Produktcode. `--engine-off` meldet
Drehzahl 0, damit sich der Löschablauf ausprobieren lässt.

## Testhilfen

- `tests/fakes.py`: `FakeTransport` antwortet nach Tabelle (`headers_on`, `later`,
  `after_clear`); `CAN_CAR`, `CAN_CAR_FULL` sind fertige Fahrzeuge.
- `tests/samples.py`: `full_session`, `minimal_session`.
- `tests/fixtures/traces/`: echte Mitschnitte anderer Programme mit Quellenangabe.
- `ReplayTransport` (`transport/trace.py`) spielt einen eigenen Mitschnitt ohne Adapter
  ab. Aus dem Mitschnitt des ersten Tests am Auto wird so ein Regressionstest.

## Verifikation ohne Hardware

- Beispiele aus dem ELM327-Datenblatt und Nutzer-Logs aus python-OBD, ELMduino und
  AndrOBD laufen durch die Parser.
- Der DTC-Decoder wird mit dem von python-OBD über alle 65536 Byte-Paare verglichen,
  jede PID-Formel über alle Bytewerte (`test_pid_differential.py`).
- Hypothesis-Round-Trips und Fuzzing für Frames, Header und Antworten.

## Mutationstests

```sh
uv run --with mutmut mutmut run       # Ziele in [tool.mutmut], rund zwei Minuten
uv run --with mutmut mutmut results
```

In `services/clear.py` darf kein Mutant überleben. Danach `mutants/` löschen; die
Testsuite legt außerdem `elm.log` an (gitignored).
