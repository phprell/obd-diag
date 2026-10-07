import dataclasses
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from obd_diag import __version__
from obd_diag.services.diagnostics import scan_to_dict
from obd_diag.services.session import (
    Session,
    default_session_dir,
    load_session,
    save_session,
    session_from_dict,
    session_to_dict,
)
from tests.samples import FREEZE_FRAME, READINESS, VEHICLE, full_session, minimal_session

# --- Ablageordner ---


def test_default_session_dir_honours_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert default_session_dir() == tmp_path / "obd-diag" / "sessions"


@pytest.mark.parametrize("value", [None, "relativ/pfad"])
def test_default_session_dir_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: str | None
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    if value is None:
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    else:
        monkeypatch.setenv("XDG_DATA_HOME", value)
    assert default_session_dir() == tmp_path / ".local" / "share" / "obd-diag" / "sessions"


# --- dict/JSON-Rundreise ---


def _variants() -> list[Session]:
    """Jede optionale Angabe einmal gesetzt und einmal nicht."""
    full = full_session()
    return [
        full,
        minimal_session(),
        dataclasses.replace(full, readiness=None),
        dataclasses.replace(full, freeze_frame=None),
        dataclasses.replace(full, vehicle=None),
        dataclasses.replace(minimal_session(), readiness=READINESS),
        dataclasses.replace(minimal_session(), freeze_frame=FREEZE_FRAME),
        dataclasses.replace(minimal_session(), vehicle=VEHICLE),
    ]


@pytest.mark.parametrize("session", _variants())
def test_dict_round_trip(session: Session) -> None:
    data = session_to_dict(session)
    # über echtes JSON, damit Tupel zu Listen werden wie beim Speichern
    restored = session_from_dict(json.loads(json.dumps(data)))
    assert restored == session
    assert restored.created.utcoffset() == session.created.utcoffset()


def test_dict_round_trip_minimal_vin_and_freeze_fields() -> None:
    vehicle = dataclasses.replace(
        VEHICLE, valid=False, checksum_ok=False, manufacturer=None, country=None, model_year=None
    )
    freeze = dataclasses.replace(FREEZE_FRAME, dtc=None, raw={}, values={})
    session = dataclasses.replace(minimal_session(), vehicle=vehicle, freeze_frame=freeze)
    assert session_from_dict(json.loads(json.dumps(session_to_dict(session)))) == session


def test_dict_layout() -> None:
    session = full_session()
    data = session_to_dict(session)
    assert data["format"] == "obd-diag-session"
    assert data["version"] == 1
    assert data["created"] == "2026-10-07T14:32:05+02:00"
    assert data["scan"] == scan_to_dict(session.scan)  # dieselbe Form wie scan --json
    assert data["readiness"]["ready"] is False
    assert data["readiness"]["monitors"][3] == {
        "key": "catalyst",
        "name": "Katalysator",
        "state": "incomplete",
    }
    assert data["freeze_frame"]["values"]["rpm"] == 2140.0
    assert data["vehicle"]["vin"] == "WVWZZZ1KZ6W123456"
    empty = session_to_dict(minimal_session())
    assert empty["readiness"] is None
    assert empty["freeze_frame"] is None
    assert empty["vehicle"] is None


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"format": "etwas-anderes"}, "Keine obd-diag-Diagnosesitzung"),
        ({"format": None}, "Keine obd-diag-Diagnosesitzung"),
        ({"version": 2}, "diese obd-diag-Version kennt nur bis 1"),
        ({"version": "1"}, "Unbekannte Format-Version"),
        ({"scan": None}, "unvollständig oder beschädigt"),
        ({"created": "gestern"}, "unvollständig oder beschädigt"),
        ({"readiness": {"mil_on": True}}, "unvollständig oder beschädigt"),
    ],
)
def test_from_dict_rejects(change: dict[str, Any], message: str) -> None:
    data = session_to_dict(full_session()) | change
    with pytest.raises(ValueError, match=message):
        session_from_dict(data)


def test_from_dict_rejects_unknown_kind() -> None:
    data = session_to_dict(full_session())
    data["scan"]["codes"][0]["kind"] = "geheim"
    with pytest.raises(ValueError, match="beschädigt"):
        session_from_dict(data)


# --- Speichern und Laden ---


def test_save_and_load(tmp_path: Path) -> None:
    session = full_session()
    path = save_session(session, tmp_path / "neu")
    assert path == tmp_path / "neu" / "session-20261007-143205.json"
    assert load_session(path) == session
    assert [p.name for p in path.parent.iterdir()] == [path.name]  # keine Reste
    assert "Katalysatorwirkungsgrad" in path.read_text(encoding="utf-8")  # ensure_ascii=False


def test_save_never_overwrites(tmp_path: Path) -> None:
    session = full_session()
    first = save_session(session, tmp_path)
    first.write_text("vorher", encoding="utf-8")
    second = save_session(session, tmp_path)
    third = save_session(session, tmp_path)
    assert [second.name, third.name] == [
        "session-20261007-143205-2.json",
        "session-20261007-143205-3.json",
    ]
    assert first.read_text(encoding="utf-8") == "vorher"
    assert load_session(third) == session


def test_save_default_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    path = save_session(minimal_session())
    assert path.parent == tmp_path / "obd-diag" / "sessions"
    assert path.name == "session-20260102-030405.json"


def test_save_uses_local_timestamp_of_session(tmp_path: Path) -> None:
    session = dataclasses.replace(
        minimal_session(), created=datetime.fromisoformat("2026-03-04T23:59:58-05:00")
    )
    assert save_session(session, tmp_path).name == "session-20260304-235958.json"


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("kein json", "kein gültiges JSON"),
        ("[1, 2]", "Keine obd-diag-Diagnosesitzung"),
        ('{"adapter": "ELM327", "codes": []}', "Keine obd-diag-Diagnosesitzung"),
    ],
)
def test_load_rejects_foreign_files(tmp_path: Path, content: str, message: str) -> None:
    path = tmp_path / "fremd.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match=message) as info:
        load_session(path)
    assert str(path) in str(info.value)


def test_load_rejects_clear_backup(tmp_path: Path) -> None:
    """Eine Löschsicherung (dtc-backup-*.json) ist keine Sitzung."""
    path = tmp_path / "dtc-backup.json"
    backup = {"created": "2026-01-02T03:04:05+00:00", "scan": scan_to_dict(full_session().scan)}
    path.write_text(json.dumps(backup), encoding="utf-8")
    with pytest.raises(ValueError, match="Keine obd-diag-Diagnosesitzung"):
        load_session(path)


def test_dict_has_tool_version() -> None:
    assert session_to_dict(minimal_session())["tool_version"] == __version__


def test_from_dict_rejects_with_exact_message() -> None:
    with pytest.raises(ValueError) as info:
        session_from_dict({"format": "x"})
    assert str(info.value) == (
        "Keine obd-diag-Diagnosesitzung (Kennung 'format' fehlt oder ist falsch)."
    )


def test_from_dict_tolerates_missing_optional_lists() -> None:
    """Fehlende Listen (Ursachen, Symptome, Codes, Rohwerte, Online-Daten) sind leer."""
    data = session_to_dict(full_session())
    del data["scan"]["codes"][0]["info"]["causes"]
    del data["scan"]["codes"][0]["info"]["symptoms"]
    for key in ("raw", "values"):
        del data["freeze_frame"][key]
    del data["vehicle"]["online"]
    session = session_from_dict(data)
    info = session.scan.codes[0].info
    assert info is not None and info.causes == () and info.symptoms == ()
    assert session.freeze_frame is not None
    assert session.freeze_frame.raw == {} and session.freeze_frame.values == {}
    assert session.vehicle is not None and session.vehicle.online == {}

    del data["scan"]["codes"]
    assert session_from_dict(data).scan.codes == []


def test_round_trip_diesel_readiness() -> None:
    readiness = dataclasses.replace(READINESS, compression_ignition=True)
    session = dataclasses.replace(minimal_session(), readiness=readiness)
    restored = session_from_dict(json.loads(json.dumps(session_to_dict(session))))
    assert restored.readiness is not None and restored.readiness.compression_ignition is True


def test_load_utf8_independent_of_locale(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Auch unter einer Latin-1-Locale werden Umlaute richtig gelesen."""
    path = save_session(full_session(), tmp_path)
    real_read_text = Path.read_text

    def latin1_read_text(self: Path, encoding: str | None = None, errors: str | None = None) -> str:
        return real_read_text(self, encoding=encoding or "latin-1", errors=errors)

    monkeypatch.setattr(Path, "read_text", latin1_read_text)
    assert load_session(path) == full_session()
