"""Auslieferung: Desktop-Eintrag, AppStream, udev-Regel, AUR- und Flatpak-Vorlage.

Gebaut und installiert werden die Pakete in ``.github/workflows/packaging.yml``; hier wird
geprüft, was ohne Paketwerkzeuge geht: Versionen passen zusammen, die Dateien liegen im
Paket, und ``obd-diag-gui --install-desktop`` legt einen gültigen Eintrag an.
"""

import re
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import yaml

import obd_diag
from obd_diag.desktop import (
    APP_ID,
    entry_paths,
    install_desktop_entry,
    quote_exec,
    remove_desktop_entry,
    share_file,
)
from obd_diag.ui import app

ROOT = Path(__file__).resolve().parents[2]
SHARE = ROOT / "src" / "obd_diag" / "share"


def _project_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as f:
        version: str = tomllib.load(f)["project"]["version"]
    return version


def test_metainfo_lists_every_release_from_the_changelog() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    released = re.findall(r"^## (\d+\.\d+\.\d+) – (\d{4}-\d\d-\d\d)", changelog, re.MULTILINE)  # noqa: RUF001
    root = ET.parse(SHARE / f"{APP_ID}.metainfo.xml").getroot()
    listed = [(r.get("version"), r.get("date")) for r in root.iter("release")]
    # Neueste zuerst, wie im CHANGELOG
    assert listed == released
    assert root.findtext("id") == APP_ID
    assert root.find("launchable").text == f"{APP_ID}.desktop"  # type: ignore[union-attr]


def test_desktop_file_in_both_languages() -> None:
    text = share_file(f"{APP_ID}.desktop")
    for key in ("Name", "GenericName", "Comment", "Keywords"):
        assert re.search(rf"^{key}=\S", text, re.MULTILINE), key
        assert re.search(rf"^{key}\[de\]=\S", text, re.MULTILINE), key
    assert "\nExec=obd-diag-gui\n" in text
    assert f"\nIcon={APP_ID}\n" in text


def test_udev_rule_grants_access_to_the_logged_in_user_only() -> None:
    rules = [path.name for path in SHARE.glob("*.rules")]
    assert rules == ["70-obd-diag.rules"]
    # uaccess wird in 73-seat-late.rules ausgewertet: die Regel muss davor laufen
    assert int(rules[0].split("-")[0]) < 73
    lines = [
        line
        for line in (SHARE / rules[0]).read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    assert len(lines) == 3
    for line in lines:
        assert line.startswith('SUBSYSTEM=="tty", ATTRS{idVendor}==')
        # weder Modus noch Gruppe ändern: das bleibt Sache der Distribution
        assert line.endswith(', TAG+="uaccess"')
        assert "MODE" not in line and "GROUP" not in line and "RUN" not in line


def test_share_files_ship_in_the_package() -> None:
    package = Path(obd_diag.__file__).parent / "share"
    names = {path.name for path in package.iterdir()}
    assert {
        f"{APP_ID}.desktop",
        f"{APP_ID}.metainfo.xml",
        f"{APP_ID}.svg",
        "70-obd-diag.rules",
    } <= names


def test_pkgbuild_template_matches_the_project() -> None:
    text = (ROOT / "packaging" / "aur" / "PKGBUILD").read_text(encoding="utf-8")
    assert re.search(rf"^pkgver={re.escape(_project_version())}$", text, re.MULTILINE)
    assert "releases/download/v$pkgver/obd_diag-$pkgver.tar.gz" in text
    # Die udev-Regel gehört nach /usr/lib, /etc bleibt dem Nutzer
    assert '"$pkgdir/usr/lib/udev/rules.d/"' in text


def _locked_versions() -> dict[str, str]:
    with (ROOT / "uv.lock").open("rb") as f:
        lock = tomllib.load(f)
    return {pkg["name"]: pkg["version"] for pkg in lock["package"]}


def test_flatpak_wheels_match_the_lockfile() -> None:
    manifest = yaml.safe_load(
        (ROOT / "packaging" / "flatpak" / f"{APP_ID}.yml").read_text(encoding="utf-8")
    )
    assert manifest["id"] == APP_ID
    assert manifest["command"] == "obd-diag-gui"
    deps = next(m for m in manifest["modules"] if m["name"] == "python3-dependencies")
    locked = _locked_versions()
    shipped = set()
    for source in deps["sources"]:
        wheel = source["url"].rsplit("/", 1)[1]
        name, version = wheel.split("-")[:2]
        name = name.replace("_", "-").lower()
        assert locked[name] == version, wheel
        assert re.fullmatch(r"[0-9a-f]{64}", source["sha256"])
        shipped.add(name)
    # alle Laufzeit-Abhängigkeiten außer PySide6 (kommt aus der BaseApp)
    assert shipped == {"pyserial", "reportlab", "pillow", "charset-normalizer"}


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/home/a/.local/bin/obd-diag-gui", "/home/a/.local/bin/obd-diag-gui"),
        ("/home/a b/bin/gui", '"/home/a b/bin/gui"'),
        ('/x/"q"', '"/x/\\\\"q\\\\""'),
        ("/x/$HOME", '"/x/\\\\$HOME"'),
        ("/x/back\\slash", '"/x/back\\\\\\\\slash"'),
        ("/x/100%", "/x/100%%"),
    ],
)
def test_quote_exec(path: str, expected: str) -> None:
    assert quote_exec(path) == expected


def test_install_and_remove_desktop_entry(tmp_path: Path) -> None:
    created = install_desktop_entry("/opt/my tools/obd-diag-gui", tmp_path)
    desktop, icon = entry_paths(tmp_path)
    assert created == [desktop, icon]
    assert desktop == tmp_path / "applications" / f"{APP_ID}.desktop"
    text = desktop.read_text(encoding="utf-8")
    assert '\nExec="/opt/my tools/obd-diag-gui"\n' in text
    assert text.count("Exec=") == 1
    assert icon.read_text(encoding="utf-8") == share_file(f"{APP_ID}.svg")
    # erneut: ersetzt den Eintrag (neuer Pfad)
    install_desktop_entry("/usr/bin/obd-diag-gui", tmp_path)
    assert "\nExec=/usr/bin/obd-diag-gui\n" in desktop.read_text(encoding="utf-8")
    assert remove_desktop_entry(tmp_path) == [desktop, icon]
    assert not desktop.exists() and not icon.exists()
    assert remove_desktop_entry(tmp_path) == []


def test_gui_installs_desktop_entry_without_starting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("LANGUAGE", "en")
    assert app.main(["bin/obd-diag-gui", "--install-desktop"]) == 0
    desktop = tmp_path / "applications" / f"{APP_ID}.desktop"
    absolute = Path("bin/obd-diag-gui").absolute()
    assert f"\nExec={absolute}\n" in desktop.read_text(encoding="utf-8")
    assert f"Created: {desktop}" in capsys.readouterr().out
    assert app.main(["obd-diag-gui", "--remove-desktop"]) == 0
    assert f"Removed: {desktop}" in capsys.readouterr().out
    assert app.main(["obd-diag-gui", "--remove-desktop"]) == 0
    assert "No start menu entry" in capsys.readouterr().out
