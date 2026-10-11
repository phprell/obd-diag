"""Startmenü-Eintrag für Installationen ohne Paketverwaltung (pipx, pip).

AUR- und Flatpak-Paket legen Desktop-Datei und Symbol selbst ab; wer mit pipx installiert,
holt das mit ``obd-diag-gui --install-desktop`` nach. Die Dateien liegen im Paket unter
``obd_diag/share`` und sind dieselben, die die Pakete installieren.
"""

import re
from importlib.resources import files
from pathlib import Path

from obd_diag.services.storage import data_dir

APP_ID = "io.github.phprell.obd_diag"


def share_file(name: str) -> str:
    """Inhalt einer Datei aus ``obd_diag/share``."""
    return (files("obd_diag") / "share" / name).read_text(encoding="utf-8")


def entry_paths(base: Path | None = None) -> tuple[Path, Path]:
    """Ziel von Desktop-Datei und Symbol unter ``base`` (Standard: ``$XDG_DATA_HOME``)."""
    base = data_dir().parent if base is None else base
    return (
        base / "applications" / f"{APP_ID}.desktop",
        base / "icons" / "hicolor" / "scalable" / "apps" / f"{APP_ID}.svg",
    )


def quote_exec(path: str) -> str:
    """Programmpfad für die ``Exec``-Zeile (Desktop Entry Specification, "The Exec key").

    Mit Leer- oder Sonderzeichen in Anführungszeichen, darin ``"``, `````, ``$`` und ``\\``
    mit Backslash; ``%`` ist ein Feldcode und wird verdoppelt. Für die Desktop-Datei als
    String kommt noch einmal jeder Backslash doppelt dazu.
    """
    path = path.replace("%", "%%")
    if re.fullmatch(r"[A-Za-z0-9_./+@:,=%-]+", path):
        return path
    inner = re.sub(r'(["`$\\])', r"\\\1", path)
    return '"' + inner.replace("\\", "\\\\") + '"'


def install_desktop_entry(command: str, base: Path | None = None) -> list[Path]:
    """Legt Desktop-Datei und Symbol an; ``Exec`` zeigt auf ``command`` (absolut).

    Vorhandene Einträge werden ersetzt (etwa nach einem Umzug der Installation).
    """
    desktop, icon = entry_paths(base)
    text = re.sub(
        r"^Exec=.*$",
        lambda _: "Exec=" + quote_exec(command),
        share_file(f"{APP_ID}.desktop"),
        count=1,
        flags=re.MULTILINE,
    )
    for path, content in ((desktop, text), (icon, share_file(f"{APP_ID}.svg"))):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return [desktop, icon]


def remove_desktop_entry(base: Path | None = None) -> list[Path]:
    """Entfernt, was :func:`install_desktop_entry` angelegt hat; gibt die gelöschten Pfade."""
    removed = []
    for path in entry_paths(base):
        if path.exists():
            path.unlink()
            removed.append(path)
    return removed
