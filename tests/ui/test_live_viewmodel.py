"""Reiter „Live-Daten“: View-Model und QML, mit einer Fake-Live-Funktion statt Adapter."""

import gc
import math
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("PySide6.QtQuick")

from PySide6.QtCore import QObject, QSettings
from PySide6.QtQml import QQmlApplicationEngine, QQmlError
from PySide6.QtQuick import QQuickItem, QQuickWindow
from pytestqt.qtbot import QtBot

from obd_diag.protocol.pids import PidSpec
from obd_diag.services.live import DEFAULT_KEYS, LiveSample, LiveSetup, SelectionError
from obd_diag.transport import TransportError
from obd_diag.ui.backend import Backend, LiveResult
from obd_diag.ui.jobs import ThreadPoolRunner
from obd_diag.ui.viewmodels.diagnosis import SETTINGS_APP, SETTINGS_ORG, DiagnosisViewModel
from obd_diag.ui.viewmodels.live import (
    HISTORY,
    NO_VALUE,
    RECORD_KEY,
    LiveViewModel,
    elapsed_text,
    number_text,
)
from obd_diag.ui.window import load_main_window, set_style
from tests.ui.conftest import FakeBackend, SyncRunner


def _unused(data: bytes) -> float:
    raise AssertionError("die Fake-Live-Funktion dekodiert nichts")


COOLANT = PidSpec(0x05, "coolant_temp", "Kühlmitteltemperatur", "°C", 1, _unused, -40, 215)
RPM = PidSpec(0x0C, "rpm", "Motordrehzahl", "1/min", 2, _unused, 0, 16383.75)
SPEED = PidSpec(0x0D, "speed", "Geschwindigkeit", "km/h", 1, _unused, 0, 255)
MAF = PidSpec(0x10, "maf", "Luftmasse", "g/s", 2, _unused, 0, 655.35)
SETUP = LiveSetup("ELM327 v1.5", "ISO 15765-4 (CAN 11/500)", [COOLANT, RPM, SPEED, MAF])


def sample(i: int, rpm: float | None = 850.0, voltage: float | None = 12.4) -> LiveSample:
    return LiveSample(
        elapsed=float(i),
        values={"coolant_temp": 80.0 + i, "rpm": rpm, "speed": 0.0},
        voltage=voltage,
        throttled=voltage is not None and voltage < 11.8,
    )


class FakeLive:
    """Ersetzt ``live_port``: meldet ``SETUP``, liefert ``samples`` und wartet ggf. auf Stopp."""

    def __init__(
        self,
        samples: Sequence[LiveSample] = (),
        *,
        until_stopped: bool = False,
        error: Exception | None = None,
        select_error: ValueError | None = None,
        recording: Path = Path("/tmp/live-20261007-143200.csv"),
    ) -> None:
        self.samples = list(samples)
        self.until_stopped = until_stopped
        self.error = error
        self.select_error = select_error
        self.recording = recording
        self.calls: list[tuple[str, int, list[str] | None, float, bool]] = []
        self.skip_unsupported: list[bool] = []
        self.threads: list[int] = []
        self.delivered = threading.Event()

    def __call__(
        self,
        port: str,
        baud: int,
        keys: Sequence[str] | None,
        interval: float,
        record: bool,
        *,
        on_setup: Callable[[LiveSetup], None],
        on_start: Callable[[list[PidSpec], Path | None], None],
        on_sample: Callable[[LiveSample], None],
        should_stop: Callable[[], bool],
        skip_unsupported: bool = False,
    ) -> LiveResult:
        self.calls.append((port, baud, None if keys is None else list(keys), interval, record))
        self.skip_unsupported.append(skip_unsupported)
        self.threads.append(threading.get_ident())
        on_setup(SETUP)
        if self.select_error is not None:
            raise self.select_error
        wanted = DEFAULT_KEYS if keys is None else keys
        pids = [p for p in SETUP.available if p.key in wanted]
        path = self.recording if record else None
        on_start(pids, path)
        count = 0
        for s in self.samples:
            if should_stop():
                break
            on_sample(s)
            count += 1
        self.delivered.set()
        if self.error is not None:
            raise self.error
        while self.until_stopped and not should_stop():
            time.sleep(0.01)
        return LiveResult(count, path)


class HoldingRunner:
    """Hält Jobs fest, bis ``finish`` sie ausführt: so bleibt ein Job „beschäftigt“."""

    def __init__(self) -> None:
        self.jobs: list[
            tuple[Callable[[], Any], Callable[[Any], None], Callable[[Exception], None]]
        ]
        self.jobs = []

    def run(
        self,
        job: Callable[[], Any],
        on_success: Callable[[Any], None],
        on_error: Callable[[Exception], None],
    ) -> None:
        self.jobs.append((job, on_success, on_error))

    def finish(self) -> None:
        job, ok, err = self.jobs.pop(0)
        try:
            result = job()
        except Exception as e:
            err(e)
        else:
            ok(result)


def _backend(fake_backend: FakeBackend, live: FakeLive) -> Backend:
    return replace(fake_backend.as_backend(), live=live)


def _vms(
    fake_backend: FakeBackend, live: FakeLive, runner: Any = None
) -> tuple[DiagnosisViewModel, LiveViewModel]:
    runner = SyncRunner() if runner is None else runner
    diagnosis = DiagnosisViewModel(_backend(fake_backend, live), runner)
    return diagnosis, LiveViewModel(diagnosis.backend, runner, diagnosis)


def _entry(vm: LiveViewModel, key: str) -> dict[str, Any]:
    entry = vm.value_model.entry(key)
    assert entry is not None, key
    return dict(entry)


# --- Hilfsfunktionen ---


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (None, NO_VALUE),
        (math.nan, NO_VALUE),
        (850.0, "850"),
        (-40.0, "-40"),
        (16383.75, "16384"),
        (55.5, "55,5"),
        (14.123, "14,1"),
        (0.784, "0,78"),
    ],
)
def test_number_text(value: float | None, text: str) -> None:
    assert number_text(value) == text


def test_elapsed_text() -> None:
    assert elapsed_text(0) == "0:00"
    assert elapsed_text(75.9) == "1:15"
    assert elapsed_text(3725) == "1:02:05"


# --- Ablauf ---


def test_start_delivers_values_from_worker_and_stops(
    qtbot: QtBot, fake_backend: FakeBackend
) -> None:
    live = FakeLive([sample(0), sample(1, rpm=900.0), sample(2, rpm=875.5)], until_stopped=True)
    runner = ThreadPoolRunner()
    diagnosis, vm = _vms(fake_backend, live, runner)
    assert vm.property("canStart") is True
    vm.start(" /dev/pts/5 ", 9600)
    assert vm.property("running") is True
    assert vm.property("canStart") is False
    assert diagnosis.property("blocked") is True
    qtbot.waitUntil(lambda: vm.property("sampleCount") == 3, timeout=5000)

    assert live.calls == [("/dev/pts/5", 9600, None, 1.0, False)]
    assert live.threads[0] != threading.get_ident()
    assert vm.value_model.keys == ["coolant_temp", "rpm", "speed"]
    rpm = _entry(vm, "rpm")
    assert rpm["name"] == "Motordrehzahl" and rpm["unit"] == "1/min"
    assert rpm["valueText"] == "876"  # ab 100 ohne Nachkommastellen
    assert (rpm["lowText"], rpm["highText"]) == ("850", "900")
    assert rpm["history"] == [850.0, 900.0, 875.5]
    assert (rpm["minimum"], rpm["maximum"]) == (0, 16383.75)
    assert vm.property("voltageText") == "12,4 V"
    assert vm.property("throttled") is False
    assert vm.property("elapsedText") == "0:02"
    assert vm.property("connectionText") == "ELM327 v1.5 · ISO 15765-4 (CAN 11/500)"

    with qtbot.waitSignal(vm.liveFinished, timeout=5000):
        vm.stop()
        assert vm.property("stopping") is True
    runner.wait()
    assert vm.property("running") is False
    assert vm.property("stopping") is False
    assert diagnosis.property("blocked") is False
    assert vm.property("notice") == "Live-Daten beendet nach 3 Runden."
    assert vm.property("recordingPath") == ""
    # Werte bleiben nach dem Stopp sichtbar
    assert _entry(vm, "rpm")["valueText"] == "876"


def test_history_is_limited(qtbot: QtBot, fake_backend: FakeBackend) -> None:
    live = FakeLive([sample(i, rpm=float(i)) for i in range(HISTORY + 30)])
    _, vm = _vms(fake_backend, live)
    vm.start("/dev/ttyUSB0", 38400)
    qtbot.waitUntil(lambda: vm.property("sampleCount") == HISTORY + 30)
    history = _entry(vm, "rpm")["history"]
    assert len(history) == HISTORY == vm.property("historyLength")
    assert history[0] == 30.0 and history[-1] == HISTORY + 29
    # kleinster/größter Wert über die ganze Abfrage, nicht nur den Verlauf
    assert _entry(vm, "rpm")["lowText"] == "0"


def test_unreadable_value_and_low_voltage(qtbot: QtBot, fake_backend: FakeBackend) -> None:
    live = FakeLive([sample(0), sample(1, rpm=None, voltage=11.2)])
    _, vm = _vms(fake_backend, live)
    vm.start("/dev/ttyUSB0", 38400)
    qtbot.waitUntil(lambda: vm.property("sampleCount") == 2)
    rpm = _entry(vm, "rpm")
    assert rpm["hasValue"] is False
    assert rpm["valueText"] == NO_VALUE
    assert rpm["history"][0] == 850.0 and math.isnan(rpm["history"][1])
    assert (rpm["lowText"], rpm["highText"]) == ("850", "850")
    assert vm.property("voltageText") == "11,2 V"
    assert vm.property("throttled") is True


def test_transport_error_is_shown_and_unblocks(qtbot: QtBot, fake_backend: FakeBackend) -> None:
    live = FakeLive([sample(0)], error=TransportError("Port /dev/ttyUSB0 weg"))
    diagnosis, vm = _vms(fake_backend, live)
    with qtbot.waitSignal(vm.jobFailed):
        vm.start("/dev/ttyUSB0", 38400)
    assert vm.property("errorMessage") == "Verbindung fehlgeschlagen: Port /dev/ttyUSB0 weg"
    assert vm.property("running") is False
    assert diagnosis.property("blocked") is False
    vm.dismissError()
    assert vm.property("errorMessage") == ""


def test_selection_error_is_named(qtbot: QtBot, fake_backend: FakeBackend) -> None:
    live = FakeLive(select_error=SelectionError("nicht unterstützt: Luftmasse"))
    _, vm = _vms(fake_backend, live)
    vm.start("/dev/ttyUSB0", 38400)
    assert vm.property("errorMessage") == "Auswahl nicht möglich: nicht unterstützt: Luftmasse"


def test_broken_answer_is_not_called_a_selection_error(
    qtbot: QtBot, fake_backend: FakeBackend
) -> None:
    # Ein ValueError aus der Initialisierung (z. B. kaputte Frames) liegt nicht an der Auswahl
    live = FakeLive(select_error=ValueError("Frame 2 fehlt"))
    _, vm = _vms(fake_backend, live)
    vm.start("/dev/ttyUSB0", 38400)
    assert vm.property("errorMessage") == "Unerwartete Antwort vom Fahrzeug: Frame 2 fehlt"


def test_selection_before_first_connection_skips_unsupported(
    qtbot: QtBot, fake_backend: FakeBackend
) -> None:
    # Vor der ersten Verbindung zeigt die Liste auch Werte, die das Fahrzeug nicht kann
    live = FakeLive([sample(0)])
    _, vm = _vms(fake_backend, live)
    vm.setSelected("speed", False)
    vm.start("/dev/ttyUSB0", 38400)
    qtbot.waitUntil(lambda: vm.property("supportedKnown") is True)
    qtbot.waitUntil(lambda: vm.property("running") is False)
    assert live.skip_unsupported == [True]
    vm.start("/dev/ttyUSB0", 38400)
    assert live.skip_unsupported == [True, False]


def test_missing_port(fake_backend: FakeBackend) -> None:
    live = FakeLive()
    _, vm = _vms(fake_backend, live)
    vm.start("  ", 38400)
    assert "Port" in vm.property("errorMessage")
    assert live.calls == []
    assert vm.property("running") is False


def test_old_run_results_are_ignored(qtbot: QtBot, fake_backend: FakeBackend) -> None:
    live = FakeLive([sample(0)])
    _, vm = _vms(fake_backend, live)
    vm.start("/dev/ttyUSB0", 38400)
    qtbot.waitUntil(lambda: vm.property("sampleCount") == 1)
    vm._on_sample(vm._run_id - 1, sample(5, rpm=1.0))
    assert vm.property("sampleCount") == 1
    assert _entry(vm, "rpm")["valueText"] == "850"


# --- Auswahl, Intervall, Aufzeichnung ---


def test_selection_and_supported_values(qtbot: QtBot, fake_backend: FakeBackend) -> None:
    live = FakeLive([sample(0)])
    _, vm = _vms(fake_backend, live)
    assert vm.property("supportedKnown") is False
    assert vm.property("selectedKeys") == list(DEFAULT_KEYS)
    vm.start("/dev/ttyUSB0", 38400)
    qtbot.waitUntil(lambda: vm.property("supportedKnown") is True)
    assert [a["key"] for a in vm.property("available")] == ["coolant_temp", "rpm", "speed", "maf"]
    assert vm.property("available")[1] == {"key": "rpm", "name": "Motordrehzahl", "unit": "1/min"}
    # Übliche Werte, soweit unterstützt; ohne eigene Auswahl entscheidet select_pids
    assert vm.property("selectedKeys") == ["rpm", "speed", "coolant_temp"]
    assert live.calls[-1][2] is None

    vm.setSelected("maf", True)
    vm.setSelected("speed", False)
    vm.setSelected("speed", False)  # doppelt: keine Änderung
    assert vm.property("selectedKeys") == ["rpm", "coolant_temp", "maf"]
    vm.start("/dev/ttyUSB0", 38400)
    # in der Reihenfolge der Liste (nach PID)
    assert live.calls[-1][2] == ["coolant_temp", "rpm", "maf"]
    qtbot.waitUntil(lambda: vm.value_model.keys == ["coolant_temp", "rpm", "maf"])

    vm.resetSelection()
    assert vm.property("selectedKeys") == ["rpm", "speed", "coolant_temp"]
    vm.start("/dev/ttyUSB0", 38400)
    assert live.calls[-1][2] is None


def test_empty_selection_is_refused(fake_backend: FakeBackend) -> None:
    live = FakeLive()
    _, vm = _vms(fake_backend, live)
    for key in DEFAULT_KEYS:
        vm.setSelected(key, False)
    vm.start("/dev/ttyUSB0", 38400)
    assert vm.property("errorMessage") == "Bitte mindestens einen Wert auswählen."
    assert live.calls == []


def test_interval(fake_backend: FakeBackend) -> None:
    live = FakeLive()
    _, vm = _vms(fake_backend, live)
    assert vm.property("intervals") == [0.5, 1.0, 2.0]
    vm.setProperty("interval", 0.7)  # nicht in der Auswahl
    assert vm.property("interval") == 1.0
    vm.setProperty("interval", 0.5)
    vm.start("/dev/ttyUSB0", 38400)
    assert live.calls[-1][3] == 0.5
    # gemerkt für den nächsten Start der Oberfläche
    _, again = _vms(fake_backend, live)
    assert again.property("interval") == 0.5


def test_recording_on_and_off(qtbot: QtBot, fake_backend: FakeBackend) -> None:
    live = FakeLive([sample(0), sample(1)], until_stopped=True)
    runner = ThreadPoolRunner()
    _, vm = _vms(fake_backend, live, runner)
    assert vm.property("recording") is False
    vm.setProperty("recording", True)
    assert QSettings(SETTINGS_ORG, SETTINGS_APP).value(RECORD_KEY, type=bool) is True

    vm.start("/dev/ttyUSB0", 38400)
    qtbot.waitUntil(lambda: vm.property("recordingPath") == str(live.recording), timeout=5000)
    assert live.calls[-1][4] is True
    vm.setProperty("recording", False)  # während der Abfrage nicht umschaltbar
    assert vm.property("recording") is True
    with qtbot.waitSignal(vm.liveFinished, timeout=5000) as finished:
        vm.stop()
    runner.wait()
    assert finished.args == [str(live.recording)]
    assert vm.property("notice").endswith(f"Aufzeichnung gespeichert: {live.recording}")

    vm.setProperty("recording", False)
    assert vm.property("recording") is False
    live.delivered.clear()
    with qtbot.waitSignal(vm.liveFinished, timeout=5000):
        vm.start("/dev/ttyUSB0", 38400)
        qtbot.waitUntil(live.delivered.is_set, timeout=5000)
        vm.stop()
    runner.wait()
    assert live.calls[-1][4] is False
    assert vm.property("recordingPath") == ""
    assert "Aufzeichnung" not in vm.property("notice")


# --- Gegenseitige Sperre mit der Diagnose ---


def test_live_does_not_start_while_diagnosis_is_busy(fake_backend: FakeBackend) -> None:
    live = FakeLive([sample(0)])
    runner = HoldingRunner()
    diagnosis, vm = _vms(fake_backend, live, runner)
    diagnosis.connectAndScan("/dev/ttyUSB0", 38400)
    assert diagnosis.property("busy") is True
    assert vm.property("canStart") is False
    vm.start("/dev/ttyUSB0", 38400)
    assert len(runner.jobs) == 1 and vm.property("running") is False
    runner.finish()
    assert vm.property("canStart") is True
    vm.start("/dev/ttyUSB0", 38400)
    assert vm.property("running") is True


def test_diagnosis_is_blocked_while_live_runs(fake_backend: FakeBackend) -> None:
    live = FakeLive([sample(0)])
    runner = HoldingRunner()
    diagnosis, vm = _vms(fake_backend, live, runner)
    diagnosis.connectAndScan("/dev/ttyUSB0", 38400)
    runner.finish()
    assert diagnosis.property("canClear") and diagnosis.property("canExport")

    vm.start("/dev/ttyUSB0", 38400)
    assert diagnosis.property("blocked") is True
    assert not diagnosis.property("canClear")
    assert not diagnosis.property("canExport")
    diagnosis.connectAndScan("/dev/ttyUSB0", 38400)
    diagnosis.clearCodes()
    diagnosis.exportCsv("/tmp/nie.csv")
    vm.start("/dev/ttyUSB0", 38400)  # zweiter Start: ignoriert
    assert len(runner.jobs) == 1  # nur die Live-Abfrage
    assert [c[0] for c in fake_backend.calls] == ["diagnose"]

    runner.finish()
    assert diagnosis.property("blocked") is False
    assert diagnosis.property("canClear") is True


# --- QML ---


class LiveUi:
    def __init__(self, engine: QQmlApplicationEngine, live: LiveViewModel) -> None:
        self.engine = engine
        self.live = live
        self.warnings: list[str] = []
        root = engine.rootObjects()[0]
        assert isinstance(root, QQuickWindow)
        self.window = root

    def find(self, name: str) -> QObject:
        obj = self.window.findChild(QObject, name) or _find_item(self.window.contentItem(), name)
        assert obj is not None, name
        return obj

    def prop(self, name: str, prop: str) -> Any:
        return self.find(name).property(prop)


def _find_item(item: QQuickItem, name: str) -> QQuickItem | None:
    """Von einem Repeater erzeugte Elemente hängen nur im Baum der sichtbaren Elemente."""
    for child in item.childItems():
        if child.objectName() == name:
            return child
        found = _find_item(child, name)
        if found is not None:
            return found
    return None


@pytest.fixture
def live_fake() -> FakeLive:
    return FakeLive([sample(0), sample(1, rpm=900.0)], until_stopped=True)


@pytest.fixture
def live_ui(qapp: Any, fake_backend: FakeBackend, live_fake: FakeLive) -> Iterator[LiveUi]:
    set_style()
    runner = ThreadPoolRunner()
    diagnosis, live = _vms(fake_backend, live_fake, runner)
    engine = QQmlApplicationEngine()
    warnings: list[str] = []

    def collect(errors: list[QQmlError]) -> None:
        warnings.extend(e.toString() for e in errors)

    engine.warnings.connect(collect)
    load_main_window(engine, diagnosis, live=live)
    ui = LiveUi(engine, live)
    ui.warnings = warnings
    yield ui
    live.stop()
    runner.wait()
    engine.warnings.disconnect(collect)
    del ui.window, ui.engine, engine
    gc.collect()


def test_live_tab_runs_and_locks_diagnosis(qtbot: QtBot, live_ui: LiveUi) -> None:
    live_ui.find("viewTabs").setProperty("currentIndex", 4)
    qtbot.waitUntil(lambda: live_ui.prop("liveEmpty", "visible") is True)
    assert live_ui.prop("liveEmpty", "title") == "Noch keine Live-Daten"
    assert "Beifahrer" in live_ui.prop("liveSafetyBanner", "text")
    assert live_ui.prop("liveSafetyBanner", "visible") is True
    assert live_ui.prop("liveSelection", "visible") is True
    assert live_ui.prop("liveStartButton", "text") == "Start"

    live_ui.find("liveStartButton").clicked.emit()  # type: ignore[attr-defined]
    qtbot.waitUntil(lambda: live_ui.prop("liveTiles", "count") == 3, timeout=5000)
    qtbot.waitUntil(lambda: live_ui.prop("liveValue_rpm", "text") == "900", timeout=5000)
    assert live_ui.prop("liveRange_rpm", "text") == "gesehen 850 bis 900"
    assert live_ui.prop("liveStartButton", "text") == "Stopp"
    assert live_ui.prop("liveSelection", "visible") is False
    assert live_ui.prop("liveVoltage", "text") == "Bordspannung 12,4 V"
    assert live_ui.prop("scanButton", "enabled") is False
    assert live_ui.prop("portBox", "enabled") is False
    assert live_ui.prop("tabLive", "dot").alpha() > 0
    assert live_ui.prop("statusLine", "text").startswith("Live-Daten laufen")
    # Port und Baudrate aus der Kopfzeile
    assert live_ui.live.property("running") is True

    live_ui.find("liveStartButton").clicked.emit()  # type: ignore[attr-defined]
    qtbot.waitUntil(lambda: live_ui.prop("liveStartButton", "text") == "Start", timeout=5000)
    assert live_ui.prop("scanButton", "enabled") is True
    assert live_ui.prop("liveNoticeBanner", "text").startswith("Live-Daten beendet")
    assert live_ui.prop("liveTiles", "count") == 3
    # nach dem ersten Start: nur unterstützte Werte zur Auswahl
    assert live_ui.prop("liveAvailable", "count") == 4
    assert live_ui.warnings == []


def test_live_tab_uses_port_from_header(qtbot: QtBot, live_ui: LiveUi, live_fake: FakeLive) -> None:
    live_ui.find("portBox").setProperty("editText", "/dev/pts/7")
    live_ui.find("viewTabs").setProperty("currentIndex", 4)
    live_ui.find("liveStartButton").clicked.emit()  # type: ignore[attr-defined]
    qtbot.waitUntil(lambda: bool(live_fake.calls), timeout=5000)
    assert live_fake.calls[0][:2] == ("/dev/pts/7", 38400)
    assert live_ui.warnings == []


def test_live_checkbox_toggles_selection(qtbot: QtBot, live_ui: LiveUi) -> None:
    live_ui.find("viewTabs").setProperty("currentIndex", 4)
    live_ui.live.start("/dev/ttyUSB0", 38400)
    qtbot.waitUntil(lambda: live_ui.live.property("supportedKnown") is True, timeout=5000)
    live_ui.live.stop()
    qtbot.waitUntil(lambda: live_ui.live.property("running") is False, timeout=5000)
    check = live_ui.find("liveCheck_maf")
    assert check.property("checked") is False
    check.setProperty("checked", True)
    check.toggled.emit()  # type: ignore[attr-defined]
    assert "maf" in live_ui.live.property("selectedKeys")
    live_ui.live.resetSelection()
    assert check.property("checked") is False  # Bindung bleibt nach dem Klick erhalten
    assert live_ui.warnings == []
