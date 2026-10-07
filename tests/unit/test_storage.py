"""Ablage: XDG-Ordner und atomares Schreiben ohne Überschreiben."""

import json
import os
from pathlib import Path
from typing import IO, Any

import pytest

from obd_diag.services.storage import data_dir, trace_dir, write_new_json

DATA = {"titel": "Katalysatorwirkungsgrad unter Schwellwert", "werte": [1, 2.5, None]}


def test_data_dir_honours_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert data_dir() == tmp_path / "obd-diag"
    assert trace_dir() == tmp_path / "obd-diag" / "traces"


def test_writes_readable_utf8_json(tmp_path: Path) -> None:
    data = DATA | {"ä": "Zündung, Kühlmittel"}
    path = write_new_json(tmp_path / "neu" / "tief", "datei", data)
    assert path == tmp_path / "neu" / "tief" / "datei.json"
    # Umlaute bleiben lesbar (kein ä), zwei Leerzeichen Einzug, Zeilenende
    assert path.read_bytes() == (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode()
    assert "Zündung" in path.read_text(encoding="utf-8")
    assert '\n  "titel": ' in path.read_text(encoding="utf-8")


def test_utf8_independent_of_locale(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Auch unter LANG=C (ASCII-Locale) wird UTF-8 geschrieben."""
    real_fdopen = os.fdopen

    def c_locale_fdopen(fd: int, *args: Any, **kwargs: Any) -> IO[Any]:
        kwargs["encoding"] = kwargs.get("encoding") or "ascii"
        file: IO[Any] = real_fdopen(fd, *args, **kwargs)
        return file

    monkeypatch.setattr(os, "fdopen", c_locale_fdopen)
    path = write_new_json(tmp_path, "datei", {"text": "Zündung"})
    assert json.loads(path.read_text(encoding="utf-8")) == {"text": "Zündung"}


def test_only_complete_file_under_final_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Während geschrieben wird, gibt es nur die versteckte Zwischendatei im Zielordner."""
    seen: list[list[str]] = []
    real_fsync = os.fsync

    def spy(fd: int) -> None:
        seen.append(sorted(p.name for p in tmp_path.iterdir()))
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", spy)
    path = write_new_json(tmp_path, "sicherung", DATA)
    ((tmp_name,),) = seen
    assert tmp_name.startswith(".sicherung-") and tmp_name.endswith(".tmp")
    assert [p.name for p in tmp_path.iterdir()] == [path.name]  # Zwischendatei weg


def test_never_overwrites(tmp_path: Path) -> None:
    (tmp_path / "x.json").write_text("alt", encoding="utf-8")
    (tmp_path / "x-2.json").write_text("alt", encoding="utf-8")
    assert write_new_json(tmp_path, "x", DATA) == tmp_path / "x-3.json"
    assert (tmp_path / "x.json").read_text(encoding="utf-8") == "alt"
    assert (tmp_path / "x-2.json").read_text(encoding="utf-8") == "alt"


def test_gives_up_after_999_names(tmp_path: Path) -> None:
    (tmp_path / "x.json").touch()
    for n in range(2, 1000):
        (tmp_path / f"x-{n}.json").touch()
    with pytest.raises(FileExistsError, match=f"kein freier Dateiname für x in {tmp_path}"):
        write_new_json(tmp_path, "x", DATA)
    assert not (tmp_path / "x-1000.json").exists()
    assert len(list(tmp_path.iterdir())) == 999  # keine Zwischendatei liegen geblieben
