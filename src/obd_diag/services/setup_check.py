"""Einrichtung am PC prüfen: Adapter gefunden, Zugriffsrechte, Baudrate, Zubehör.

Für den ersten Start (Oberfläche) und ``obd-diag setup-check``. Geprüft wird die
Umgebung am PC, nicht das Fahrzeug:

- Oberfläche (PySide6) und Fehlercode-Katalog installiert,
- angeschlossene Adapter (USB-Seriell, ``/dev/rfcomm*``) und ob der Nutzer sie öffnen
  darf; sonst die Gruppe, in die er gehört (``uucp`` auf Arch, ``dialout`` auf
  Debian/Ubuntu/Fedora), mit dem Befehl zum Eintragen,
- mit welcher Baudrate der Adapter antwortet (38400, dann 115200). Gesendet werden dafür
  nur die Adapter-Befehle von ``Elm327.initialize`` (``ATZ`` …); nichts davon erreicht
  das Fahrzeug.

Version, Spannung und Befehlsumfang des Adapters prüft dieses Modul nicht.
"""

import grp
import importlib.util
import os
import pwd
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path

from obd_diag.data.dtc_catalog import DEFAULT_PATH as CATALOG_PATH
from obd_diag.i18n import N_, tr
from obd_diag.protocol.elm327 import Elm327, ElmError
from obd_diag.transport import Transport, TransportError, TransportTimeout
from obd_diag.transport.discovery import PortInfo, list_ports
from obd_diag.transport.serial import SerialTransport

# Übliche Baudraten von ELM327-Adaptern: 38400 ist der Auslieferungszustand des
# Originals (Datenblatt S. 7, „PP 0C“), viele Klone und FORScan-Adapter laufen mit 115200.
BAUD_RATES = (38400, 115200)
# Der ELM327 antwortet auf ``ATZ`` nach etwa einer Sekunde; bei falscher Baudrate kommt
# kein Prompt. Kurz halten, sonst dauert jede falsche Baudrate die vollen 10 s.
PROBE_TIMEOUT = 3.0

# Gruppe der seriellen Geräte je Distribution (ID bzw. ID_LIKE aus /etc/os-release)
_SERIAL_GROUPS = {
    "arch": "uucp",
    "debian": "dialout",
    "ubuntu": "dialout",
    "fedora": "dialout",
    "rhel": "dialout",
    "suse": "dialout",
}
DEFAULT_SERIAL_GROUP = "dialout"

IGNITION_HINT = N_(
    "Vor dem Verbinden am Auto: Zündung einschalten (der Motor darf aus bleiben). Bei "
    "Adaptern mit MS-/HS-CAN-Schalter HS-CAN wählen."
)


class Status(StrEnum):
    OK = "ok"
    WARNING = "warning"
    ERROR = "error"
    INFO = "info"


@dataclass(frozen=True)
class CheckItem:
    """Ein Prüfpunkt; Texte in der beim Prüfen eingestellten Sprache."""

    status: Status
    title: str
    detail: str = ""
    fix: str = ""  # Befehl zum Abhelfen, z. B. ``sudo usermod -aG uucp $USER``


@dataclass(frozen=True)
class FoundAdapter:
    port: str
    baud: int
    identification: str  # letzte Zeile der ATZ-Antwort, z. B. ``ELM327 v1.5``


@dataclass(frozen=True)
class SetupReport:
    items: list[CheckItem]
    adapters: list[FoundAdapter] = field(default_factory=list)

    @property
    def adapter(self) -> FoundAdapter | None:
        """Der erste antwortende Adapter, mit dem sich verbinden lässt."""
        return self.adapters[0] if self.adapters else None

    @property
    def ok(self) -> bool:
        return self.adapter is not None and not any(
            item.status is Status.ERROR for item in self.items
        )


@dataclass(frozen=True)
class Host:
    """Was die Prüfung vom Rechner wissen muss; in Tests ersetzbar (``Host.current``)."""

    user: str
    is_root: bool
    process_groups: frozenset[str]  # Gruppen dieses Prozesses (gelten seit der Anmeldung)
    group_members: Callable[[str], frozenset[str] | None]  # None: Gruppe gibt es nicht
    device_group: Callable[[str], str | None]
    can_access: Callable[[str], bool]  # lesen und schreiben
    distribution: frozenset[str]  # ID und ID_LIKE aus /etc/os-release
    has_program: Callable[[str], bool]

    @classmethod
    def current(cls) -> "Host":
        return cls(
            user=_user_name(),
            is_root=os.geteuid() == 0,
            process_groups=frozenset(_group_name(g) for g in {os.getegid(), *os.getgroups()}),
            group_members=_group_members,
            device_group=_device_group,
            can_access=lambda path: os.access(path, os.R_OK | os.W_OK),
            distribution=_distribution(Path("/etc/os-release")),
            has_program=lambda name: shutil.which(name) is not None,
        )


def _user_name() -> str:
    try:
        return pwd.getpwuid(os.getuid()).pw_name
    except KeyError:
        return os.environ.get("USER", "")


def _group_name(gid: int) -> str:
    try:
        return grp.getgrgid(gid).gr_name
    except KeyError:
        return str(gid)


def _group_members(name: str) -> frozenset[str] | None:
    """Eingetragene Mitglieder (/etc/group) samt Nutzern mit dieser Hauptgruppe."""
    try:
        group = grp.getgrnam(name)
    except KeyError:
        return None
    primary = {user.pw_name for user in pwd.getpwall() if user.pw_gid == group.gr_gid}
    return frozenset({*group.gr_mem, *primary})


def _device_group(path: str) -> str | None:
    try:
        return _group_name(os.stat(path).st_gid)
    except OSError:
        return None


def _distribution(os_release: Path) -> frozenset[str]:
    try:
        text = os_release.read_text(encoding="utf-8")
    except OSError:
        return frozenset()
    ids: set[str] = set()
    for line in text.splitlines():
        key, _, value = line.partition("=")
        if key in ("ID", "ID_LIKE"):
            ids.update(value.strip().strip("\"'").lower().split())
    return frozenset(ids)


def serial_group(distribution: frozenset[str]) -> str:
    """Gruppe, der serielle Geräte auf dieser Distribution gehören."""
    for name, group in _SERIAL_GROUPS.items():
        if name in distribution:
            return group
    return DEFAULT_SERIAL_GROUP


def add_to_group_command(group: str) -> str:
    return f"sudo usermod -aG {group} $USER"


def _gui_available() -> bool:
    return importlib.util.find_spec("PySide6") is not None


def _catalog_available() -> bool:
    return CATALOG_PATH.exists()


def _open_serial(port: str, baud: int) -> Transport:
    return SerialTransport(port, baud)


# --- einzelne Prüfungen -------------------------------------------------------


def check_software(gui_available: bool, catalog_available: bool) -> list[CheckItem]:
    items = []
    if gui_available:
        items.append(CheckItem(Status.OK, tr("Oberfläche (PySide6) installiert")))
    else:
        items.append(
            CheckItem(
                Status.INFO,
                tr("Oberfläche (PySide6) nicht installiert"),
                tr("Die Kommandozeile funktioniert auch ohne."),
                "pip install 'obd-diag[gui]'",
            )
        )
    if catalog_available:
        items.append(CheckItem(Status.OK, tr("Fehlercode-Katalog vorhanden")))
    else:
        items.append(
            CheckItem(
                Status.WARNING,
                tr("Fehlercode-Katalog fehlt"),
                tr("Fehlercodes werden ohne Beschreibung angezeigt."),
                "uv run python tools/build_dtc_db.py",
            )
        )
    return items


def check_group(host: Host, group: str) -> CheckItem:
    """Ist der Nutzer in ``group``? Eingetragen, aber noch nicht neu angemeldet, zählt
    als Warnung: die Rechte gelten erst nach der nächsten Anmeldung."""
    if host.is_root:
        return CheckItem(Status.OK, tr("Als root: Zugriff auf alle Geräte"))
    if group in host.process_groups:
        return CheckItem(
            Status.OK,
            tr("Nutzer {user} ist in der Gruppe {group}").format(user=host.user, group=group),
        )
    members = host.group_members(group)
    if members is not None and host.user in members:
        return CheckItem(
            Status.WARNING,
            tr("Nutzer {user} ist in der Gruppe {group}, aber noch nicht neu angemeldet").format(
                user=host.user, group=group
            ),
            tr("Abmelden und neu anmelden (oder neu starten), dann gilt die Gruppe."),
        )
    return CheckItem(
        Status.ERROR,
        tr("Nutzer {user} ist nicht in der Gruppe {group}").format(user=host.user, group=group),
        tr(
            "Ohne die Gruppe (oder die udev-Regel aus packaging/) darf das Programm den "
            "Adapter nicht öffnen. Nach dem Eintragen abmelden und neu anmelden."
        ),
        add_to_group_command(group),
    )


def check_port_access(host: Host, port: PortInfo) -> CheckItem:
    name = f"{port.device} ({port.description})"
    if host.can_access(port.device):
        return CheckItem(Status.OK, tr("{port}: Zugriff erlaubt").format(port=name))
    group = host.device_group(port.device)
    if group is None:
        return CheckItem(Status.ERROR, tr("{port}: Gerät nicht vorhanden").format(port=name))
    membership = check_group(host, group)
    if membership.status is Status.WARNING:  # eingetragen, Anmeldung fehlt noch
        return CheckItem(
            Status.ERROR,
            tr("{port}: kein Zugriff").format(port=name),
            membership.detail,
        )
    return CheckItem(
        Status.ERROR,
        tr("{port}: kein Zugriff (Gerät gehört der Gruppe {group})").format(port=name, group=group),
        tr("Nutzer in die Gruppe eintragen, danach abmelden und neu anmelden."),
        add_to_group_command(group),
    )


def no_adapter_item(host: Host) -> CheckItem:
    detail = tr(
        "USB-Adapter einstecken und die Prüfung wiederholen. Bluetooth-Adapter "
        "erst koppeln und mit rfcomm bind als /dev/rfcomm0 einbinden."
    )
    if host.has_program("brltty"):
        detail += " " + tr(
            "brltty (Braillezeilen) ist installiert und belegt oft Adapter mit CH340/CH341: "
            "falls keine Braillezeile genutzt wird, brltty deinstallieren."
        )
    return CheckItem(Status.ERROR, tr("Kein Adapter gefunden"), detail)


def probe_adapter(
    port: str,
    open_port: Callable[[str, int], Transport] = _open_serial,
    baud_rates: Sequence[int] = BAUD_RATES,
    timeout: float = PROBE_TIMEOUT,
) -> tuple[FoundAdapter | None, CheckItem]:
    """Probiert die Baudraten der Reihe nach, bis ein ELM327 auf ``ATZ`` antwortet."""
    answered: list[str] = []  # Antworten, die nicht nach einem ELM327 aussahen
    for baud in baud_rates:
        try:
            with open_port(port, baud) as transport:
                identification = Elm327(transport, timeout=timeout).initialize()
        except TransportTimeout:
            continue  # falsche Baudrate: kein Prompt
        except TransportError as e:
            return None, CheckItem(
                Status.ERROR,
                tr("{port} lässt sich nicht benutzen").format(port=port),
                tr("{error}. Läuft ein anderes Programm mit dem Adapter?").format(error=e),
            )
        except ElmError as e:
            answered.append(str(e))
            continue
        if "ELM" in identification.upper():
            found = FoundAdapter(port, baud, identification)
            return found, CheckItem(
                Status.OK,
                tr("{port}: {adapter} antwortet mit {baud} Baud").format(
                    port=port, adapter=identification, baud=baud
                ),
            )
        answered.append(identification)
    rates = ", ".join(str(b) for b in baud_rates)
    if answered:
        return None, CheckItem(
            Status.ERROR,
            tr("{port}: Antwort, aber nicht wie ein ELM327").format(port=port),
            tr("Probiert mit {rates} Baud. Ist das ein OBD-Adapter?").format(rates=rates),
        )
    return None, CheckItem(
        Status.ERROR,
        tr("{port}: keine Antwort vom Adapter").format(port=port),
        tr(
            "Probiert mit {rates} Baud. Adapter neu einstecken; manche Adapter brauchen "
            "Strom aus der OBD-Buchse und antworten erst am Auto."
        ).format(rates=rates),
    )


# --- alles zusammen -----------------------------------------------------------


def run_setup_check(
    port: str | None = None,
    *,
    host: Host | None = None,
    ports: Callable[[], list[PortInfo]] = list_ports,
    open_port: Callable[[str, int], Transport] = _open_serial,
    gui_available: Callable[[], bool] = _gui_available,
    catalog_available: Callable[[], bool] = _catalog_available,
) -> SetupReport:
    """Prüft Software, Adapter, Zugriffsrechte und Baudrate; ohne ``port`` alle
    gefundenen Adapter, mit ``port`` nur diesen (auch wenn die Suche ihn nicht kennt,
    z. B. ein pty des Emulators)."""
    host = host or Host.current()
    items = check_software(gui_available(), catalog_available())
    group = serial_group(host.distribution)
    if port is None:
        found_ports = ports()
    else:
        known = {p.device: p for p in ports()}
        found_ports = [known.get(port, PortInfo(port, tr("angegeben")))]

    adapters: list[FoundAdapter] = []
    if not found_ports:
        items.append(no_adapter_item(host))
        # Ohne Gerät nur eine Warnung: die udev-Regel gäbe auch ohne Gruppe Zugriff
        membership = check_group(host, group)
        if membership.status is Status.ERROR:
            membership = replace(membership, status=Status.WARNING)
        items.append(membership)
    for info in found_ports:
        access = check_port_access(host, info)
        items.append(access)
        if access.status is not Status.OK:
            continue
        adapter, item = probe_adapter(info.device, open_port)
        items.append(item)
        if adapter is not None:
            adapters.append(adapter)
    items.append(CheckItem(Status.INFO, tr(IGNITION_HINT)))
    return SetupReport(items, adapters)
