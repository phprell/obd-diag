"""Ablage unter ``$XDG_DATA_HOME/obd-diag`` (Sicherungen, Sitzungen, Mitschnitte)."""

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def data_dir() -> Path:
    """``$XDG_DATA_HOME/obd-diag`` (Standard: ~/.local/share/obd-diag)."""
    data_home = os.environ.get("XDG_DATA_HOME", "")
    # Laut XDG-Spezifikation gelten nur absolute Pfade.
    base = Path(data_home) if os.path.isabs(data_home) else Path.home() / ".local" / "share"
    return base / "obd-diag"


def trace_dir() -> Path:
    """Ablage der Adapter-Mitschnitte (``obd-diag ... --trace``)."""
    return data_dir() / "traces"


def write_new_json(directory: Path, stem: str, data: dict[str, Any]) -> Path:
    """Schreibt ``data`` atomar als ``<stem>.json`` (belegt: ``<stem>-2.json`` usw.).

    Vorhandene Dateien werden nie überschrieben, und unter dem endgültigen Namen
    erscheint nur eine vollständig geschriebene Datei.
    """
    directory.mkdir(parents=True, exist_ok=True)
    content = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(dir=directory, prefix=f".{stem}-", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        for n in range(1, 1000):
            path = directory / (f"{stem}.json" if n == 1 else f"{stem}-{n}.json")
            try:
                # link() legt den Namen nur an, wenn er frei ist; die Datei ist dann
                # schon vollständig geschrieben.
                os.link(tmp, path)
            except FileExistsError:
                continue
            return path
        raise FileExistsError(f"kein freier Dateiname für {stem} in {directory}")
    finally:
        tmp.unlink(missing_ok=True)
