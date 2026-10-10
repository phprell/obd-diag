"""Bildschirmfotos der Oberfläche für die Dokumentation erzeugen.

    uv run python tools/docs_screenshots.py [--lang en] [ZIELORDNER]

Standard-Zielordner: ``docs/_static/screenshots`` (Deutsch) bzw.
``docs/en/_static/screenshots`` (Englisch, für die englische Doku).

Startet den ELM327-emulator mit standardgemäßen Antworten (wie ``tools/emulator.py``),
öffnet die Oberfläche ohne Bildschirm, verbindet, scannt und speichert jeden Reiter im
hellen und im dunklen Design als PNG. Für Live-Daten läuft eine kurze Abfrage. Die
Bilder sind damit reproduzierbar; nach Änderungen an der Oberfläche neu erzeugen und
einchecken. Einstellungen und Daten landen in einem temporären Ordner, die echten
bleiben unberührt.
"""

import argparse
import os
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Vor dem ersten Qt-Import: ohne Bildschirm, ohne OpenGL, Einstellungen getrennt
_TMP = Path(tempfile.mkdtemp(prefix="obd-diag-screenshots-"))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QUICK_BACKEND"] = "software"
for _name in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
    os.environ[_name] = str(_TMP / _name.lower())

import pytest  # noqa: E402
from elm import Elm, obd_message  # noqa: E402
from PySide6.QtCore import QCoreApplication, QObject, QSettings  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402
from PySide6.QtQuick import QQuickWindow  # noqa: E402

from obd_diag.ui.backend import serial_backend  # noqa: E402
from obd_diag.ui.jobs import ThreadPoolRunner  # noqa: E402
from obd_diag.ui.theme import DesignController  # noqa: E402
from obd_diag.ui.viewmodels.diagnosis import DiagnosisViewModel  # noqa: E402
from obd_diag.ui.viewmodels.live import LiveViewModel  # noqa: E402
from obd_diag.ui.window import (  # noqa: E402
    LANGUAGE_KEY,
    LanguageController,
    load_main_window,
    set_style,
)
from tests.emulator_patches import (  # noqa: E402
    patch_dtc_count_byte,
    patch_freeze_frame,
    patch_persistent_clear,
)

TABS = ("fehlercodes", "readiness", "freeze-frame", "fahrzeug", "live-daten")
SIZE = (1180, 760)


def _start_emulator() -> tuple[Elm, str]:
    mp = pytest.MonkeyPatch()
    mp.setattr(obd_message, "DTC_STORED", ["0420", "0300"])
    mp.setattr(obd_message, "DTC_PENDING", ["0171"])
    mp.setattr(obd_message, "DTC_PERMANENT", [])
    patch_dtc_count_byte(mp, obd_message)
    patch_freeze_frame(mp, obd_message, "0420")
    patch_persistent_clear(mp, obd_message)
    emulator = Elm()
    emulator.set_sorted_obd_msg("car")
    port: str = emulator.get_pty()
    threading.Thread(target=emulator.run, daemon=True).start()
    time.sleep(0.5)
    return emulator, port


def _wait(condition: Callable[[], bool], seconds: float, what: str) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        if time.monotonic() > deadline:
            raise SystemExit(f"Zeitüberschreitung: {what}")
        QCoreApplication.processEvents()
        time.sleep(0.02)


def _settle(seconds: float = 0.4) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        time.sleep(0.02)


def _grab(window: QQuickWindow, path: Path) -> None:
    _settle()
    image = window.grabWindow()
    if image.isNull() or not image.save(str(path)):
        raise SystemExit(f"Bild nicht gespeichert: {path}")
    print(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lang", choices=("de", "en"), default="de")
    parser.add_argument("out_dir", nargs="?", type=Path)
    args = parser.parse_args()
    docs = ROOT / "docs" if args.lang == "de" else ROOT / "docs" / args.lang
    out_dir = args.out_dir or docs / "_static" / "screenshots"
    out_dir.mkdir(parents=True, exist_ok=True)
    emulator, port = _start_emulator()

    app = QGuiApplication(sys.argv[:1])
    app.setApplicationName("obd-diag")
    app.setApplicationDisplayName("OBD-Diagnose")
    set_style()
    settings = QSettings(str(_TMP / "language.conf"), QSettings.Format.IniFormat)
    settings.setValue(LANGUAGE_KEY, args.lang)
    language = LanguageController(settings, app)
    runner = ThreadPoolRunner()
    vm = DiagnosisViewModel(serial_backend(), runner)
    live = LiveViewModel(vm.backend, runner, vm)
    design = DesignController(QSettings(str(_TMP / "design.conf"), QSettings.Format.IniFormat))
    design.setProperty("mode", "light")
    engine = QQmlApplicationEngine()
    load_main_window(engine, vm, design=design, live=live, language=language)
    window = engine.rootObjects()[0]
    assert isinstance(window, QQuickWindow)
    window.resize(*SIZE)
    window.show()
    tabs = window.findChild(QObject, "viewTabs")
    assert tabs is not None

    try:
        vm.connectAndScan(port, 38400)
        _wait(lambda: not vm.property("busy"), 60, "Diagnose")
        if vm.property("errorMessage"):
            raise SystemExit(f"Diagnose fehlgeschlagen: {vm.property('errorMessage')}")

        for mode in ("light", "dark"):
            design.setProperty("mode", mode)
            for index, name in enumerate(TABS[:-1]):
                tabs.setProperty("currentIndex", index)
                _grab(window, out_dir / f"{name}-{mode}.png")

        # Live-Daten: kurze Abfrage mit den Standardwerten
        design.setProperty("mode", "light")
        tabs.setProperty("currentIndex", len(TABS) - 1)
        live.start(port, 38400)
        _wait(lambda: bool(live.property("running")), 30, "Live-Daten starten")
        _settle(20)
        _grab(window, out_dir / "live-daten-light.png")
        design.setProperty("mode", "dark")
        _grab(window, out_dir / "live-daten-dark.png")
        live.stop()
        _wait(lambda: not live.property("running"), 30, "Live-Daten beenden")
    finally:
        live.stop()
        runner.wait()
        emulator.terminate()
        del engine
    return 0


if __name__ == "__main__":
    sys.exit(main())
