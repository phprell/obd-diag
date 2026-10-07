"""``run_diagnosis`` mit dem Fake-Adapter: vollständig, Teilausfälle, vPIC-Opt-in."""

import json
from datetime import datetime
from typing import Any

import pytest

from obd_diag.protocol.elm327 import Elm327, ElmError
from obd_diag.protocol.obd import FreezeFrame
from obd_diag.services import session as session_module
from obd_diag.services import vehicle
from obd_diag.services.diagnostics import DtcKind
from obd_diag.services.readiness import MonitorState, decode_readiness
from obd_diag.services.session import run_diagnosis, session_from_dict, session_to_dict
from obd_diag.transport import TransportError
from tests.fakes import CAN_CAR_FULL as FULL_CAR
from tests.fakes import FakeCatalog, FakeTransport


def _run(responses: dict[str, str], **kwargs: Any) -> tuple[Any, FakeTransport]:
    transport = FakeTransport(responses)
    catalog = FakeCatalog({"P0133": "Lambdasonde reagiert zu langsam"})
    return run_diagnosis(Elm327(transport), catalog, "de", **kwargs), transport


def test_full_diagnosis() -> None:
    session, transport = _run(FULL_CAR)
    assert session.created.tzinfo is not None
    assert abs((datetime.now().astimezone() - session.created).total_seconds()) < 60
    assert [c.code for c in session.scan.codes if c.kind is DtcKind.STORED] == [
        "P0133",
        "P0300",
        "P0171",
    ]
    assert session.readiness == decode_readiness(bytes.fromhex("83076521"))
    assert session.freeze_frame == FreezeFrame(
        dtc="P0133",
        raw={"020200": "4202000133", "020500": "42050073", "020C00": "420C001AF8"},
        values={"coolant_temp_c": 75, "rpm": 1726.0},
    )
    assert session.vehicle is not None
    assert session.vehicle.vin == "WVWZZZ1KZ6W123456"
    assert session.vehicle.manufacturer == "Volkswagen"
    assert session.vehicle.online == {}
    # nur lesend, Adapter-Reset zuerst
    assert transport.sent[0] == "ATZ"
    assert "04" not in transport.sent
    assert {"0101", "0902", "020200"} <= set(transport.sent)
    # speicherbar
    assert session_from_dict(json.loads(json.dumps(session_to_dict(session)))) == session


@pytest.mark.parametrize(
    ("change", "missing"),
    [
        ({"0101": "NO DATA"}, "readiness"),
        ({"0101": "CAN ERROR"}, "readiness"),
        ({"0101": "OK"}, "readiness"),  # kein Hex: ElmError
        ({"0902": "NO DATA"}, "vehicle"),
        ({"0902": "?"}, "vehicle"),
        ({"0902": "014\r0:490201575657\r2:57313233343536"}, "vehicle"),  # Frame fehlt
        ({"0902": "4902010000000000"}, "vehicle"),  # keine FIN
        ({"020200": "BUS BUSY"}, "freeze_frame"),
    ],
)
def test_single_part_failing_leaves_it_empty(change: dict[str, str], missing: str) -> None:
    session, _ = _run({**FULL_CAR, **change})
    for part in ("readiness", "freeze_frame", "vehicle"):
        assert (getattr(session, part) is None) == (part == missing), part
    assert len(session.scan.codes) == 4


def test_everything_optional_missing() -> None:
    responses = {
        **FULL_CAR,
        "0101": "NO DATA",
        "0902": "NO DATA",
        **dict.fromkeys(["020200", "020400", "020500", "020C00", "020D00"], "NO DATA"),
    }
    session, _ = _run(responses)
    assert (session.readiness, session.freeze_frame, session.vehicle) == (None, None, None)


def test_empty_freeze_frame_is_none() -> None:
    """Ohne gespeicherten Code: ``42 02 00 00 00`` und keine Werte."""
    responses = {**FULL_CAR, "020200": "4202000000", "020500": "7F0212", "020C00": "7F0212"}
    session, _ = _run(responses)
    assert session.freeze_frame is None


def test_freeze_frame_with_values_but_no_code_is_kept() -> None:
    session, _ = _run({**FULL_CAR, "020200": "4202000000"})
    assert session.freeze_frame is not None
    assert session.freeze_frame.dtc is None
    assert session.freeze_frame.values["rpm"] == 1726.0


def test_scan_failure_propagates() -> None:
    with pytest.raises(ElmError, match="UNABLE TO CONNECT"):
        _run({**FULL_CAR, "0100": "SEARCHING...\rUNABLE TO CONNECT"})


def test_transport_failure_in_part_propagates() -> None:
    class Dropping(FakeTransport):
        def write(self, data: bytes) -> None:
            if data.startswith(b"0902"):
                raise TransportError("Verbindung verloren")
            super().write(data)

    with pytest.raises(TransportError):
        run_diagnosis(Elm327(Dropping(FULL_CAR)), None)


def test_no_vpic_without_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any, **kwargs: Any) -> dict[str, str]:
        raise AssertionError("vPIC ohne Zustimmung abgefragt")

    monkeypatch.setattr(session_module, "lookup_vpic", refuse)
    monkeypatch.setattr(vehicle, "urlopen", refuse)
    session, _ = _run(FULL_CAR)
    assert session.vehicle is not None and session.vehicle.online == {}


def test_vpic_with_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_lookup(vin: str) -> dict[str, str]:
        calls.append(vin)
        return {"Model": "Golf"}

    monkeypatch.setattr(session_module, "lookup_vpic", fake_lookup)
    session, _ = _run(FULL_CAR, online_vin_lookup=True)
    assert calls == ["WVWZZZ1KZ6W123456"]
    assert session.vehicle is not None and session.vehicle.online == {"Model": "Golf"}


def test_vehicle_model_year_uses_protocol() -> None:
    # Stelle 10 „T“ = 2026 oder 1996; über CAN (ISO 15765-4) ist 1996 unplausibel
    vin_t = "014\r0:490201575657\r1:5A5A5A314B5A54\r2:57313233343536"
    session, _ = _run({**FULL_CAR, "0902": vin_t})
    assert session.vehicle is not None
    assert session.vehicle.vin == "WVWZZZ1KZTW123456"
    assert session.vehicle.model_year == 2026
    assert session.vehicle.model_year_alternatives == ()


def test_readiness_monitors_all_complete() -> None:
    session, _ = _run({**FULL_CAR, "0101": "4101 0007 6500"})
    assert session.readiness is not None
    assert session.readiness.all_complete
    assert {m.state for m in session.readiness.monitors} == {
        MonitorState.COMPLETE,
        MonitorState.NOT_SUPPORTED,
    }
