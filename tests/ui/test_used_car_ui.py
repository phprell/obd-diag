"""Gebrauchtwagen-Check in der Oberfläche: Werte für den Reiter (``used_car_entry``) und
das View-Model nach Scan bzw. geöffneter Sitzung."""

import dataclasses
import json
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtCore")

from obd_diag.i18n import set_language
from obd_diag.services.diagnostics import DiagnosticCode, DtcKind
from obd_diag.services.readiness import decode_readiness
from obd_diag.services.session import save_session, session_to_dict
from obd_diag.services.used_car import ClearCounters
from obd_diag.ui.viewmodels.diagnosis import DiagnosisViewModel
from obd_diag.ui.viewmodels.session_parts import used_car_entry
from tests.samples import full_session, minimal_session
from tests.ui.conftest import FakeBackend, SyncRunner

OLD = ClearCounters(warmups=40, distance_km=3000, time_min=600, mil_distance_km=0, mil_time_min=0)
READY = decode_readiness(bytes.fromhex("00076500"))


def test_entry_without_session() -> None:
    assert used_car_entry(None) == {"available": False}


def test_entry_full_session() -> None:
    entry = used_car_entry(full_session())
    assert entry["available"] is True
    assert entry["verdict"] == "suspicious"
    assert entry["verdictLabel"] == "Auffällig"
    assert entry["recentlyCleared"] is True
    assert entry["countersRead"] is True
    assert entry["counters"] == [
        {"label": "Warmläufe seit dem Löschen", "value": "3"},
        {"label": "Strecke seit dem Löschen", "value": "45 km"},
        {"label": "Zeit seit dem Löschen", "value": "80 Minuten"},
        {"label": "Strecke mit Warnleuchte an", "value": "12 km"},
        {"label": "Zeit mit Warnleuchte an", "value": "20 Minuten"},
    ]
    assert [f["level"] for f in entry["findings"]] == ["warning", "info", "warning", "warning"]
    assert entry["findings"][0]["text"] == (
        "Fehlercodes vor kurzem gelöscht: erst 3 Warmläufe, 45 km, 80 Minuten seit dem Löschen."
    )
    assert entry["note"].startswith("Faustregel, kein Beweis")


def test_entry_inconspicuous() -> None:
    session = dataclasses.replace(minimal_session(), readiness=READY, clear_counters=OLD)
    entry = used_car_entry(session)
    assert entry["verdict"] == "inconspicuous"
    assert entry["verdictLabel"] == "Unauffällig"
    assert entry["recentlyCleared"] is False
    assert [f["level"] for f in entry["findings"]] == ["ok", "ok", "ok"]
    assert entry["counters"][2] == {"label": "Zeit seit dem Löschen", "value": "10 Stunden"}


def test_entry_read_but_nothing_reported() -> None:
    """Gelesen, aber nichts gemeldet: Zeilen „nicht gemeldet“, anders als alte Sitzungen."""
    session = dataclasses.replace(minimal_session(), clear_counters=ClearCounters())
    entry = used_car_entry(session)
    assert entry["verdict"] == "unknown"
    assert entry["countersRead"] is True
    assert {c["value"] for c in entry["counters"]} == {"nicht gemeldet"}
    assert len(entry["counters"]) == 5


def test_entry_old_session_with_permanent_code() -> None:
    scan = dataclasses.replace(
        minimal_session().scan, codes=[DiagnosticCode("P0420", DtcKind.PERMANENT)]
    )
    session = dataclasses.replace(minimal_session(), scan=scan)
    entry = used_car_entry(session)
    assert entry["countersRead"] is False
    assert entry["counters"] == []
    assert entry["verdict"] == "suspicious"
    assert entry["findings"][-1]["text"].startswith("Permanenter Fehlercode: P0420.")


def test_entry_english() -> None:
    set_language("en")
    entry = used_car_entry(full_session())
    assert entry["verdictLabel"] == "Suspicious"
    assert entry["counters"][1] == {"label": "Distance since codes cleared", "value": "45 km"}
    assert entry["findings"][0]["text"] == (
        "Trouble codes cleared recently: only 3 warm-ups, 45 km, 80 minutes since clearing."
    )
    assert entry["note"].startswith("Rule of thumb, not proof")


def test_viewmodel_after_scan() -> None:
    vm = DiagnosisViewModel(FakeBackend(full_session()).as_backend(), SyncRunner())
    assert vm.property("usedCar") == {"available": False}
    vm.connectAndScan("/dev/ttyUSB0", 38400)
    assert vm.property("usedCar") == used_car_entry(full_session())


def test_viewmodel_opened_old_session_file(tmp_path: Path) -> None:
    """Sitzungsdatei einer älteren Version ohne ``clear_counters``."""
    path = save_session(full_session(), tmp_path)
    data = session_to_dict(full_session())
    del data["clear_counters"]
    path.write_text(json.dumps(data), encoding="utf-8")
    vm = DiagnosisViewModel(FakeBackend().as_backend(), SyncRunner())
    vm.openSession(str(path))
    entry = vm.property("usedCar")
    assert entry["available"] is True
    assert entry["countersRead"] is False
    assert entry["counters"] == []
    # Warnleuchte an (Readiness) und permanenter Code P0420 bleiben Befunde
    assert entry["verdict"] == "suspicious"
    assert entry["findings"][0]["level"] == "info"
