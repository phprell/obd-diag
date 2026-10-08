import pytest

from obd_diag.protocol.elm327 import (
    Elm327,
    ElmError,
    NoDataError,
    ObdProtocol,
    UnknownCommandError,
)
from obd_diag.transport import TransportError, TransportTimeout
from tests.fakes import FakeTransport
from tests.verification.helpers import RawTransport


def test_initialize_returns_version_and_configures(fake_transport: FakeTransport) -> None:
    elm = Elm327(fake_transport)
    assert elm.initialize() == "ELM327 v1.5"
    assert fake_transport.sent == ["ATZ", "ATE0", "ATL0", "ATS0", "ATH0", "ATSP0"]


def test_echo_is_stripped() -> None:
    transport = FakeTransport({"03": "03\r43 01 33 00 00 00 00"})
    assert Elm327(transport).command("03") == "43 01 33 00 00 00 00"


def test_voltage(fake_transport: FakeTransport) -> None:
    assert Elm327(fake_transport).voltage() == 12.6


def test_no_data_raises() -> None:
    with pytest.raises(NoDataError):
        Elm327(FakeTransport({"07": "NO DATA"})).command("07")
    assert issubclass(NoDataError, ElmError)


def test_query_returns_none_on_no_data() -> None:
    elm = Elm327(FakeTransport({"07": "NO DATA", "03": "4300"}))
    assert elm.query("07") is None
    assert elm.query("03") == "4300"


def test_unknown_command() -> None:
    with pytest.raises(UnknownCommandError):
        Elm327(FakeTransport({"0A": "?"})).query("0A")


def test_searching_line_is_stripped() -> None:
    elm = Elm327(FakeTransport({"0100": "SEARCHING...\r4100BE3FA813"}))
    assert elm.command("0100") == "4100BE3FA813"


@pytest.mark.parametrize("raw", ["BUS INIT: ...OK\r4100BE3FA813", "BUS INIT: ...OK4100BE3FA813"])
def test_bus_init_prefix_is_stripped(raw: str) -> None:
    assert Elm327(FakeTransport({"0100": raw})).command("0100") == "4100BE3FA813"


def test_bus_init_error_raises() -> None:
    with pytest.raises(ElmError, match="BUS INIT"):
        Elm327(FakeTransport({"0100": "BUS INIT: ...ERROR"})).command("0100")


def test_searching_then_unable_to_connect_raises() -> None:
    elm = Elm327(FakeTransport({"0100": "SEARCHING...\rUNABLE TO CONNECT"}))
    with pytest.raises(ElmError, match="UNABLE TO CONNECT"):
        elm.protocol()


def test_protocol_can_auto_detected() -> None:
    transport = FakeTransport(
        {
            "0100": "SEARCHING...\r4100BE3FA813",
            "ATDPN": "A6",
            "ATDP": "AUTO, ISO 15765-4 (CAN 11/500)",
        }
    )
    protocol = Elm327(transport).protocol()
    assert transport.sent == ["0100", "ATDPN", "ATDP"]
    assert protocol == ObdProtocol("6", "ISO 15765-4 (CAN 11/500)")
    assert protocol.is_can


@pytest.mark.parametrize(
    ("dpn", "number", "is_can"),
    [("A3", "3", False), ("5", "5", False), ("8", "8", True), ("A", "A", True), ("AB", "B", True)],
)
def test_protocol_number(dpn: str, number: str, is_can: bool) -> None:
    protocol = Elm327(FakeTransport({"0100": "4100BE3FA813", "ATDPN": dpn})).protocol()
    assert protocol.number == number
    assert protocol.is_can is is_can


@pytest.mark.parametrize(
    "raw",
    [b"\xfc\r\rELM327 v1.5", b"ATZ\r\xfc\r\rELM327 v1.5", b"\r\rELM327 v1.5", b"\x00ELM327 v1.5"],
)
def test_initialize_ignores_junk_before_version(raw: bytes) -> None:
    # Mitschnitte aus python-OBD #153/#164/#187: Klone senden nach ATZ ein Byte FC.
    # Früher lieferte initialize() "�\nELM327 v1.5".
    transport = RawTransport({"ATZ": raw + b"\r\r>"})
    assert Elm327(transport).initialize() == "ELM327 v1.5"


@pytest.mark.parametrize(
    "raw",
    [
        "41 00 BE 3F A8 13\rSTOPPED",
        "43 01 33 00 00 00 00\rBUFFER FULL",
        "ERR94",
        "LV RESET",
        "43 01 33 00 00 00 00 <DATA ERROR",
        "43 01 33 <RX ERROR",
    ],
)
def test_error_lines_raise(raw: str) -> None:
    # Auch nach Teildaten: die Antwort ist unvollständig oder fehlerhaft.
    with pytest.raises(ElmError):
        Elm327(FakeTransport({"03": raw})).command("03")


@pytest.mark.parametrize(
    ("cmd", "echo"),
    [
        ("ATDPN", "atdpn"),
        ("ATDPN", "AT DPN"),
        ("0902", "09 02"),
        ("ATSP0", "at sp 0"),
        ("03", " 03"),
    ],
)
def test_echo_variants_are_stripped(cmd: str, echo: str) -> None:
    transport = RawTransport({cmd: f"{echo}\rA6\r\r>".encode("ascii")})
    assert Elm327(transport).command(cmd) == "A6"


def test_echo_only_before_answer() -> None:
    # Eine Datenzeile, die zufällig dem Befehl gleicht, bleibt erhalten.
    transport = RawTransport({"0100": b"0100\r0100\r\r>"})
    assert Elm327(transport).command("0100") == "0100"


def test_read_more_reads_without_writing() -> None:
    transport = RawTransport({"04": b"7F 04 78\r\r>44\r\r>"})
    elm = Elm327(transport)
    with elm.allow_clear():
        assert elm.command("04") == "7F 04 78"
    assert elm.read_more("04", 1.0) == "44"
    assert transport.sent == ["04"]
    with pytest.raises(TransportTimeout):
        elm.read_more("04", 1.0)


def test_query_with_headers_switches_back() -> None:
    transport = FakeTransport({"0100": "4100"}, headers_on={"0100": "7E8 06 41 00 BE 3F A8 13"})
    elm = Elm327(transport)
    assert elm.query_with_headers("0100") == "7E8 06 41 00 BE 3F A8 13"
    assert transport.sent == ["ATH1", "0100", "ATH0"]
    assert elm.query("0100") == "4100"


class _Sequence(FakeTransport):
    """Antwortet auf wiederholte Befehle der Reihe nach (die letzte Antwort bleibt)."""

    def __init__(self, answers: dict[str, list[str]]) -> None:
        super().__init__({})
        self.answers = answers

    def write(self, data: bytes) -> None:
        cmd = data.decode("ascii").strip()
        queue = self.answers.get(cmd, ["OK"])
        self.responses = {cmd: queue.pop(0) if len(queue) > 1 else queue[0]}
        super().write(data)


@pytest.mark.parametrize("dpn", ["A0", "0", "?", "", "XYZ"])
def test_protocol_unknown_asks_again(dpn: str) -> None:
    transport = _Sequence(
        {"0100": ["4100BE3FA813"], "ATDPN": [dpn, "A6"], "ATDP": ["AUTO, ISO 15765-4"]}
    )
    protocol = Elm327(transport).protocol()
    assert transport.sent == ["0100", "ATDPN", "ATDPN", "ATDP"]
    assert protocol == ObdProtocol("6", "ISO 15765-4")
    assert protocol.is_can


@pytest.mark.parametrize(
    ("headers", "inferred", "is_can"),
    [
        ("SEARCHING...\r7E9 06 41 00 88 18 00 13 \r7E8 06 41 00 BE 3F A8 13 ", "CAN 11 Bit", True),
        ("7E8064100BE3FA813", "CAN 11 Bit", True),
        ("18DAF110064100BE3FA813", "CAN 29 Bit", True),
        ("48 6B 13 41 00 BE 1F B8 11 AD ", "J1850/ISO 9141/KWP", False),
        ("BUS INIT: OK\r86 F1 10 41 00 BE 3E B8 11 8D ", "J1850/ISO 9141/KWP", False),
    ],
)
def test_protocol_inferred_from_headers(headers: str, inferred: str, is_can: bool) -> None:
    transport = FakeTransport(
        {"0100": "4100BE3FA813", "ATDPN": "A0", "ATDP": "AUTO"}, headers_on={"0100": headers}
    )
    protocol = Elm327(transport).protocol()
    assert transport.sent == ["0100", "ATDPN", "ATDPN", "ATH1", "0100", "ATH0", "ATDP"]
    assert protocol.number == "0"
    assert protocol.inferred == inferred
    assert protocol.is_can is is_can
    assert protocol.name == f"AUTO (laut Headern {inferred})"


def test_protocol_no_data_retries_0100_once() -> None:
    transport = FakeTransport({"0100": "NO DATA", "ATDPN": "0", "ATDP": "AUTO"})
    protocol = Elm327(transport).protocol()
    # ohne Antwort keine Header-Probe: bleibt „kein CAN“
    assert transport.sent == ["0100", "ATDPN", "0100", "ATDPN", "ATDP"]
    assert protocol == ObdProtocol("0", "AUTO")
    assert not protocol.is_can


def test_protocol_unreadable_headers_stay_unknown() -> None:
    transport = FakeTransport(
        {"0100": "4100BE3FA813", "ATDPN": "?", "ATDP": "?"}, headers_on={"0100": "GARBAGE"}
    )
    protocol = Elm327(transport).protocol()
    assert protocol == ObdProtocol("", "")
    assert not protocol.is_can


def test_protocol_header_probe_adapter_error_stays_unknown() -> None:
    # Scheitert die Probe mit ATH1 an einem Adapterfehler, bleibt es bei „unbekannt“,
    # und ATH0 wird trotzdem zurückgestellt
    transport = FakeTransport(
        {"0100": "4100BE3FA813", "ATDPN": "A0", "ATDP": "AUTO"}, headers_on={"0100": "CAN ERROR"}
    )
    protocol = Elm327(transport).protocol()
    assert transport.sent == ["0100", "ATDPN", "ATDPN", "ATH1", "0100", "ATH0", "ATDP"]
    assert protocol == ObdProtocol("0", "AUTO")
    assert not protocol.is_can


def test_failed_header_restore_aborts_instead_of_misreading() -> None:
    """Bleibt der Adapter nach dem Header-Fallback auf ATH1, würde alles Weitere falsch
    gelesen; das darf kein Aufrufer als bloß fehlende Angabe abfangen."""

    class StuckInHeaders(FakeTransport):
        def write(self, data: bytes) -> None:
            super().write(data)
            if self.sent[-1] == "ATH0" and self.sent.count("ATH0") > 1:
                self._pending = b"?\r\r>"

    transport = StuckInHeaders({"ATZ": "ELM327 v1.5", "ATDPN": "0", "0100": "4100BE3FA813"})
    elm = Elm327(transport)
    elm.initialize()
    with pytest.raises(TransportError, match="ATH0"):
        elm.protocol()


class _TimeoutRecorder(FakeTransport):
    """Merkt sich je Befehl die Wartezeit, mit der auf den Prompt gelesen wird."""

    def __init__(self, responses: dict[str, str]) -> None:
        super().__init__(responses)
        self.timeouts: list[tuple[str, float]] = []

    def read_until(self, terminator: bytes, timeout: float) -> bytes:
        self.timeouts.append((self.sent[-1], timeout))
        return super().read_until(terminator, timeout)


def test_default_timeout_outlasts_the_adapters_response_pending_wait() -> None:
    # Der ELM327 wartet nach 7F xx 78 selbst bis zu 5 s (Datenblatt S. 45)
    assert Elm327(FakeTransport({})).timeout > 5.0


def test_protocol_search_gets_the_longer_timeout() -> None:
    transport = _TimeoutRecorder(
        {"0100": "SEARCHING...\r4100BE3FA813", "ATDPN": "A6", "ATDP": "AUTO, CAN"}
    )
    elm = Elm327(transport, timeout=2.0, search_timeout=30.0)
    elm.protocol()
    assert transport.timeouts == [("0100", 30.0), ("ATDPN", 2.0), ("ATDP", 2.0)]
    assert elm.timeout == 2.0


def test_repeated_search_also_gets_the_longer_timeout() -> None:
    transport = _TimeoutRecorder({"0100": "NO DATA", "ATDPN": "0", "ATDP": "AUTO"})
    Elm327(transport, timeout=2.0, search_timeout=30.0).protocol()
    assert [t for c, t in transport.timeouts if c == "0100"] == [30.0, 30.0]


def test_search_timeout_never_shortens_the_normal_one() -> None:
    transport = _TimeoutRecorder({"0100": "4100BE3FA813", "ATDPN": "6", "ATDP": "CAN"})
    Elm327(transport, timeout=40.0, search_timeout=30.0).protocol()
    assert transport.timeouts[0] == ("0100", 40.0)


def test_search_timeout_is_restored_after_an_error() -> None:
    elm = Elm327(_TimeoutRecorder({"0100": "CAN ERROR"}), timeout=2.0)
    with pytest.raises(ElmError):
        elm.protocol()
    assert elm.timeout == 2.0
