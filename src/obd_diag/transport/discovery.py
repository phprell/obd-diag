"""Angeschlossene Adapter finden (USB-Seriell und gebundene Bluetooth-RFCOMM-Geräte)."""

from dataclasses import dataclass


@dataclass(frozen=True)
class PortInfo:
    device: str  # z. B. /dev/ttyUSB0 oder /dev/rfcomm0
    description: str  # für die Anzeige, z. B. "FT232R USB UART (FTDI)"


def list_ports() -> list[PortInfo]:
    raise NotImplementedError
