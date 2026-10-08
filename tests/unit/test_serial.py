import os
import termios
import tty

import pytest

from obd_diag.transport import TransportError, TransportTimeout
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


def test_lost_connection_is_a_transport_error(pty_pair: tuple[int, str]) -> None:
    # Wie ein abgezogener USB-Adapter: die Gegenseite verschwindet, read liefert EIO.
    master, device = pty_pair
    with SerialTransport(device) as transport:
        os.close(master)
        with pytest.raises(TransportError, match="unterbrochen"):
            transport.read_until(b">", 1.0)


def test_failing_close_does_not_hide_the_real_error(
    pty_pair: tuple[int, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    master, device = pty_pair

    def broken_close(self: object) -> None:
        raise OSError(5, "Input/output error")

    # Erwartet wird die Meldung aus read, nicht das OSError aus close
    with (
        pytest.raises(TransportError, match="unterbrochen"),
        SerialTransport(device) as transport,
    ):
        monkeypatch.setattr(type(transport._port()), "close", broken_close)
        os.close(master)
        transport.read_until(b">", 1.0)
    assert transport._serial is None


def test_unplugged_between_commands_is_noticed_on_write(pty_pair: tuple[int, str]) -> None:
    # Häufigster Fall in der Praxis: abgezogen zwischen zwei Befehlen, also beim Senden.
    # pyserial meldet das beim Leeren des Puffers als termios.error (kein OSError).
    master, device = pty_pair
    with SerialTransport(device) as transport:
        os.close(master)
        with pytest.raises(TransportError, match=r"unterbrochen \(Input/output error\)"):
            transport.write(b"0100\r")


def test_unplugged_is_noticed_when_setting_the_timeout(
    pty_pair: tuple[int, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Vor jedem Lesen stellt pyserial den Timeout um (tcsetattr): auch dort termios.error
    _, device = pty_pair

    def gone(self: object) -> None:
        raise termios.error(5, "Input/output error")

    with SerialTransport(device) as transport:
        monkeypatch.setattr(type(transport._port()), "_reconfigure_port", gone)
        with pytest.raises(TransportError, match="unterbrochen"):
            transport.read_until(b">", 1.0)


def test_use_before_open_is_a_transport_error() -> None:
    with pytest.raises(TransportError, match="nicht geöffnet"):
        SerialTransport("/dev/null").write(b"0100\r")
