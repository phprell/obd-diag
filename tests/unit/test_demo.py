"""Demo-Fahrzeug (``transport/demo.py``): antwortet wie der ELM327 am W177.

Diagnose, Live-Daten und Löschen laufen hier durch den echten Treiber und die echten
Dienste; verglichen wird mit dem, was die Mitschnitte vom Auto ergeben haben.
"""

import io
import json
from pathlib import Path

import pytest

from obd_diag import cli
from obd_diag.protocol.elm327 import Elm327, is_read_only
from obd_diag.protocol.headers import parse_header_response
from obd_diag.services.clear import ClearRefused, clear_codes
from obd_diag.services.diagnostics import DtcKind
from obd_diag.services.live import LiveSample, prepare_live, run_live, select_pids
from obd_diag.services.readiness import MonitorState
from obd_diag.services.session import run_diagnosis
from obd_diag.services.used_car import ClearCounters
from obd_diag.transport import TransportTimeout
from obd_diag.transport.demo import ADAPTER_ID, DEMO_PORT, DemoTransport
from obd_diag.transport.trace import TracingTransport, open_serial, read_trace


def _ask(transport: DemoTransport, cmd: str) -> str:
    transport.write(f"{cmd}\r".encode())
    return transport.read_until(b">", 1.0).decode()


def _sent(log: io.StringIO, tmp_path: Path) -> list[str]:
    path = tmp_path / "trace.log"
    path.write_text(log.getvalue(), encoding="utf-8")
    return [data.decode().rstrip("\r") for d, data in read_trace(path) if d == ">>"]


def test_diagnosis_matches_the_real_car() -> None:
    with DemoTransport() as transport:
        session = run_diagnosis(Elm327(transport), None)

    assert session.scan.adapter == ADAPTER_ID
    assert "Demo" in session.scan.adapter  # landet so in Sitzung und Bericht
    assert session.scan.protocol == "ISO 15765-4 (CAN 29/500)"
    assert session.scan.voltage == pytest.approx(13.9)
    assert [(c.code, c.kind) for c in session.scan.codes] == [("U1218", DtcKind.STORED)]

    readiness = session.readiness
    assert readiness is not None
    assert readiness.compression_ignition  # Diesel, unabhängig vom ersten Steuergerät
    assert not readiness.mil_on
    assert readiness.dtc_count == 1
    open_tests = [m.key for m in readiness.monitors if m.state is MonitorState.INCOMPLETE]
    assert len(open_tests) == 1 and "sensor" in open_tests[0]
    assert not readiness.all_complete

    assert session.freeze_frame is not None
    assert session.freeze_frame.dtc == "U1218"
    assert session.freeze_frame.values  # Motorlast, Temperatur, Drehzahl, Tempo

    assert session.vehicle is not None
    assert session.vehicle.vin == "WDD1770031J000000"

    # Gebrauchtwagen-Check: 21, 30 und 31 meldet das Auto (Werte erfunden), 4D/4E nicht
    assert session.clear_counters == ClearCounters(warmups=40, distance_km=3000, mil_distance_km=0)
    assert session.vehicle.manufacturer == "Mercedes-Benz"


def test_demo_answers_mode_09_and_mode_06() -> None:
    # erfundene Beispielwerte, aber durch Treiber und Parser wie am Auto
    with DemoTransport() as transport:
        session = run_diagnosis(Elm327(transport), None)
    assert session.ecus is not None
    engine, gearbox = session.ecus
    assert (engine.ecu, engine.name) == ("18DAF110", "ECM-EngineControl")
    assert (gearbox.ecu, gearbox.name) == ("18DAF118", "TCM-TransmissionCtl")
    assert engine.performance is not None and engine.performance.compression_ignition
    sensor = next(c for c in engine.performance.counters if c.key == "exhaust_gas_sensor")
    assert sensor.completed == 0  # passt zum offenen Readiness-Test Abgassensor
    assert session.tests is not None
    assert len(session.tests) == 7
    assert {t.ecu for t in session.tests} == {"18DAF110"}
    assert [t.status.value for t in session.tests].count("no_result") == 1
    assert all(t.status.value != "failed" for t in session.tests)


def test_live_cycles_through_the_recorded_idle_rounds() -> None:
    samples: list[LiveSample] = []
    with DemoTransport() as transport:
        elm = Elm327(transport)
        setup = prepare_live(elm)
        keys = {spec.key for spec in setup.available}
        # aufgezeichnet bzw. als Beispielwert ergänzt: alles, was das Auto anbietet
        assert {"rpm", "speed", "coolant_temp", "engine_load", "control_voltage"} <= keys
        assert {"distance_since_clear", "warmups_since_clear", "maf_a"} <= keys
        pids = select_pids(setup, None)
        run_live(
            elm,
            pids,
            on_sample=samples.append,
            should_stop=lambda: False,
            interval=0,
            max_samples=12,
        )

    assert len(samples) == 12
    rpms = [s.values["rpm"] for s in samples]
    assert rpms[0] == 864  # 0x0D80 / 4, erstes Steuergerät der ersten Runde
    assert rpms[11] == rpms[0]  # nach elf Runden wieder von vorn
    assert len(set(rpms)) > 3  # der Leerlauf schwankt
    assert all(s.values["speed"] == 0 for s in samples)
    assert all(s.values["coolant_temp"] == 62 for s in samples)
    assert samples[0].voltage == pytest.approx(13.9)


def test_every_supported_value_has_an_answer() -> None:
    with DemoTransport() as transport:
        elm = Elm327(transport)
        setup = prepare_live(elm)
        samples: list[LiveSample] = []
        run_live(
            elm,
            setup.available,
            on_sample=samples.append,
            should_stop=lambda: False,
            interval=0,
            max_samples=1,
        )
    missing = {key for key, value in samples[0].values.items() if value is None}
    assert missing == {"maf_b"}  # Sensor B meldet das Steuergerät als nicht vorhanden


def test_only_allowed_commands_are_sent(tmp_path: Path) -> None:
    log = io.StringIO()
    with TracingTransport(DemoTransport(), log) as transport:
        elm = Elm327(transport)
        run_diagnosis(elm, None)
        setup = prepare_live(elm)
        run_live(
            elm,
            setup.available,
            on_sample=lambda s: None,
            should_stop=lambda: False,
            interval=0,
            max_samples=2,
        )
    sent = _sent(log, tmp_path)
    assert sent and all(is_read_only(cmd) for cmd in sent)


def test_clear_stops_at_the_running_engine(tmp_path: Path) -> None:
    # Löschen ist für Tests freigegeben (conftest); das Demo-Auto läuft im Leerlauf.
    log = io.StringIO()
    with (
        TracingTransport(DemoTransport(), log) as transport,
        pytest.raises(ClearRefused),
    ):
        clear_codes(Elm327(transport), None, backup_dir=tmp_path / "backups")
    assert "04" not in _sent(log, tmp_path)
    assert not (tmp_path / "backups").exists()


def test_adapter_behaviour() -> None:
    with DemoTransport() as transport:
        assert _ask(transport, "ATZ") == f"ATZ\r\r{ADAPTER_ID}\r\r>"
        assert _ask(transport, "ATE0") == "ATE0\rOK\r\r>"  # Echo bis hierher an
        assert _ask(transport, "ATDPN") == "A0\r\r>"  # noch nicht gesucht
        assert _ask(transport, "ATS0") == "OK\r\r>"
        assert _ask(transport, "0100").startswith("SEARCHING...\r410098180011\r")
        assert not _ask(transport, "0100").startswith("SEARCHING")
        assert _ask(transport, "ATDPN") == "A7\r\r>"
        assert _ask(transport, "0110") == "NO DATA\r\r>"
        assert _ask(transport, "ATMA") == "?\r\r>"
        assert _ask(transport, "0202") == _ask(transport, "020200")
        assert _ask(transport, "0902") == (
            "014\r0:490201574444\r1:31373730303331\r2:4A303030303030\r\r>"
        )
        assert _ask(transport, "ATS1") == "OK\r\r>"
        assert _ask(transport, "0105") == "41 05 66\r" * 4 + "\r>"
        with pytest.raises(TransportTimeout):
            transport.read_until(b">", 1.0)


def test_headers_give_the_same_messages_per_ecu() -> None:
    with DemoTransport() as transport:
        elm = Elm327(transport)
        elm.initialize()
        elm.protocol()
        for cmd in ("0100", "0101", "03", "0902", "020200"):
            plain = elm.query(cmd)
            with_headers = elm.query_with_headers(cmd)
            assert plain is not None and with_headers is not None
            messages = parse_header_response(with_headers, can=True)
            assert {m.header[:6] for m in messages} == {"18DAF1"}
            # ohne Header: Einzel-Frames als Zeilen, die FIN als ein Block
            expected = (
                [bytes.fromhex("490201574444313737303033314A303030303030")]
                if cmd == "0902"
                else [bytes.fromhex(line) for line in plain.split()]
            )
            assert sorted(m.data for m in messages) == sorted(expected)


def test_open_serial_opens_the_demo_for_the_demo_port(tmp_path: Path) -> None:
    assert isinstance(open_serial(DEMO_PORT, 38400), DemoTransport)
    trace = tmp_path / "demo.log"
    with open_serial(DEMO_PORT, 38400, trace) as transport:
        Elm327(transport).initialize()
    assert "ELM327 v1.5 (Demo)" in trace.read_text(encoding="utf-8")


@pytest.mark.parametrize("args", [["--demo"], ["--port", "demo"]])
def test_cli_diagnose_demo(args: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--lang", "de", "diagnose", *args]) == 0
    out, err = capsys.readouterr()
    assert "U1218" in out
    assert "ELM327 v1.5 (Demo)" in out
    assert "Demo-Modus" in err


def test_cli_ecus_and_tests_demo(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--lang", "de", "ecus", "--demo"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Steuergeräte (Mode 09):\n  18DAF110  ECM-EngineControl\n")
    assert "Kalibrierungs-ID:" in out and "A6519006400" in out
    assert "      Abgassensor:" in out and "0 von 96 (0.00)" in out
    assert cli.main(["--lang", "de", "tests", "--demo"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Testergebnisse (Mode 06, 7 Tests, davon 0 nicht bestanden):\n")
    assert (
        "    Partikelfilter Bank 1 · Herstellertest 80\n"
        "      1.50 kPa  (Grenzen 0.00 kPa bis 5.00 kPa)  bestanden\n"
    ) in out
    assert "    Lambdasonde Bank 1 Sensor 1 · Herstellertest 80\n      kein Ergebnis\n" in out


def test_cli_ecus_and_tests_json_demo(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["ecus", "--demo", "--json"]) == 0
    ecus = json.loads(capsys.readouterr().out)
    assert [e["ecu"] for e in ecus] == ["18DAF110", "18DAF118"]
    assert cli.main(["tests", "--demo", "--json"]) == 0
    tests = json.loads(capsys.readouterr().out)
    assert tests[1] == {
        "ecu": "18DAF110",
        "mid": 0x21,
        "tid": 0x80,
        "uasid": 0x24,
        "value_raw": 100,
        "min_raw": 0,
        "max_raw": 200,
        "value": 100,
        "min": 0,
        "max": 200,
        "unit": "",
        "status": "passed",
    }


def test_cli_demo_in_english(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--lang", "en", "info", "--demo"]) == 0
    assert "Demo mode" in capsys.readouterr().err
