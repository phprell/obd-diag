"""Dialog „Batterie- und Anlasstest“: View-Model mit Zwischenständen, Abbruch und QML."""

import gc
import threading
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import pytest

pytest.importorskip("PySide6.QtQuick")

from PySide6.QtCore import QMetaObject, QObject
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from pytestqt.qtbot import QtBot

from obd_diag.protocol.elm327 import Elm327
from obd_diag.services.battery import (
    BatteryProgress,
    BatteryTestRefused,
    BatteryTestResult,
    Phase,
    run_battery_test,
)
from obd_diag.transport.demo import DemoTransport
from obd_diag.ui.backend import Backend
from obd_diag.ui.jobs import ThreadPoolRunner
from obd_diag.ui.viewmodels.battery import BatteryViewModel
from obd_diag.ui.viewmodels.diagnosis import DiagnosisViewModel
from obd_diag.ui.window import load_main_window, set_style
from tests.battery_fakes import SimClock
from tests.ui.conftest import FakeBackend, SyncRunner


def demo_test(
    port: str,
    baud: int,
    *,
    on_progress: Callable[[BatteryProgress], None],
    should_stop: Callable[[], bool],
) -> BatteryTestResult:
    """Der echte Test am Demo-Fahrzeug, mit simulierter Uhr."""
    clock = SimClock()
    with DemoTransport(engine_start=True, clock=clock) as transport:
        return run_battery_test(
            Elm327(transport),
            on_progress=on_progress,
            should_stop=should_stop,
            clock=clock,
            sleep=clock.sleep,
        )


def refused(port: str, baud: int, **kwargs: Any) -> BatteryTestResult:
    raise BatteryTestRefused("Der Motor läuft (860 1/min).")


def _backend(battery_test: Callable[..., BatteryTestResult]) -> Backend:
    return replace(FakeBackend().as_backend(), battery_test=battery_test)


def _vm(battery_test: Callable[..., BatteryTestResult], runner: Any = None) -> BatteryViewModel:
    runner = runner or SyncRunner()
    backend = _backend(battery_test)
    return BatteryViewModel(backend, runner, DiagnosisViewModel(backend, runner))


def test_result_rows(qapp: Any) -> None:
    vm = _vm(demo_test)
    vm.start("demo", 38400)
    assert vm.property("done") and not vm.property("running")
    assert vm.property("error") == ""
    rows = vm.property("rows")
    assert [r["rating"] for r in rows] == ["good", "good", "good"]
    assert [r["label"] for r in rows] == ["Ruhespannung", "Anlassen", "Ladespannung"]
    assert rows[1]["text"] == "10,2 V (Grenze 9,5 V): gut"
    assert vm.property("overallRating") == "good"
    assert vm.property("overallText") == "Batterie und Ladung in Ordnung"
    assert len(vm.property("notes")) == 2
    assert not vm._diagnosis.property("blocked")


def test_refusal_is_shown(qapp: Any) -> None:
    vm = _vm(refused)
    vm.start("demo", 38400)
    assert not vm.property("done") and not vm.property("running")
    assert vm.property("error") == "Der Motor läuft (860 1/min)."


def test_needs_a_port(qapp: Any) -> None:
    vm = _vm(demo_test)
    vm.start("  ", 38400)
    assert not vm.property("done")
    assert vm.property("error").startswith("Bitte einen Port angeben")


def test_not_while_diagnosis_is_blocked(qapp: Any) -> None:
    vm = _vm(demo_test)
    vm._diagnosis.set_blocked(True)  # Live-Daten laufen
    assert not vm.property("canStart")
    vm.start("demo", 38400)
    assert not vm.property("done")
    assert vm.property("error") == "Erst die laufende Abfrage beenden, dann den Test starten."


def test_progress_blocks_diagnosis_and_cancel(qapp: Any, qtbot: QtBot) -> None:
    release = threading.Event()

    def waiting(port: str, baud: int, *, on_progress: Any, should_stop: Any) -> BatteryTestResult:
        on_progress(BatteryProgress(Phase.START, 3.2, 12.55, 0.0, 29.6))
        while not should_stop():
            release.wait(0.01)
        return demo_test(port, baud, on_progress=on_progress, should_stop=should_stop)

    runner = ThreadPoolRunner()
    vm = _vm(waiting, runner)
    vm.start("demo", 38400)
    try:
        assert vm.property("running")
        assert vm._diagnosis.property("blocked")
        qtbot.waitUntil(lambda: vm.property("phase") == "start")
        assert vm.property("phaseText") == "Jetzt den Motor starten."
        assert vm.property("voltageText") == "12,6 V"
        assert vm.property("remainingText") == "noch 30 s"
    finally:
        vm.cancel()
    runner.wait()
    qtbot.waitUntil(lambda: not vm.property("running"))
    assert vm.property("error") == "Batterietest abgebrochen"
    assert vm.property("phaseText") == ""
    assert not vm._diagnosis.property("blocked")


# --- QML ------------------------------------------------------------------------------


def test_dialog_from_menu(qapp: Any, qtbot: QtBot) -> None:
    set_style()
    backend = _backend(demo_test)
    runner = SyncRunner()
    vm = DiagnosisViewModel(backend, runner)
    engine = QQmlApplicationEngine()
    battery = BatteryViewModel(backend, runner, vm, engine)
    load_main_window(engine, vm, battery=battery)
    root = engine.rootObjects()[0]
    assert isinstance(root, QQuickWindow)

    def find(name: str) -> QObject:
        obj = root.findChild(QObject, name)
        assert obj is not None, name
        return obj

    dialog = find("batteryDialog")
    QMetaObject.invokeMethod(find("batteryMenuItem"), "triggered")
    qtbot.waitUntil(lambda: dialog.property("opened") is True)
    assert find("batteryInstructions").property("visible")
    QMetaObject.invokeMethod(find("batteryStart"), "clicked")
    qtbot.waitUntil(lambda: battery.property("done") is True)
    assert find("batteryOverall").property("text") == "Batterie und Ladung in Ordnung"
    assert find("batteryStart").property("text") == "Erneut testen"
    del engine
    gc.collect()
