"""Einrichtung prüfen (``services/setup_check.py``): Gruppen, Zugriff, Baudraten."""

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from obd_diag import cli
from obd_diag.services import setup_check
from obd_diag.services.setup_check import (
    CheckItem,
    FoundAdapter,
    Host,
    SetupReport,
    Status,
    check_group,
    check_port_access,
    probe_adapter,
    run_setup_check,
    serial_group,
)
from obd_diag.transport import Transport, TransportError, TransportTimeout
from obd_diag.transport.discovery import PortInfo
from tests.fakes import FakeTransport

INIT = ["ATZ", "ATE0", "ATL0", "ATS0", "ATH0", "ATSP0"]
CH340 = PortInfo("/dev/ttyUSB0", "USB Serial (QinHeng)")


def host(
    *,
    groups: frozenset[str] = frozenset({"philipp", "wheel"}),
    members: dict[str, frozenset[str]] | None = None,
    access: bool = True,
    device_group: str | None = "uucp",
    distribution: frozenset[str] = frozenset({"arch"}),
    programs: frozenset[str] = frozenset(),
    root: bool = False,
) -> Host:
    known = {"uucp": frozenset[str](), "dialout": frozenset[str]()} | (members or {})
    return Host(
        user="philipp",
        is_root=root,
        process_groups=groups,
        group_members=known.get,
        device_group=lambda path: device_group,
        can_access=lambda path: access,
        distribution=distribution,
        has_program=lambda name: name in programs,
    )


class Silent(FakeTransport):
    """Adapter mit anderer Baudrate: es kommt nie ein Prompt."""

    def __init__(self) -> None:
        super().__init__({})

    def read_until(self, terminator: bytes, timeout: float) -> bytes:
        raise TransportTimeout(f"keine Antwort nach {timeout} s")


class Opener:
    """Öffnet je Baudrate den vorbereiteten Fake und merkt sich, was gesendet wurde."""

    def __init__(self, by_baud: dict[int, Callable[[], Transport]]) -> None:
        self.by_baud = by_baud
        self.opened: list[tuple[str, int, Transport]] = []

    def __call__(self, port: str, baud: int) -> Transport:
        transport = self.by_baud[baud]()
        self.opened.append((port, baud, transport))
        return transport

    def sent(self) -> list[tuple[int, list[str]]]:
        return [(baud, t.sent) for _port, baud, t in self.opened]  # type: ignore[attr-defined]


def elm() -> FakeTransport:
    return FakeTransport({"ATZ": "ELM327 v1.5"})


# --- Gruppe je Distribution -----------------------------------------------------------


@pytest.mark.parametrize(
    ("ids", "group"),
    [
        ({"arch"}, "uucp"),
        ({"endeavouros", "arch"}, "uucp"),  # ID_LIKE=arch
        ({"debian"}, "dialout"),
        ({"ubuntu", "debian"}, "dialout"),
        ({"fedora"}, "dialout"),
        ({"opensuse-tumbleweed", "suse"}, "dialout"),
        (set(), "dialout"),
    ],
)
def test_serial_group_by_distribution(ids: set[str], group: str) -> None:
    assert serial_group(frozenset(ids)) == group


def test_distribution_reads_id_and_id_like(tmp_path: Path) -> None:
    os_release = tmp_path / "os-release"
    os_release.write_text('NAME="Ubuntu"\nID=ubuntu\nID_LIKE="debian"\nVERSION_ID=24.04\n')
    assert setup_check._distribution(os_release) == {"ubuntu", "debian"}
    assert setup_check._distribution(tmp_path / "fehlt") == frozenset()


# --- Gruppenmitgliedschaft ------------------------------------------------------------


def test_member_of_group() -> None:
    item = check_group(host(groups=frozenset({"uucp"})), "uucp")
    assert item.status is Status.OK
    assert item.title == "Nutzer philipp ist in der Gruppe uucp"


def test_not_in_group_names_the_command() -> None:
    item = check_group(host(), "uucp")
    assert item.status is Status.ERROR
    assert item.fix == "sudo usermod -aG uucp $USER"


def test_added_but_not_logged_in_again() -> None:
    item = check_group(host(members={"uucp": frozenset({"philipp"})}), "uucp")
    assert item.status is Status.WARNING
    assert "neu anmelden" in item.detail
    assert item.fix == ""


def test_root_needs_no_group() -> None:
    assert check_group(host(root=True), "uucp").status is Status.OK


# --- Zugriff auf den Port -------------------------------------------------------------


def test_accessible_port() -> None:
    item = check_port_access(host(), CH340)
    assert item == CheckItem(Status.OK, "/dev/ttyUSB0 (USB Serial (QinHeng)): Zugriff erlaubt")


def test_inaccessible_port_names_the_device_group() -> None:
    # Gruppe vom Gerät, nicht aus der Distribution: Debian kennt auch uucp
    item = check_port_access(host(access=False, device_group="dialout"), CH340)
    assert item.status is Status.ERROR
    assert "Gruppe dialout" in item.title
    assert item.fix == "sudo usermod -aG dialout $USER"


def test_inaccessible_port_after_adding_the_group() -> None:
    h = host(access=False, members={"uucp": frozenset({"philipp"})})
    item = check_port_access(h, CH340)
    assert item.status is Status.ERROR
    assert item.fix == ""  # eingetragen ist er schon; es fehlt nur die neue Anmeldung
    assert "neu anmelden" in item.detail


def test_missing_device() -> None:
    item = check_port_access(host(access=False, device_group=None), PortInfo("/dev/pts/9", "x"))
    assert item.status is Status.ERROR
    assert item.title == "/dev/pts/9 (x): Gerät nicht vorhanden"


# --- Baudrate ausprobieren ------------------------------------------------------------


def test_probe_finds_adapter_at_38400_and_stops() -> None:
    opener = Opener({38400: elm, 115200: elm})
    adapter, item = probe_adapter("/dev/ttyUSB0", opener)
    assert adapter == FoundAdapter("/dev/ttyUSB0", 38400, "ELM327 v1.5")
    assert item.status is Status.OK
    assert item.title == "/dev/ttyUSB0: ELM327 v1.5 antwortet mit 38400 Baud"
    assert opener.sent() == [(38400, INIT)]  # nur Adapter-Befehle


def test_probe_falls_back_to_115200() -> None:
    opener = Opener({38400: Silent, 115200: elm})
    adapter, _item = probe_adapter("/dev/ttyUSB0", opener)
    assert adapter is not None and adapter.baud == 115200
    assert opener.sent() == [(38400, ["ATZ"]), (115200, INIT)]


def test_probe_without_answer() -> None:
    adapter, item = probe_adapter("/dev/ttyUSB0", Opener({38400: Silent, 115200: Silent}))
    assert adapter is None
    assert item.status is Status.ERROR
    assert item.title == "/dev/ttyUSB0: keine Antwort vom Adapter"
    assert "38400, 115200" in item.detail


def test_probe_with_something_that_is_no_elm327() -> None:
    def modem() -> FakeTransport:
        return FakeTransport({"ATZ": "OK"})

    adapter, item = probe_adapter("/dev/ttyACM0", Opener({38400: modem, 115200: modem}))
    assert adapter is None
    assert item.title == "/dev/ttyACM0: Antwort, aber nicht wie ein ELM327"


def test_probe_with_rejected_reset_tries_the_next_rate() -> None:
    def garbage() -> FakeTransport:
        return FakeTransport({"ATZ": "CAN ERROR"})

    adapter, _item = probe_adapter("/dev/ttyUSB0", Opener({38400: garbage, 115200: elm}))
    assert adapter is not None and adapter.baud == 115200


def test_probe_with_busy_port_stops() -> None:
    def busy() -> Transport:
        raise TransportError("/dev/ttyUSB0 lässt sich nicht öffnen (Device or resource busy)")

    opener = Opener({38400: busy, 115200: elm})
    adapter, item = probe_adapter("/dev/ttyUSB0", opener)
    assert adapter is None
    assert item.title == "/dev/ttyUSB0 lässt sich nicht benutzen"
    assert "Device or resource busy" in item.detail
    assert [baud for _p, baud, _t in opener.opened] == []  # open scheiterte schon


# --- alles zusammen -------------------------------------------------------------------


def run(h: Host, ports: list[PortInfo], opener: Opener, port: str | None = None) -> SetupReport:
    return run_setup_check(
        port,
        host=h,
        ports=lambda: ports,
        open_port=opener,
        gui_available=lambda: True,
        catalog_available=lambda: True,
    )


def test_ready() -> None:
    report = run(host(), [CH340], Opener({38400: elm, 115200: elm}))
    assert report.ok
    assert report.adapter == FoundAdapter("/dev/ttyUSB0", 38400, "ELM327 v1.5")
    assert [i.status for i in report.items] == [
        Status.OK,  # Oberfläche
        Status.OK,  # Katalog
        Status.OK,  # Zugriff
        Status.OK,  # Baudrate
        Status.INFO,  # Zündung, HS-CAN
    ]
    assert "HS-CAN" in report.items[-1].title


def test_no_permission_never_opens_the_port() -> None:
    opener = Opener({38400: elm, 115200: elm})
    report = run(host(access=False), [CH340], opener)
    assert not report.ok and report.adapter is None
    assert opener.opened == []
    assert any(i.fix == "sudo usermod -aG uucp $USER" for i in report.items)


def test_no_adapter_checks_the_group_of_the_distribution() -> None:
    report = run(host(distribution=frozenset({"ubuntu", "debian"})), [], Opener({}))
    titles = [i.title for i in report.items]
    assert "Kein Adapter gefunden" in titles
    assert "Nutzer philipp ist nicht in der Gruppe dialout" in titles


def test_no_adapter_mentions_brltty_when_installed() -> None:
    report = run(host(programs=frozenset({"brltty"})), [], Opener({}))
    (missing,) = [i for i in report.items if i.title == "Kein Adapter gefunden"]
    assert "brltty" in missing.detail


def test_given_port_is_checked_even_if_unknown() -> None:
    opener = Opener({38400: elm, 115200: elm})
    report = run(host(), [CH340], opener, port="/dev/pts/5")
    assert report.adapter == FoundAdapter("/dev/pts/5", 38400, "ELM327 v1.5")
    assert [p for p, _b, _t in opener.opened] == ["/dev/pts/5"]


def test_missing_software_is_reported() -> None:
    report = run_setup_check(
        host=host(),
        ports=lambda: [],
        open_port=Opener({}),
        gui_available=lambda: False,
        catalog_available=lambda: False,
    )
    fixes = [i.fix for i in report.items]
    assert "pip install 'obd-diag[gui]'" in fixes
    assert "uv run python tools/build_dtc_db.py" in fixes


def test_current_host_is_plausible() -> None:
    current = Host.current()
    assert current.user
    assert current.process_groups


# --- Kommandozeile --------------------------------------------------------------------

READY = SetupReport(
    [
        CheckItem(Status.OK, "Oberfläche (PySide6) installiert"),
        CheckItem(Status.ERROR, "Fehlercode-Katalog fehlt", "Text", "befehl"),
    ],
    [FoundAdapter("/dev/ttyUSB0", 115200, "ELM327 v1.5")],
)


def test_cli_setup_check(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ports: list[str | None] = []

    def fake(port: str | None) -> SetupReport:
        ports.append(port)
        return replace(READY, items=READY.items[:1])

    monkeypatch.setattr(cli, "run_setup_check", fake)
    assert cli.main(["setup-check", "--port", "/dev/ttyUSB0"]) == 0
    assert ports == ["/dev/ttyUSB0"]
    out = capsys.readouterr().out
    assert "  OK       Oberfläche (PySide6) installiert\n" in out
    assert "obd-diag diagnose --port /dev/ttyUSB0 --baud 115200" in out


def test_cli_setup_check_shows_fix_and_fails_without_adapter(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "run_setup_check", lambda port: replace(READY, adapters=[]))
    assert cli.main(["setup-check"]) == 1
    out = capsys.readouterr().out
    assert "  Fehler   Fehlercode-Katalog fehlt\n           Text\n" in out
    assert "Abhilfe: befehl" in out
    assert "kein nutzbarer Adapter" in out


def test_cli_setup_check_english(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "run_setup_check", lambda port: READY)
    assert cli.main(["--lang", "en", "setup-check"]) == 0
    out = capsys.readouterr().out
    assert "  Error    " in out
    assert "not everything is set up" in out
