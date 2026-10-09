"""Erster Test am echten Auto (2026-10-09): Mercedes-Benz A 180 d (W177), Diesel,
Adapter FORScan ELMconfig (CH340) an /dev/ttyUSB0, Motor aus, Zündung an.

Die Mitschnitte unter ``tests/fixtures/traces/mercedes_w177/`` stammen von
``obd-diag diagnose --save --trace`` bzw. ``obd-diag info --trace``. Die Seriennummer der
FIN ist geschwärzt, die Antwort auf ``0142`` ist aus dem Live-Mitschnitt derselben
Sitzung eingefügt (Kopf der Datei). Abgespielt wird über ``ReplayTransport``: jeder
gesendete Befehl muss in Reihenfolge und Wortlaut dem Mitschnitt entsprechen.

Belegt sind hier die Befunde dieses Tests: vier Steuergeräte antworten, von denen nur
eines einen Freeze Frame hat; der Adapter misst 0,8 V weniger als das Motorsteuergerät;
das erste ``ATZ`` nach dem Einstecken ergibt ``?``; Stelle 10 der FIN ist bei Mercedes
kein Modelljahr.
"""

from pathlib import Path

import pytest

from obd_diag.protocol.elm327 import Elm327
from obd_diag.services.diagnostics import DiagnosticCode, DtcKind
from obd_diag.services.readiness import MonitorState
from obd_diag.services.session import run_diagnosis
from obd_diag.transport.trace import ReplayTransport

TRACES = Path(__file__).parent.parent / "fixtures" / "traces" / "mercedes_w177"


def test_diagnosis_replays_with_expected_result() -> None:
    session = run_diagnosis(Elm327(ReplayTransport.from_file(TRACES / "diagnose.log")), None)

    scan = session.scan
    assert scan.adapter == "ELM327 v1.5"
    assert scan.protocol == "ISO 15765-4 (CAN 29/500)"
    # ATRV 11,2 V, Motorsteuergerät (PID 42) 12,0 V: keine Warnung
    assert scan.voltage == pytest.approx(11.996)
    assert not scan.low_voltage
    assert scan.codes == [DiagnosticCode("U1218", DtcKind.STORED)]

    readiness = session.readiness
    assert readiness is not None
    assert not readiness.mil_on
    assert readiness.dtc_count == 1
    assert readiness.compression_ignition
    incomplete = [m.key for m in readiness.monitors if m.state is MonitorState.INCOMPLETE]
    assert len(incomplete) == 1  # Abgassensor
    assert not readiness.all_complete

    frame = session.freeze_frame
    assert frame is not None
    assert frame.dtc == "U1218"  # nicht das 00 00 der drei anderen Steuergeräte
    assert frame.values == {
        "engine_load_pct": 0.0,
        "coolant_temp_c": 37,
        "rpm": 0.0,
        "speed_kmh": 0,
    }

    vehicle = session.vehicle
    assert vehicle is not None
    assert vehicle.vin == "WDD1770031J000000"
    assert vehicle.manufacturer == "Mercedes-Benz"
    assert vehicle.country == "Deutschland"
    assert vehicle.model_year is None  # Stelle 10 = Lenkung, nicht „2001“


def test_first_atz_after_plugging_in_is_repeated() -> None:
    elm = Elm327(ReplayTransport.from_file(TRACES / "atz_rejected.log"))
    assert elm.initialize() == "ELM327 v1.5"
    assert elm.voltage() == 11.5
