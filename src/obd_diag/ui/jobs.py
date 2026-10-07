"""Blockierende Arbeit (serielle Ein-/Ausgabe) abseits des GUI-Threads ausführen.

Ein Job ist eine Funktion ohne Argumente, die in einem Thread des ``QThreadPool``
läuft. Ergebnis oder Ausnahme kommen über Signale eines ``_Relay``-Objekts zurück,
das im GUI-Thread lebt; Qt stellt sie deshalb dort zu (queued connection). Die
Rückruf-Funktionen laufen also immer im GUI-Thread und dürfen Modelle ändern.
"""

from collections.abc import Callable
from typing import Any, Protocol

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal


class JobRunner(Protocol):
    """Was das View-Model zum Ausführen von Jobs braucht; in Tests austauschbar."""

    def run(
        self,
        job: Callable[[], Any],
        on_success: Callable[[Any], None],
        on_error: Callable[[Exception], None],
    ) -> None: ...


class _Relay(QObject):
    succeeded = Signal(object)
    failed = Signal(object)


class _Task(QRunnable):
    def __init__(self, job: Callable[[], Any], relay: _Relay) -> None:
        super().__init__()
        self._job = job
        self._relay = relay

    def run(self) -> None:
        try:
            result = self._job()
        except Exception as e:  # jede Ausnahme an die Oberfläche melden, nie abstürzen
            self._relay.failed.emit(e)
        else:
            self._relay.succeeded.emit(result)


class ThreadPoolRunner:
    """Führt Jobs im ``QThreadPool`` aus, höchstens einen gleichzeitig.

    Am Adapter darf immer nur ein Job hängen; ein Thread genügt also und verhindert,
    dass sich zwei Jobs denselben Port teilen.
    """

    def __init__(self) -> None:
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(1)
        self._relays: set[_Relay] = set()  # hält die Relays bis zur Zustellung am Leben

    def run(
        self,
        job: Callable[[], Any],
        on_success: Callable[[Any], None],
        on_error: Callable[[Exception], None],
    ) -> None:
        relay = _Relay()
        self._relays.add(relay)

        def succeeded(result: object) -> None:
            self._relays.discard(relay)
            on_success(result)

        def failed(error: object) -> None:
            self._relays.discard(relay)
            on_error(error if isinstance(error, Exception) else RuntimeError(str(error)))

        relay.succeeded.connect(succeeded)
        relay.failed.connect(failed)
        self._pool.start(_Task(job, relay))

    def wait(self, msecs: int = -1) -> bool:
        """Wartet, bis alle Jobs fertig sind (beim Beenden und in Tests)."""
        return self._pool.waitForDone(msecs)
