"""Einstiegspunkt der Desktop-Oberfläche: ``obd-diag-gui``."""

import importlib.util
import sys


def main(argv: list[str] | None = None) -> int:
    # PySide6 ist ein optionales Extra; ohne es eine verständliche Meldung statt Traceback.
    if importlib.util.find_spec("PySide6") is None:
        print(
            "Fehler: Die Oberfläche braucht PySide6. Installieren mit: "
            "pip install 'obd-diag[gui]' (in der Entwicklung: uv sync)",
            file=sys.stderr,
        )
        return 1

    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlApplicationEngine

    from obd_diag import __version__
    from obd_diag.ui.backend import serial_backend
    from obd_diag.ui.jobs import ThreadPoolRunner
    from obd_diag.ui.viewmodels.diagnosis import DiagnosisViewModel
    from obd_diag.ui.viewmodels.live import LiveViewModel
    from obd_diag.ui.window import install_translations, load_main_window, set_style

    # Eine schon laufende Anwendung (Tests mit pytest-qt) weiterverwenden; Qt erlaubt
    # nur eine je Prozess.
    existing = QGuiApplication.instance()
    if isinstance(existing, QGuiApplication):
        app = existing
    else:
        app = QGuiApplication(sys.argv if argv is None else argv)
    app.setApplicationName("obd-diag")
    app.setApplicationDisplayName("OBD-Diagnose")
    app.setApplicationVersion(__version__)
    set_style()
    install_translations(app)

    runner = ThreadPoolRunner()
    vm = DiagnosisViewModel(serial_backend(), runner)
    # Derselbe Runner: Live-Daten und Diagnose teilen sich den einen Worker-Thread
    live = LiveViewModel(vm.backend, runner, vm)
    engine = QQmlApplicationEngine()
    load_main_window(engine, vm, live=live)
    if not engine.rootObjects():
        print("Fehler: Die Oberfläche ließ sich nicht laden.", file=sys.stderr)
        return 1
    code = app.exec()
    live.stop()  # sonst wartet runner.wait() ewig auf die Live-Abfrage
    runner.wait()  # laufenden Job zu Ende bringen, damit der Port sauber schließt
    # Die Engine vor dem View-Model abbauen, sonst werten die Bindungen ein
    # gelöschtes Objekt aus und melden Fehler.
    del engine
    return code


if __name__ == "__main__":
    sys.exit(main())
