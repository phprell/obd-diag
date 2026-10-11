"""Einstiegspunkt der Desktop-Oberfläche: ``obd-diag-gui``."""

import importlib.util
import os
import sys
from pathlib import Path

from obd_diag.i18n import set_language, system_language, tr


def _desktop_entry(option: str, program: str) -> int:
    """``--install-desktop`` / ``--remove-desktop``: Startmenü-Eintrag für pipx/pip."""
    from obd_diag.desktop import install_desktop_entry, remove_desktop_entry

    set_language(system_language())
    if option == "--install-desktop":
        # Absoluter Pfad, aber Symlinks nicht auflösen: pipx verlinkt ~/.local/bin/... in
        # seine Umgebung, und der Link bleibt bei einem Upgrade gültig.
        for path in install_desktop_entry(os.path.abspath(program)):
            print(tr("Angelegt: {path}").format(path=path))
    else:
        removed = remove_desktop_entry()
        for path in removed:
            print(tr("Entfernt: {path}").format(path=path))
        if not removed:
            print(tr("Kein Startmenü-Eintrag vorhanden."))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv if argv is None else argv
    for option in ("--install-desktop", "--remove-desktop"):
        if option in args[1:]:
            return _desktop_entry(option, args[0])

    # PySide6 ist ein optionales Extra; ohne es eine verständliche Meldung statt Traceback.
    if importlib.util.find_spec("PySide6") is None:
        set_language(system_language())
        print(
            tr(
                "Fehler: Die Oberfläche braucht PySide6. Installieren mit: "
                "pip install 'obd-diag[gui]' (in der Entwicklung: uv sync)"
            ),
            file=sys.stderr,
        )
        return 1

    from PySide6.QtGui import QGuiApplication, QIcon
    from PySide6.QtQml import QQmlApplicationEngine

    from obd_diag import __version__
    from obd_diag.desktop import APP_ID
    from obd_diag.ui.backend import serial_backend
    from obd_diag.ui.jobs import ThreadPoolRunner
    from obd_diag.ui.viewmodels.diagnosis import DiagnosisViewModel
    from obd_diag.ui.viewmodels.live import LiveViewModel
    from obd_diag.ui.window import create_language, load_main_window, set_style

    # Eine schon laufende Anwendung (Tests mit pytest-qt) weiterverwenden; Qt erlaubt
    # nur eine je Prozess.
    existing = QGuiApplication.instance()
    app = existing if isinstance(existing, QGuiApplication) else QGuiApplication(args)
    app.setApplicationName("obd-diag")
    app.setApplicationVersion(__version__)
    # Verknüpft das Fenster mit der Desktop-Datei (Wayland: app_id) für Symbol und Taskleiste
    app.setDesktopFileName(APP_ID)
    icon = QIcon(str(Path(__file__).parent.parent / "share" / f"{APP_ID}.svg"))
    app.setWindowIcon(QIcon.fromTheme(APP_ID, icon))
    set_style()
    # Sprache vor dem View-Model: gespeicherte Wahl, sonst die Systemsprache
    language = create_language(app)
    app.setApplicationDisplayName(tr("OBD-Diagnose"))

    runner = ThreadPoolRunner()
    vm = DiagnosisViewModel(serial_backend(), runner)
    # Derselbe Runner: Live-Daten und Diagnose teilen sich den einen Worker-Thread
    live = LiveViewModel(vm.backend, runner, vm)
    engine = QQmlApplicationEngine()
    load_main_window(engine, vm, live=live, language=language)
    if not engine.rootObjects():
        print(tr("Fehler: Die Oberfläche ließ sich nicht laden."), file=sys.stderr)
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
