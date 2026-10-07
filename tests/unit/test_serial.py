import os
import tty

import pytest

from obd_diag.transport import TransportTimeout
from obd_diag.transport.serial import SerialTransport


@pytest.fixture
def pty_pair() -> tuple[int, str]:
    master, slave = os.openpty()
    tty.setraw(slave)
    return master, os.ttyname(slave)


def test_stale_prompt_is_discarded_before_write(pty_pair: tuple[int, str]) -> None:
    # python-OBD #173: nach "STOPPED" schickt der Adapter einen zweiten Prompt.
    master, device = pty_pair
    with SerialTransport(device) as transport:
        os.write(master, b"STOPPED\r\r>\r>")
        transport.write(b"0100\r")
        assert os.read(master, 64) == b"0100\r"
        os.write(master, b"4100BE3FA813\r\r>")
        assert transport.read_until(b">", 1.0) == b"4100BE3FA813\r\r>"


def test_read_timeout(pty_pair: tuple[int, str]) -> None:
    _, device = pty_pair
    with SerialTransport(device) as transport, pytest.raises(TransportTimeout):
        transport.read_until(b">", 0.1)
