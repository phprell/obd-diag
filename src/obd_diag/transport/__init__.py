"""Transport-Schicht: Byte-Kanäle zum Adapter (USB-Seriell, später Bluetooth, SocketCAN)."""

from obd_diag.transport.base import Transport, TransportError, TransportTimeout

__all__ = ["Transport", "TransportError", "TransportTimeout"]
