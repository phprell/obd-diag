"""View-Model „Batterie- und Anlasstest“: verspätete Rückmeldungen, Fehlermeldungen,
Anzeige je Phase und Englisch."""

from collections.abc import Callable
from dataclasses import replace
from typing import Any

import pytest

pytest.importorskip("PySide6.QtQuick")

from PySide6.QtCore import QCoreApplication

from obd_diag import i18n
from obd_diag.protocol.elm327 import ElmError, NoConnectionError
from obd_diag.services.battery import (
    BatteryProgress,
    BatteryTestCancelled,
    BatteryTestResult,
    Phase,
)
from obd_diag.transport import TransportError
from obd_diag.ui.viewmodels.battery import BatteryViewModel
from obd_diag.ui.viewmodels.diagnosis import DiagnosisViewModel
from tests.ui.conftest import FakeBackend, SyncRunner

RESULT = BatteryTestResult(
    "ELM327 v1.5",
    12.0,
    12.6,
    0.6,
    20.0,
    True,
    9.7,
    9.1,
    0.7,
    True,
    14.1,
    14.2,
    False,
    ((0.0, 12.6), (3.5, 9.7)),
)


class DeferredRunner:
    """Merkt sich Jobs, ohne sie auszuführen; Tests lösen die Rückmeldungen selbst aus."""

    def __init__(self) -> None:
        self.jobs: list[
            tuple[Callable[[], Any], Callable[[Any], None], Callable[[Exception], None]]
        ] = []

    def run(
        self,
        job: Callable[[], Any],
        on_success: Callable[[Any], None],
        on_error: Callable[[Exception], None],
    ) -> None:
        self.jobs.append((job, on_success, on_error))


def _vm(battery_test: Callable[..., BatteryTestResult], runner: Any = None) -> BatteryViewModel:
    runner = runner or SyncRunner()
    backend = replace(FakeBackend().as_backend(), battery_test=battery_test)
    return BatteryViewModel(backend, runner, DiagnosisViewModel(backend, runner))


def _returns(result: BatteryTestResult) -> Callable[..., BatteryTestResult]:
    def run(port: str, baud: int, **kwargs: Any) -> BatteryTestResult:
        return result

    return run


def _raises(error: Exception) -> Callable[..., BatteryTestResult]:
    def run(port: str, baud: int, **kwargs: Any) -> BatteryTestResult:
        raise error

    return run


def test_port_is_stripped_and_baud_passed(qapp: Any) -> None:
    calls: list[tuple[str, int]] = []

    def record(port: str, baud: int, **kwargs: Any) -> BatteryTestResult:
        calls.append((port, baud))
        assert set(kwargs) == {"on_progress", "should_stop"}
        assert kwargs["should_stop"]() is False
        return RESULT

    vm = _vm(record)
    vm.start("  /dev/ttyUSB0 ", 115200)
    assert calls == [("/dev/ttyUSB0", 115200)]
    assert vm.result is RESULT


def test_result_property_none_before_and_after_failure(qapp: Any) -> None:
    vm = _vm(_raises(TransportError("weg")))
    assert vm.result is None
    assert vm.property("rows") == []
    assert vm.property("overallRating") == ""
    assert vm.property("overallText") == ""
    assert vm.property("notes") == []
    vm.start("/dev/ttyUSB0", 38400)
    assert vm.result is None


def test_rows_with_weak_crank(qapp: Any) -> None:
    vm = _vm(_returns(RESULT))
    vm.start("/dev/ttyUSB0", 38400)
    rows = vm.property("rows")
    assert [r["rating"] for r in rows] == ["good", "weak", "good"]
    assert rows[1]["text"] == "9,7 V (Grenze 9,5 V): knapp, Batterie altert"
    assert vm.property("overallRating") == "weak"
    assert vm.property("overallText") == "Auffällig: Einzelheiten beachten"


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (TransportError("Port weg"), "Verbindung fehlgeschlagen: Port weg"),
        (ElmError("ATRV: BUS BUSY"), "Der Adapter meldet einen Fehler: ATRV: BUS BUSY"),
        (ValueError("kaputt"), "Unerwartete Antwort vom Fahrzeug: kaputt"),
        (BatteryTestCancelled("Batterietest abgebrochen"), "Batterietest abgebrochen"),
    ],
)
def test_error_messages(qapp: Any, error: Exception, message: str) -> None:
    vm = _vm(_raises(error))
    vm.start("/dev/ttyUSB0", 38400)
    assert vm.property("error") == message
    assert not vm.property("running") and not vm.property("done")
    assert not vm._diagnosis.property("blocked")


def test_no_connection_message_has_hint(qapp: Any) -> None:
    vm = _vm(_raises(NoConnectionError("0100: UNABLE TO CONNECT")))
    vm.start("/dev/ttyUSB0", 38400)
    assert vm.property("error").endswith("(Adapter: 0100: UNABLE TO CONNECT)")


def test_error_is_cleared_on_next_start(qapp: Any) -> None:
    vm = _vm(_returns(RESULT))
    vm.start("", 38400)
    assert vm.property("error")
    vm.start("/dev/ttyUSB0", 38400)
    assert vm.property("error") == ""
    assert vm.property("done")


def test_start_while_running_is_ignored(qapp: Any) -> None:
    runner = DeferredRunner()
    vm = _vm(_returns(RESULT), runner)
    vm.start("/dev/ttyUSB0", 38400)
    vm.start("/dev/ttyUSB1", 38400)
    assert len(runner.jobs) == 1
    assert vm.property("running") and not vm.property("canStart")
    assert vm.property("error") == ""


def test_cancel_sets_the_stop_event_of_the_job(qapp: Any) -> None:
    seen: list[Callable[[], bool]] = []

    def capture(port: str, baud: int, *, on_progress: Any, should_stop: Any) -> BatteryTestResult:
        seen.append(should_stop)
        return RESULT

    runner = DeferredRunner()
    vm = _vm(capture, runner)
    vm.cancel()  # nichts läuft: kein Effekt
    vm.start("/dev/ttyUSB0", 38400)
    job, on_success, _on_error = runner.jobs[0]
    vm.cancel()
    on_success(job())
    assert seen[0]() is True


def test_late_callbacks_of_an_old_run_are_ignored(qapp: Any) -> None:
    runner = DeferredRunner()
    vm = _vm(_returns(RESULT), runner)
    vm.start("/dev/ttyUSB0", 38400)
    _job1, done1, failed1 = runner.jobs[0]
    done1(RESULT)
    assert vm.property("done")
    vm.start("/dev/ttyUSB0", 38400)  # zweiter Lauf
    assert vm.property("running") and not vm.property("done")
    failed1(TransportError("alt"))
    done1(RESULT)
    vm._relay.progress.emit(1, BatteryProgress(Phase.START, 3.0, 12.6, 0.0, 29.0))
    QCoreApplication.processEvents()
    assert vm.property("running")
    assert vm.property("error") == ""
    assert not vm.property("done")
    assert vm.property("phase") == ""
    assert vm._diagnosis.property("blocked")
    vm._relay.progress.emit(2, BatteryProgress(Phase.REST, 1.0, 12.6, 0.0, 2.0))
    QCoreApplication.processEvents()
    assert vm.property("phase") == "rest"
    _job2, done2, _failed2 = runner.jobs[1]
    done2(RESULT)
    assert vm.property("done") and not vm._diagnosis.property("blocked")


def test_progress_after_finish_is_ignored(qapp: Any) -> None:
    runner = DeferredRunner()
    vm = _vm(_returns(RESULT), runner)
    vm.start("/dev/ttyUSB0", 38400)
    runner.jobs[0][1](RESULT)
    vm._relay.progress.emit(1, BatteryProgress(Phase.RUNNING, 20.0, 14.4, 800.0, 1.0))
    QCoreApplication.processEvents()
    assert vm.property("phaseText") == ""
    assert vm.property("voltageText") == ""
    assert vm.property("remainingText") == ""


@pytest.mark.parametrize(
    ("progress", "voltage", "remaining"),
    [
        (BatteryProgress(Phase.PREPARE, 0.0, None, None, None), "", ""),
        (BatteryProgress(Phase.REST, 0.5, 12.55, 0.0, 2.5), "12,6 V", "noch 2 s"),
        (BatteryProgress(Phase.START, 3.1, 12.8, 0.0, 29.6), "12,8 V", "noch 30 s"),
        # beim Anlassen kein Countdown, obwohl die Frist der Startphase weiterläuft
        (BatteryProgress(Phase.CRANKING, 5.0, 9.71, 200.0, 26.0), "9,7 V", ""),
        (BatteryProgress(Phase.RUNNING, 8.0, 14.4, 800.0, 0.0), "14,4 V", "noch 0 s"),
    ],
)
def test_progress_texts_per_phase(
    qapp: Any, progress: BatteryProgress, voltage: str, remaining: str
) -> None:
    runner = DeferredRunner()
    vm = _vm(_returns(RESULT), runner)
    vm.start("/dev/ttyUSB0", 38400)
    vm._relay.progress.emit(1, progress)
    QCoreApplication.processEvents()
    assert vm.property("phase") == progress.phase.value
    assert vm.property("voltageText") == voltage
    assert vm.property("remainingText") == remaining


def test_english_after_retranslate(qapp: Any) -> None:
    runner = DeferredRunner()
    vm = _vm(_returns(RESULT), runner)
    vm.start("/dev/ttyUSB0", 38400)
    vm._relay.progress.emit(1, BatteryProgress(Phase.START, 3.1, 12.8, 0.0, 29.6))
    QCoreApplication.processEvents()
    i18n.set_language("en")  # ``_german`` (autouse) stellt danach wieder Deutsch ein
    changed: list[str] = []
    vm.stateChanged.connect(lambda: changed.append("state"))
    vm.progressChanged.connect(lambda: changed.append("progress"))
    vm.retranslate()
    assert changed == ["state", "progress"]
    assert vm.property("phaseText") == "Start the engine now."
    assert vm.property("voltageText") == "12.8 V"
    assert vm.property("remainingText") == "30 s left"
    assert vm.property("instructions").startswith("Vehicle")
    runner.jobs[0][1](RESULT)
    rows = vm.property("rows")
    assert [r["label"] for r in rows] == ["Resting voltage", "Cranking", "Charging voltage"]
    assert rows[1]["text"] == "9.7 V (limit 9.5 V): marginal, battery is ageing"
    assert vm.property("overallText") == "Noticeable: see details"
    assert vm.property("notes")[0] == (
        "Adapter reading corrected by 0.6 V (difference to the control unit at rest)."
    )


def test_english_errors(qapp: Any) -> None:
    i18n.set_language("en")
    vm = _vm(_raises(TransportError("gone")))
    vm.start("", 38400)
    assert vm.property("error") == "Please specify a port, e.g. /dev/ttyUSB0."
    vm.start("/dev/ttyUSB0", 38400)
    assert vm.property("error") == "Connection failed: gone"
