"""Dritter Test am echten Auto (2026-10-16): Mercedes-Benz A 180 d (W177), Motor aus,
Zündung an, obd-diag 0.2.2 über die GUI mit Mitschnitt, zwei Läufe im Abstand von vier
Minuten (``diagnose_mode09.log``, ``diagnose_mode09_order.log``; FIN-Seriennummer
geschwärzt, sonst unverändert).

Erstmals echte Antworten auf Mode 09 je Steuergerät und Mode 06 (bis dahin nur erfundene
Demo-Antworten). Befunde:

- ``0904`` und ``090A`` enden mit ``BUFFER FULL``: vier Steuergeräte antworten
  gleichzeitig mehrteilig, mit Headern und 38400 Baud läuft der Puffer des Adapters über.
  Vorher fehlten dadurch alle Namen und Kalibrierungs-IDs. Jetzt zählt, was vollständig
  ankam, und die Anfrage wird einmal wiederholt. Die Reihenfolge der Antworten wechselt
  (siehe ``test_real_car.py``), daher ergänzt die Wiederholung die fehlenden Namen. Die
  vier Kalibrierungs-IDs des Motorsteuergeräts (18DAF158, 67 Bytes) kamen in keinem Lauf
  vollständig an.
- P24C7 ist nur ausstehend (Mode 07), Mode 03 meldet U1218, die Kontrollleuchte ist aus.
- Mode 06: ein nicht bestandener Herstellertest (MID 03, TID A2 am Motorsteuergerät),
  Grenzen mit Vorzeichen (UASID 84, 96, AF).

Für die Wiederholung wird die Antwort des anderen Laufs auf denselben Befehl eingefügt:
echte Antworten desselben Autos, nur nicht unmittelbar hintereinander aufgezeichnet.
"""

from pathlib import Path

import pytest

from obd_diag.protocol import monitor_tests
from obd_diag.protocol.elm327 import Elm327
from obd_diag.services.diagnostics import DiagnosticCode, DtcKind
from obd_diag.services.session import Session, run_diagnosis
from obd_diag.services.urgency import catalog_mil_text
from obd_diag.transport import TransportError
from obd_diag.transport.trace import ReplayTransport, read_trace

TRACES = Path(__file__).parent.parent / "fixtures" / "traces" / "mercedes_w177"
FIRST = "diagnose_mode09.log"
SECOND = "diagnose_mode09_order.log"
RETRIED = (b"0904\r", b"090A\r")


def _answer(entries: list[tuple[str, bytes]], cmd: bytes) -> tuple[str, bytes]:
    return entries[entries.index((">>", cmd)) + 1]


def _with_retries(name: str, other: str) -> list[tuple[str, bytes]]:
    """Mitschnitt ``name``; nach 0904 und 090A folgt die Wiederholung mit der Antwort aus
    ``other``."""
    entries = read_trace(TRACES / name)
    second = read_trace(TRACES / other)
    out: list[tuple[str, bytes]] = []
    for direction, data in entries:
        out.append((direction, data))
        if direction == "<<" and out[-2][1] in RETRIED:
            out += [(">>", out[-2][1]), _answer(second, out[-2][1])]
    return out


def _diagnosis(name: str, other: str) -> tuple[Session, ReplayTransport]:
    transport = ReplayTransport(_with_retries(name, other))
    return run_diagnosis(Elm327(transport), None), transport


@pytest.mark.parametrize(("name", "other"), [(FIRST, SECOND), (SECOND, FIRST)])
def test_both_runs_end_with_buffer_full(name: str, other: str) -> None:
    entries = read_trace(TRACES / name)
    for cmd in RETRIED:
        assert _answer(entries, cmd)[1].endswith(b"BUFFER FULL\r\r>")


@pytest.mark.parametrize(("name", "other"), [(FIRST, SECOND), (SECOND, FIRST)])
def test_ecu_names_complete_after_one_retry(name: str, other: str) -> None:
    session, _ = _diagnosis(name, other)
    assert session.ecus is not None
    names = {e.ecu: e.name for e in session.ecus}
    assert names == {
        "18DAF158": "ECM-EngineControl",
        "18DAF159": "PCM-PowertrainCtrl",
        "18DAF15A": "TCM-TransmisCtrl",
        "18DAF15D": "ECCI-EmisCritInfo",
    }


def test_calibration_ids_of_the_ecus_that_fit_into_the_buffer() -> None:
    session, _ = _diagnosis(FIRST, SECOND)
    assert session.ecus is not None
    calids = {e.ecu: e.calibration_ids for e in session.ecus}
    assert calids == {
        "18DAF158": (),  # 4 IDs, 67 Bytes: in keinem Lauf vollständig
        "18DAF159": ("0009018906030385",),
        "18DAF15A": ("0009012604030109",),
        "18DAF15D": ("0009031536290001",),
    }


def test_without_retry_answer_the_complete_part_counts() -> None:
    """Ohne zweite Antwort (Wiederholung bricht ab): was vor BUFFER FULL vollständig
    war, bleibt."""
    entries = read_trace(TRACES / FIRST)
    out: list[tuple[str, bytes]] = []
    for direction, data in entries:
        out.append((direction, data))
        if direction == "<<" and out[-2][1] in RETRIED:
            out += [(">>", out[-2][1]), ("<<", b"NO DATA\r\r>")]
    session = run_diagnosis(Elm327(ReplayTransport(out)), None)
    assert session.ecus is not None
    names = {e.ecu: e.name for e in session.ecus}
    # erster Lauf: 090A vollständig nur von 18DAF159 und 18DAF15A
    assert names == {
        "18DAF158": None,
        "18DAF159": "PCM-PowertrainCtrl",
        "18DAF15A": "TCM-TransmisCtrl",
        "18DAF15D": None,
    }


def test_cvns_and_in_use_performance() -> None:
    session, _ = _diagnosis(FIRST, SECOND)
    assert session.ecus is not None
    ecus = {e.ecu: e for e in session.ecus}
    assert ecus["18DAF158"].cvns == ("6ACC87FE", "F6D7F1BD", "F6D7F1BD", "9E9076F1")
    assert ecus["18DAF159"].cvns == ("317BCC2B",)
    assert ecus["18DAF158"].supported == (2, 4, 6, 10, 11, 15)
    engine = ecus["18DAF158"].performance
    assert engine is not None
    assert engine.compression_ignition
    assert (engine.obd_conditions, engine.ignition_cycles) == (1948, 5442)
    counters = {c.key: (c.completed, c.conditions) for c in engine.counters}
    assert counters["nox_catalyst"] == (606, 1948)
    assert counters["pm_filter"] == (931, 102)
    assert counters["exhaust_gas_sensor"] == (810, 1948)
    emissions = ecus["18DAF15D"].performance
    assert emissions is not None
    assert emissions.ignition_cycles == 5429


def test_p24c7_is_only_pending_and_the_lamp_is_off() -> None:
    session, _ = _diagnosis(FIRST, SECOND)
    assert session.scan.codes == [
        DiagnosticCode("U1218", DtcKind.STORED),
        DiagnosticCode("P24C7", DtcKind.PENDING),
    ]
    assert session.readiness is not None
    assert not session.readiness.mil_on
    # Katalog: P24C7 schaltet die Leuchte ein, aber erst bestätigt
    assert catalog_mil_text(True, DtcKind.PENDING) == "Kontrollleuchte erst, wenn bestätigt"
    assert session.vehicle is not None
    assert session.vehicle.vin == "WDD1770031J000000"


def test_mode06_results_with_signed_limits() -> None:
    session, _ = _diagnosis(FIRST, SECOND)
    assert session.tests is not None
    assert len(session.tests) == 24
    by_key = {(t.ecu, t.mid, t.tid): t for t in session.tests}
    failed = [k for k, t in by_key.items() if t.status is monitor_tests.TestStatus.FAILED]
    assert failed == [("18DAF158", 0x03, 0xA2)]
    test = by_key["18DAF158", 0x03, 0xA2]
    # UASID 96: Temperatur mit Vorzeichen, 0,1 °C; Min 8000 = kleinster Wert
    assert test.uasid == 0x96
    assert test.value == pytest.approx(123.3)
    assert test.minimum == pytest.approx(-3276.8)
    assert test.maximum == pytest.approx(40.0)
    lambda_like = by_key["18DAF158", 0x01, 0x91]  # UASID 84: FFCA = -54 → -0,054
    assert lambda_like.value == pytest.approx(-0.054)
    assert lambda_like.minimum == pytest.approx(-0.801)
    nox = by_key["18DAF15D", 0x98, 0x83]  # UASID AF: FD62 = -670 → -6,70 %
    assert nox.value == pytest.approx(-6.7)
    assert nox.minimum == pytest.approx(-45.0)
    assert by_key["18DAF158", 0xB2, 0xC2].status is monitor_tests.TestStatus.NO_RESULT


def test_replay_consumes_every_recorded_command() -> None:
    _, transport = _diagnosis(FIRST, SECOND)
    with pytest.raises(TransportError):
        transport.write(b"0100\r")
