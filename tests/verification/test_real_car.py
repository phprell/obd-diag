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
kein Modelljahr. ``live.log`` (``obd-diag live --record``) enthält keine FIN. Der erste
Versuch mit Zündung aus endete mit ``UNABLE TO CONNECT``.

Zweiter Test (2026-10-10): ``diagnose_order.log`` (Motor aus) und
``diagnose_engine_running.log`` (Motor läuft im Stand, ``--save``). Ohne Header kommen die
Antworten der vier Steuergeräte bei jeder Anfrage anders sortiert; in
``diagnose_order.log`` steht auf ``0101`` das Steuergerät ohne Abgasmonitore (Bit B3 = 0)
vorn, was vorher einen Ottomotor ohne offene Tests ergab.
"""

from pathlib import Path

import pytest

from obd_diag.protocol.elm327 import Elm327, NoConnectionError
from obd_diag.services.diagnostics import DiagnosticCode, DtcKind
from obd_diag.services.live import LiveSample, prepare_live, run_live, select_pids
from obd_diag.services.readiness import MonitorState
from obd_diag.services.session import run_diagnosis
from obd_diag.transport.trace import ReplayTransport, read_trace

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


def test_live_data_not_throttled_by_low_adapter_voltage() -> None:
    """``live --record``, 20 s im Stand: vorher Runden nur alle 5 s, weil ATRV 11,2 V
    zeigte; mit der Spannung des Motorsteuergeräts (12,0 V) jetzt jede Sekunde."""
    elm = Elm327(ReplayTransport.from_file(TRACES / "live.log"))
    setup = prepare_live(elm)
    pids = select_pids(setup, None)
    # Ansauglufttemperatur (010F) meldet keins der vier Steuergeräte
    assert [p.key for p in pids] == [
        "rpm",
        "speed",
        "coolant_temp",
        "engine_load",
        "control_voltage",
    ]
    now = [0.0]

    def sleep(seconds: float) -> None:
        now[0] += seconds

    samples: list[LiveSample] = []
    rounds = run_live(
        elm,
        pids,
        on_sample=samples.append,
        should_stop=lambda: False,
        max_samples=4,
        clock=lambda: now[0],
        sleep=sleep,
    )
    assert rounds == 4
    assert [s.elapsed for s in samples] == pytest.approx([0.0, 1.0, 2.0, 3.0])
    assert not any(s.throttled for s in samples)
    assert samples[0].voltage == pytest.approx(11.996)
    assert samples[0].values == {
        "rpm": 0.0,
        "speed": 0,
        "coolant_temp": 19,
        "engine_load": 0.0,
        "control_voltage": pytest.approx(11.996),
    }
    assert [s.values["control_voltage"] for s in samples] == pytest.approx(
        [11.996, 12.001, 12.006, 12.007]
    )


def test_ignition_off_reports_no_connection_with_hint() -> None:
    """Erster Versuch mit Zündung aus: Protokollsuche endet mit ``UNABLE TO CONNECT``."""
    entries = read_trace(TRACES / "diagnose.log")
    first_query = entries.index((">>", b"0100\r"))
    unable = ("<<", b"SEARCHING...\rUNABLE TO CONNECT\r\r>")
    replay = ReplayTransport([*entries[: first_query + 1], unable])
    with pytest.raises(NoConnectionError) as error:
        run_diagnosis(Elm327(replay), None)
    assert str(error.value) == "0100: UNABLE TO CONNECT"
    assert "Zündung einschalten" in NoConnectionError.HINT


@pytest.mark.parametrize(
    ("trace", "voltage"),
    [("diagnose_order.log", 12.391), ("diagnose_engine_running.log", 13.9)],
)
def test_second_test_diagnosis(trace: str, voltage: float) -> None:
    session = run_diagnosis(Elm327(ReplayTransport.from_file(TRACES / trace)), None)
    assert session.scan.voltage == pytest.approx(voltage)
    assert session.scan.codes == [DiagnosticCode("U1218", DtcKind.STORED)]

    readiness = session.readiness
    assert readiness is not None
    assert readiness.compression_ignition  # nicht Otto, auch wenn 00 04 00 00 vorn steht
    assert readiness.dtc_count == 1
    assert not readiness.mil_on
    states = {m.key: m.state for m in readiness.monitors}
    assert [k for k, s in states.items() if s is MonitorState.INCOMPLETE] == ["exhaust_gas_sensor"]
    assert states["misfire"] is MonitorState.NOT_SUPPORTED
    assert not readiness.all_complete

    frame = session.freeze_frame
    assert frame is not None
    assert frame.dtc == "U1218"
    assert frame.values == {
        "engine_load_pct": 0.0,
        "coolant_temp_c": 37,
        "rpm": 0.0,
        "speed_kmh": 0,
    }

    vehicle = session.vehicle
    assert vehicle is not None
    assert vehicle.vin == "WDD1770031J000000"
    assert vehicle.model_year is None
