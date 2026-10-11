"""Sitzungsvergleich in der Oberfläche: View-Model und Dialog (Datei → Mit Sitzung
vergleichen …)."""

import dataclasses
import gc
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("PySide6.QtQuick")

from PySide6.QtCore import QMetaObject, QObject
from PySide6.QtQml import QQmlApplicationEngine, QQmlError
from PySide6.QtQuick import QQuickItem, QQuickWindow
from pytestqt.qtbot import QtBot

from obd_diag.i18n import set_language
from obd_diag.services.diagnostics import DiagnosticCode, DtcKind
from obd_diag.services.session import Session
from obd_diag.ui.viewmodels.compare import CompareViewModel
from obd_diag.ui.viewmodels.diagnosis import DiagnosisViewModel
from obd_diag.ui.window import load_main_window, set_style
from tests.samples import full_session
from tests.ui.conftest import FakeBackend, SyncRunner


def _older(session: Session) -> Session:
    """Dieselbe Sitzung drei Tage früher, mit P0171 statt P0300."""
    codes = [c for c in session.scan.codes if c.code != "P0300"]
    codes.append(DiagnosticCode("P0171", DtcKind.STORED))
    return dataclasses.replace(
        session,
        created=session.created - timedelta(days=3),
        scan=dataclasses.replace(session.scan, codes=codes),
    )


@pytest.fixture
def scanned() -> tuple[DiagnosisViewModel, FakeBackend]:
    backend = FakeBackend(full_session())
    vm = DiagnosisViewModel(backend.as_backend(), SyncRunner())
    vm.connectAndScan("/dev/ttyUSB0", 38400)
    return vm, backend


def test_compare_with_saved_session(scanned: tuple[DiagnosisViewModel, FakeBackend]) -> None:
    vm, backend = scanned
    path = backend.as_backend().save_session(_older(full_session()))
    compare = CompareViewModel(vm)
    events: list[bool] = []
    compare.compared.connect(lambda: events.append(True))
    compare.compareWith(path.as_uri())
    assert events == [True]
    assert compare.property("hasResult") is True
    assert compare.property("errorMessage") == ""
    result = compare.property("comparison")
    # Die Datei ist älter, also „vorher“; die angezeigte Diagnose „nachher“
    assert result["before"] == f"2026-10-04 14:32 · {path}"
    assert result["after"] == "2026-10-07 14:32 · angezeigte Diagnose"
    assert result["warning"] == "" and result["summary"] == ""
    assert [s["key"] for s in result["sections"]] == ["codes"]
    changes = {c["subject"].split()[0]: c for c in result["sections"][0]["changes"]}
    assert changes["P0300"]["text"] == "neu: gespeichert"
    assert changes["P0300"]["trend"] == "worse"
    assert changes["P0171"]["text"] == "entfällt (vorher: gespeichert)"
    assert changes["P0171"]["kind"] == "removed"
    assert changes["P0420"]["kind"] == "unchanged"


def test_compare_identical_and_retranslate(
    scanned: tuple[DiagnosisViewModel, FakeBackend],
) -> None:
    vm, backend = scanned
    path = backend.as_backend().save_session(full_session())
    compare = CompareViewModel(vm)
    changed: list[bool] = []
    compare.changed.connect(lambda: changed.append(True))
    compare.compareWith(str(path))
    assert compare.property("comparison")["summary"] == "Keine Unterschiede."
    set_language("en")
    try:
        compare.retranslate()
        result = compare.property("comparison")
        assert result["summary"] == "No differences."
        assert result["sections"][0]["title"] == "Trouble codes"
        assert result["after"].endswith("· displayed diagnosis")
    finally:
        set_language("de")
    assert len(changed) == 2


def test_compare_error_and_without_session(
    scanned: tuple[DiagnosisViewModel, FakeBackend], tmp_path: Path
) -> None:
    vm, _backend = scanned
    broken = tmp_path / "kaputt.json"
    broken.write_text("kein json", encoding="utf-8")
    compare = CompareViewModel(vm)
    compare.compareWith(str(broken))
    assert compare.property("hasResult") is False
    assert compare.property("errorMessage").startswith("Sitzung ließ sich nicht öffnen:")
    assert compare.property("comparison") == {}
    compare.compareWith(str(tmp_path / "fehlt.json"))
    assert "fehlt.json" in compare.property("errorMessage")

    # ohne angezeigte Sitzung passiert nichts
    empty = CompareViewModel(
        DiagnosisViewModel(FakeBackend(full_session()).as_backend(), SyncRunner())
    )
    empty.compareWith(str(broken))
    assert empty.property("errorMessage") == ""


# --- Dialog im echten Fenster ---------------------------------------------------------


class _Ui:
    def __init__(self, engine: QQmlApplicationEngine, vm: DiagnosisViewModel) -> None:
        self.engine = engine
        self.vm = vm
        self.warnings: list[str] = []
        self.window: QQuickWindow | None = None

    def find(self, name: str) -> QObject:
        if self.window is None:
            root = self.engine.rootObjects()[0]
            assert isinstance(root, QQuickWindow)
            self.window = root
        obj = self.window.findChild(QObject, name)
        assert obj is not None, name
        return obj


@pytest.fixture
def ui(qapp: Any, fake_backend: FakeBackend) -> Iterator[_Ui]:
    set_style()
    vm = DiagnosisViewModel(fake_backend.as_backend(), SyncRunner())
    engine = QQmlApplicationEngine()
    result = _Ui(engine, vm)

    def collect(errors: list[QQmlError]) -> None:
        result.warnings.extend(e.toString() for e in errors)

    engine.warnings.connect(collect)
    load_main_window(engine, vm)
    yield result
    engine.warnings.disconnect(collect)
    result.window = None
    del result.engine, engine
    gc.collect()


def test_compare_dialog(qtbot: QtBot, ui: _Ui, fake_backend: FakeBackend) -> None:
    item = ui.find("compareMenuItem")
    assert item.property("enabled") is False  # erst mit einer Sitzung
    ui.vm.connectAndScan("/dev/ttyUSB0", 38400)
    assert item.property("enabled") is True

    assert ui.vm.session is not None
    path = fake_backend.as_backend().save_session(ui.vm.session)
    compare = ui.engine.rootContext().contextProperty("sessionCompare")
    compare.compareWith(path.as_uri())
    dialog = ui.find("compareDialog")
    qtbot.waitUntil(lambda: dialog.property("visible") is True)
    assert ui.find("compareSummary").property("text") == "Keine Unterschiede."
    assert ui.find("compareWarning").property("visible") is False
    assert ui.find("compareError").property("visible") is False
    assert ui.find("compareAfter").property("text").endswith("· angezeigte Diagnose")
    sections = ui.find("compareSections")
    assert isinstance(sections, QQuickItem)
    assert sections.property("count") == 1
    assert ui.warnings == []

    QMetaObject.invokeMethod(dialog, "close")
    qtbot.waitUntil(lambda: dialog.property("visible") is False)
    compare.compareWith((path.parent / "fehlt.json").as_uri())
    qtbot.waitUntil(lambda: dialog.property("visible") is True)
    assert ui.find("compareError").property("visible") is True
    assert ui.find("compareSummary").property("visible") is False
    assert ui.warnings == []
