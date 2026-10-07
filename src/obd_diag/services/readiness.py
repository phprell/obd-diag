"""Readiness-Monitore (Mode 01, PID 01): Status der Eigendiagnosen fürs Abgas-System."""

from dataclasses import dataclass
from enum import StrEnum

from obd_diag.protocol.elm327 import Elm327


class MonitorState(StrEnum):
    COMPLETE = "complete"  # Test abgeschlossen
    INCOMPLETE = "incomplete"  # Test noch nicht gelaufen, z. B. nach dem Löschen
    NOT_SUPPORTED = "not_supported"


@dataclass(frozen=True)
class Monitor:
    key: str  # stabil, z. B. "misfire", "catalyst", "egr"
    name: str  # Anzeige auf Deutsch, z. B. "Verbrennungsaussetzer"
    state: MonitorState


@dataclass(frozen=True)
class ReadinessStatus:
    mil_on: bool
    dtc_count: int  # vom Steuergerät gemeldete Zahl gespeicherter Codes
    compression_ignition: bool  # True: Diesel-Monitore, False: Otto-Monitore
    monitors: tuple[Monitor, ...]

    @property
    def ready(self) -> bool:
        """Alle unterstützten Monitore abgeschlossen (Voraussetzung für die AU)."""
        return all(m.state is not MonitorState.INCOMPLETE for m in self.monitors)


def decode_readiness(data: bytes) -> ReadinessStatus:
    """Dekodiert die vier Datenbytes A-D der Antwort auf ``0101``."""
    raise NotImplementedError


def read_readiness(elm: Elm327) -> ReadinessStatus | None:
    """``None``, wenn das Steuergerät PID 01 nicht beantwortet."""
    raise NotImplementedError
