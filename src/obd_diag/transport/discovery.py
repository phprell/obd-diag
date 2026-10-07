"""Angeschlossene Adapter finden (USB-Seriell und gebundene Bluetooth-RFCOMM-Geräte)."""

import re
from dataclasses import dataclass
from pathlib import Path

from serial.tools import list_ports as serial_ports

DEV = Path("/dev")
BLUETOOTH_DESCRIPTION = "Bluetooth (RFCOMM)"

_USB_NAMES = re.compile(r"^/dev/tty(USB|ACM)\d+$")
_RFCOMM_NAME = re.compile(r"^rfcomm\d+$")


@dataclass(frozen=True)
class PortInfo:
    device: str  # z. B. /dev/ttyUSB0 oder /dev/rfcomm0
    description: str  # für die Anzeige, z. B. "FT232R USB UART (FTDI)"


def _is_usb(device: str, hwid: str) -> bool:
    return bool(_USB_NAMES.match(device)) or hwid.startswith("USB")


def _usb_description(description: str, manufacturer: str | None) -> str:
    text = "" if description in ("", "n/a") else description
    if manufacturer and manufacturer not in text:
        text = f"{text} ({manufacturer})" if text else manufacturer
    return text or "USB-Seriell"


def _sort_key(device: str) -> tuple[str, int]:
    """Natürliche Reihenfolge: ttyUSB2 vor ttyUSB10."""
    match = re.match(r"^(.*?)(\d+)$", device)
    return (device, 0) if match is None else (match.group(1), int(match.group(2)))


def list_ports() -> list[PortInfo]:
    """USB-Seriell-Adapter und RFCOMM-Geräte, sortiert nach Gerätenamen.

    Eingebaute serielle Schnittstellen (``/dev/ttyS*`` ohne USB) werden übergangen,
    an ihnen hängt praktisch nie ein OBD-Adapter.
    """
    found: dict[str, PortInfo] = {}
    for port in serial_ports.comports():
        if _RFCOMM_NAME.match(Path(port.device).name):
            found[port.device] = PortInfo(port.device, BLUETOOTH_DESCRIPTION)
        elif _is_usb(port.device, port.hwid or ""):
            description = _usb_description(port.description or "", port.manufacturer)
            found[port.device] = PortInfo(port.device, description)
    # Gebundene RFCOMM-Geräte (``rfcomm bind``) auch dann, wenn pyserial sie nicht meldet
    for path in DEV.glob("rfcomm*"):
        if _RFCOMM_NAME.match(path.name):
            found.setdefault(str(path), PortInfo(str(path), BLUETOOTH_DESCRIPTION))
    return [found[d] for d in sorted(found, key=_sort_key)]
