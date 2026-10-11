"""Demo-Fahrzeug in der Oberfläche: Portliste, Vorauswahl, Hinweis und echter Durchlauf."""

import gc
from collections.abc import Iterator
from typing import Any

import pytest

pytest.importorskip("PySide6.QtQuick")

from PySide6.QtCore import QCoreApplication, QObject, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from pytestqt.qtbot import QtBot

from obd_diag.services.diagnostics import DtcKind
from obd_diag.transport.demo import DEMO_PORT
from obd_diag.transport.discovery import PortInfo
from obd_diag.ui import app
from obd_diag.ui import backend as backend_module
from obd_diag.ui.viewmodels.diagnosis import DiagnosisViewModel
from obd_diag.ui.window import load_main_window, set_style
from tests.ui.conftest import FakeBackend, SyncRunner
from tests.ui.test_app import restore_app  # noqa: F401  (Fixture)
from tests.ui.test_qml import Ui

DEMO = PortInfo(DEMO_PORT, "Demo")
ADAPTER = PortInfo("/dev/ttyUSB1", "USB Serial (QinHeng)")


def _vm(*ports: PortInfo) -> DiagnosisViewModel:
    return DiagnosisViewModel(FakeBackend(ports=list(ports)).as_backend(), SyncRunner())


def test_serial_backend_lists_the_demo_after_the_adapters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backend_module, "list_ports", lambda: [ADAPTER])
    ports = backend_module.serial_backend().list_ports()
    assert [p.device for p in ports] == ["/dev/ttyUSB1", DEMO_PORT]
    assert "Demo" in ports[1].description


def test_demo_is_never_chosen_silently() -> None:
    assert _vm(DEMO).property("defaultPort") == "/dev/ttyUSB0"
    assert _vm(ADAPTER, DEMO).property("defaultPort") == "/dev/ttyUSB1"
    vm = _vm(ADAPTER, DEMO)
    vm.prefer_demo()
    assert vm.property("defaultPort") == DEMO_PORT


@pytest.fixture
def ui(qapp: Any) -> Iterator[Ui]:
    set_style()
    vm = _vm(DEMO)
    engine = QQmlApplicationEngine()
    result = Ui(engine, vm)
    load_main_window(engine, vm)
    yield result
    # wie in test_qml: Engine vor dem View-Model abbauen, dann aufräumen
    result._window = None
    del result.engine, engine
    gc.collect()


def test_banner_shows_while_the_demo_is_selected(qtbot: QtBot, ui: Ui) -> None:
    vm = ui.vm
    port_box, banner = ui.find("portBox"), ui.find("demoBanner")
    assert port_box.property("editText") == "/dev/ttyUSB0"
    assert banner.property("visible") is False
    assert ui.warnings == []
    vm.prefer_demo()
    qtbot.waitUntil(lambda: port_box.property("editText") == DEMO_PORT)
    assert banner.property("visible") is True
    assert "Beispielfahrzeug" in banner.property("text")
    port_box.setProperty("editText", "/dev/ttyUSB0")
    qtbot.waitUntil(lambda: banner.property("visible") is False)


def test_diagnose_port_reads_the_demo_vehicle() -> None:
    session = backend_module.diagnose_port(DEMO_PORT, 38400)
    assert session.scan.adapter.endswith("(Demo)")
    assert [(c.code, c.kind) for c in session.scan.codes] == [("U1218", DtcKind.STORED)]


def test_live_port_reads_the_demo_vehicle() -> None:
    samples: list[Any] = []
    result = backend_module.live_port(
        DEMO_PORT,
        38400,
        ["rpm"],
        0.0,
        False,
        on_setup=lambda setup: None,
        on_start=lambda pids, path: None,
        on_sample=samples.append,
        should_stop=lambda: len(samples) >= 3,
    )
    assert result.samples == 3
    assert all(800 < s.values["rpm"] < 900 for s in samples)


def test_main_with_demo_preselects_it(
    restore_app: QCoreApplication,  # noqa: F811
) -> None:
    seen: list[str] = []

    def look_and_quit() -> None:
        for w in QGuiApplication.topLevelWindows():
            box = w.findChild(QObject, "portBox")
            if box is not None:
                seen.append(box.property("editText"))
        QCoreApplication.quit()

    QTimer.singleShot(300, look_and_quit)
    assert app.main(["obd-diag-gui", "--demo"]) == 0
    assert DEMO_PORT in seen
