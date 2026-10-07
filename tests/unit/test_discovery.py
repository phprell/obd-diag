from pathlib import Path

import pytest
from serial.tools.list_ports_common import ListPortInfo

from obd_diag.transport import discovery
from obd_diag.transport.discovery import PortInfo, list_ports


def _port(
    device: str,
    description: str = "n/a",
    hwid: str = "n/a",
    manufacturer: str | None = None,
) -> ListPortInfo:
    info = ListPortInfo(device, skip_link_detection=True)
    info.description = description
    info.hwid = hwid
    info.manufacturer = manufacturer
    return info


@pytest.fixture
def dev(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(discovery, "DEV", tmp_path)
    return tmp_path


def _comports(monkeypatch: pytest.MonkeyPatch, ports: list[ListPortInfo]) -> None:
    monkeypatch.setattr("serial.tools.list_ports.comports", lambda: ports)


def test_usb_and_bluetooth_sorted(monkeypatch: pytest.MonkeyPatch, dev: Path) -> None:
    _comports(
        monkeypatch,
        [
            _port("/dev/ttyUSB10", "USB-Serial Controller", "USB VID:PID=067B:2303", "Prolific"),
            _port("/dev/ttyS0"),  # eingebaut, ohne USB
            _port("/dev/ttyUSB2", "FT232R USB UART", "USB VID:PID=0403:6001", "FTDI"),
            _port("/dev/ttyACM0", "n/a", "USB VID:PID=2341:0043"),
            _port("/dev/ttyS4", "Adapter", "USB VID:PID=1A86:7523", "QinHeng"),
            _port("/dev/ttyAMA0", "ttyAMA0", "3f201000.serial"),
            _port(str(dev / "rfcomm1")),  # meldet pyserial auch, aber nur einmal ausgeben
        ],
    )
    (dev / "rfcomm0").touch()
    (dev / "rfcomm1").touch()
    (dev / "rfcomm-irgendwas").touch()
    # tmp_path liegt unter /tmp und wird damit nach /dev/... einsortiert
    assert list_ports() == [
        PortInfo("/dev/ttyACM0", "USB-Seriell"),
        PortInfo("/dev/ttyS4", "Adapter (QinHeng)"),
        PortInfo("/dev/ttyUSB2", "FT232R USB UART (FTDI)"),
        PortInfo("/dev/ttyUSB10", "USB-Serial Controller (Prolific)"),
        PortInfo(str(dev / "rfcomm0"), "Bluetooth (RFCOMM)"),
        PortInfo(str(dev / "rfcomm1"), "Bluetooth (RFCOMM)"),
    ]


def test_natural_order(monkeypatch: pytest.MonkeyPatch, dev: Path) -> None:
    names = ["/dev/ttyUSB10", "/dev/ttyUSB2", "/dev/ttyUSB1"]
    _comports(monkeypatch, [_port(n, "X", "USB VID:PID=0403:6001") for n in names])
    assert [p.device for p in list_ports()] == ["/dev/ttyUSB1", "/dev/ttyUSB2", "/dev/ttyUSB10"]


def test_description_contains_manufacturer(monkeypatch: pytest.MonkeyPatch, dev: Path) -> None:
    _comports(
        monkeypatch,
        [
            _port("/dev/ttyUSB0", "FTDI FT232R", "USB VID:PID=0403:6001", "FTDI"),
            _port("/dev/ttyUSB1", "n/a", "USB VID:PID=0403:6001", "FTDI"),
        ],
    )
    assert [p.description for p in list_ports()] == ["FTDI FT232R", "FTDI"]


def test_nothing_found(monkeypatch: pytest.MonkeyPatch, dev: Path) -> None:
    _comports(monkeypatch, [_port("/dev/ttyS0"), _port("/dev/ttyS1")])
    assert list_ports() == []
