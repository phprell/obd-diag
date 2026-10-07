"""Start der Oberfläche (``obd-diag-gui``), Übersetzungen, Jobs und Backend-Kleinteile."""

import importlib.util
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from obd_diag.ui import app

# Ohne PySide6 muss der Einstieg trotzdem eine verständliche Meldung geben.


def test_main_without_pyside6(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    real_find_spec = importlib.util.find_spec

    def find_spec(name: str, *args: Any) -> Any:
        return None if name == "PySide6" else real_find_spec(name, *args)

    monkeypatch.setattr(importlib.util, "find_spec", find_spec)
    assert app.main([]) == 1
    err = capsys.readouterr().err
    assert err.startswith("Fehler: Die Oberfläche braucht PySide6.")
    assert "pip install 'obd-diag[gui]'" in err


pytest.importorskip("PySide6.QtQuick")

from PySide6.QtCore import (  # noqa: E402
    QCoreApplication,
    QLibraryInfo,
    QLocale,
    QTimer,
    QTranslator,
)
from PySide6.QtGui import QGuiApplication  # noqa: E402
from pytestqt.qtbot import QtBot  # noqa: E402

from obd_diag.ui import window  # noqa: E402
from obd_diag.ui.backend import Backend, serial_backend  # noqa: E402
from obd_diag.ui.jobs import ThreadPoolRunner, _Relay, _Task  # noqa: E402
from tests.samples import minimal_session  # noqa: E402


@pytest.fixture
def restore_app(qapp: QCoreApplication) -> Iterator[QCoreApplication]:
    """``main`` stellt Sprache und Übersetzer der gemeinsamen Test-Anwendung um."""
    locale = QLocale()
    before = set(qapp.findChildren(QTranslator))
    yield qapp
    for translator in set(qapp.findChildren(QTranslator)) - before:
        qapp.removeTranslator(translator)
        translator.deleteLater()
    QLocale.setDefault(locale)


def test_main_starts_window_and_quits(
    restore_app: QCoreApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []

    def look_and_quit() -> None:
        seen.extend(w.title() for w in QGuiApplication.topLevelWindows() if w.isVisible())
        QCoreApplication.quit()

    QTimer.singleShot(300, look_and_quit)
    assert app.main(["obd-diag-gui"]) == 0
    assert "OBD-Diagnose" in seen
    assert QCoreApplication.applicationName() == "obd-diag"
    assert QGuiApplication.applicationDisplayName() == "OBD-Diagnose"
    assert QLocale().language() == QLocale.Language.German


def test_main_reports_broken_qml(
    restore_app: QCoreApplication,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(window, "load_main_window", lambda engine, vm: None)
    assert app.main([]) == 1
    assert capsys.readouterr().err == "Fehler: Die Oberfläche ließ sich nicht laden.\n"


# --- Übersetzungen ---


def test_install_translations_german(restore_app: QCoreApplication) -> None:
    translators = window.install_translations(restore_app)
    assert QLocale().language() == QLocale.Language.German
    assert len(translators) == 2  # qtbase und qtdeclarative liegen PySide6 bei
    assert all(t.parent() is restore_app and not t.isEmpty() for t in translators)
    # Qt-eigene Texte sind jetzt deutsch
    assert QCoreApplication.translate("QPlatformTheme", "Cancel") == "Abbrechen"


def test_install_translations_without_files(
    restore_app: QCoreApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(QLibraryInfo, "path", lambda which: str(tmp_path))
    assert window.install_translations(restore_app) == []
    assert QLocale().language() == QLocale.Language.German  # Datumsformat trotzdem


# --- Jobs: Fehlerpfade ---


def test_task_reports_exception() -> None:
    relay = _Relay()
    failed: list[object] = []
    succeeded: list[object] = []
    relay.failed.connect(failed.append)
    relay.succeeded.connect(succeeded.append)

    def boom() -> None:
        raise ValueError("kaputt")

    _Task(boom, relay).run()  # direkt im Test-Thread: Signale kommen sofort
    assert [type(e) for e in failed] == [ValueError] and succeeded == []
    _Task(lambda: 42, relay).run()
    assert succeeded == [42]


def test_runner_error_paths(qtbot: QtBot) -> None:
    runner = ThreadPoolRunner()
    errors: list[Exception] = []
    results: list[object] = []

    def boom() -> None:
        raise OSError("Port weg")

    runner.run(boom, results.append, errors.append)
    qtbot.waitUntil(lambda: len(errors) == 1)
    assert runner.wait(5000)
    assert isinstance(errors[0], OSError) and str(errors[0]) == "Port weg"
    assert results == []
    assert runner._relays == set()  # Relay nach der Zustellung freigegeben


def test_runner_wraps_non_exception_errors(qtbot: QtBot) -> None:
    """Kommt über das Signal etwas anderes als eine Exception an, wird sie verpackt."""
    runner = ThreadPoolRunner()
    errors: list[Exception] = []
    relays: list[_Relay] = []

    real_relay = _Relay

    def capture_relay() -> _Relay:
        relay = real_relay()
        relays.append(relay)
        return relay

    import obd_diag.ui.jobs as jobs

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(jobs, "_Relay", capture_relay)
        runner.run(lambda: None, lambda result: None, errors.append)
    assert runner.wait(5000)
    qtbot.waitUntil(lambda: runner._relays == set())
    # Ein zweiter, künstlicher Fehler über dasselbe Relay: kein Exception-Objekt
    runner._relays.add(relays[0])
    relays[0].failed.emit("nur Text")
    assert [(type(e), str(e)) for e in errors] == [(RuntimeError, "nur Text")]


# --- Backend: Kleinteile ohne Adapter ---


def test_backend_default_save_session(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    backend = Backend(
        diagnose=lambda port, baud, online: minimal_session(),
        clear=lambda port, baud: pytest.fail("nicht gebraucht"),
        list_ports=list,
        catalog_available=lambda: True,
    )
    path = backend.save_session(minimal_session())
    assert path.parent == tmp_path / "obd-diag" / "sessions"
    assert backend.load_session(path) == minimal_session()
    backend.set_tracing(True)  # ohne eigene Funktion: nichts zu tun


def test_serial_backend_forwards_tracing(monkeypatch: pytest.MonkeyPatch) -> None:
    from obd_diag.ui import backend as backend_module

    calls: list[tuple[str, Any]] = []
    monkeypatch.setattr(
        backend_module,
        "diagnose_port",
        lambda port, baud, online, trace: calls.append(("diagnose", (port, baud, online, trace))),
    )
    monkeypatch.setattr(
        backend_module,
        "clear_port",
        lambda port, baud, trace: calls.append(("clear", (port, baud, trace))),
    )
    backend = serial_backend()
    backend.diagnose("/dev/ttyUSB0", 38400, True)
    backend.set_tracing(True)
    backend.clear("/dev/ttyUSB0", 9600)
    backend.diagnose("/dev/ttyUSB1", 38400, False)
    assert calls == [
        ("diagnose", ("/dev/ttyUSB0", 38400, True, False)),
        ("clear", ("/dev/ttyUSB0", 9600, True)),
        ("diagnose", ("/dev/ttyUSB1", 38400, False, True)),
    ]
