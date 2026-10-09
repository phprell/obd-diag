import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from obd_diag import __version__
from obd_diag.protocol.elm327 import Elm327, ElmError
from obd_diag.services import clear
from obd_diag.services.clear import (
    ClearRefused,
    check_preconditions,
    clear_codes,
    clearable_codes,
    default_backup_dir,
)
from obd_diag.services.diagnostics import DtcKind
from tests.fakes import CAN_CAR_ENGINE_OFF, CLEARED, FakeCatalog, FakeTransport

FREEZE_COMMANDS = ["020200", "020400", "020500", "020C00", "020D00"]


def _car(**overrides: str) -> FakeTransport:
    return FakeTransport(CAN_CAR_ENGINE_OFF | overrides, CLEARED)


# --- Sicherungsordner ---


def test_default_backup_dir_honours_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert default_backup_dir() == tmp_path / "obd-diag" / "backups"


@pytest.mark.parametrize("value", [None, "", "relativ/pfad"])
def test_default_backup_dir_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: str | None
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    if value is None:
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    else:
        monkeypatch.setenv("XDG_DATA_HOME", value)
    assert default_backup_dir() == tmp_path / ".local" / "share" / "obd-diag" / "backups"


# --- Vorbedingungen ---


def test_preconditions_met() -> None:
    transport = _car()
    check_preconditions(Elm327(transport))
    assert transport.sent == ["0100", "ATRV", "010C"]


def test_preconditions_without_voltage_reading() -> None:
    check_preconditions(Elm327(_car(ATRV="?")))


def test_preconditions_voltage_at_limit() -> None:
    # Genau 11,8 V gilt noch als ausreichend (gewarnt wird erst darunter).
    check_preconditions(Elm327(_car(ATRV="11.8V")))


def test_preconditions_low_adapter_voltage_confirmed_by_ecu() -> None:
    # Mercedes W177: Adapter 11,2 V, Motorsteuergerät 12,0 V (PID 42).
    transport = _car(ATRV="11.2V", **{"0142": "41422EDC"})
    check_preconditions(Elm327(transport))
    assert transport.sent == ["0100", "ATRV", "0142", "010C"]


@pytest.mark.parametrize("ecu", ["41422AF8", "NO DATA", "7F014212", "4142FFFF", "?"])
def test_preconditions_low_voltage_not_lifted_by_low_or_bad_ecu_value(ecu: str) -> None:
    transport = _car(ATRV="11.2V", **{"0142": ecu})
    with pytest.raises(ClearRefused, match="Bordspannung zu niedrig"):
        check_preconditions(Elm327(transport))
    assert transport.sent == ["0100", "ATRV", "0142"]


def test_preconditions_accept_answer_with_spaces() -> None:
    # Mit ATS1 (Standard nach ATZ) trennt der Adapter die Bytes durch Leerzeichen.
    check_preconditions(Elm327(_car(**{"0100": "41 00 BE 3F A8 13"})))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"0100": "SEARCHING...\rUNABLE TO CONNECT"}, "Keine Verbindung zum Steuergerät"),
        ({"0100": "NO DATA"}, r"Keine Verbindung zum Steuergerät \(NO DATA\)\. Ist die Zündung"),
        ({"0100": "OK"}, r"Keine Verbindung zum Steuergerät \(Antwort 'OK'\)\. Ist die Zündung"),
        (
            {"ATRV": "11.2V"},
            r"^Bordspannung zu niedrig \(11\.2 V, mindestens 11\.8 V\)\. "
            r"Batterie laden oder Ladegerät anschließen\.$",
        ),
        ({"ATRV": "11.79V"}, "Bordspannung zu niedrig"),
        ({"010C": "410C0AF0"}, r"Motor läuft \(700 1/min\)"),
        ({"010C": "410C0001"}, "Motor läuft"),
        (
            {"010C": "NO DATA"},
            r"^Drehzahl nicht lesbar, daher wird nicht gelöscht\. Motor aus, Zündung an\?$",
        ),
        ({"010C": "7F0112"}, "Drehzahl nicht lesbar"),
        ({"010C": "OK"}, "Drehzahl nicht lesbar"),
        ({"010C": "CAN ERROR"}, "Drehzahl nicht lesbar"),
    ],
)
def test_preconditions_refused(overrides: dict[str, str], message: str) -> None:
    transport = _car(**overrides)
    with pytest.raises(ClearRefused, match=message):
        check_preconditions(Elm327(transport))
    assert "04" not in transport.sent


# --- Löschen ---


def test_clear_codes_backs_up_then_clears(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    now = datetime(2026, 10, 7, 12, 30, 5, tzinfo=UTC)
    monkeypatch.setattr(clear, "_now", lambda: now)
    backup_seen: list[bool] = []
    transport = _car()
    write = transport.write

    def spy(data: bytes) -> None:
        # Beim Senden von Mode 04 muss die Sicherung schon vollständig da sein.
        if data.strip() == b"04":
            backup_seen.append((tmp_path / "dtc-backup-20261007-123005.json").is_file())
        write(data)

    monkeypatch.setattr(transport, "write", spy)
    catalog = FakeCatalog({"P0133": "Lambdasonde reagiert zu langsam"})

    result = clear_codes(Elm327(transport), catalog, backup_dir=tmp_path, lang="en")

    assert backup_seen == [True]
    assert transport.sent.count("04") == 1
    i = transport.sent.index("04")
    assert transport.sent[i - 8 : i] == ["0100", "ATRV", "010C", *FREEZE_COMMANDS]
    assert transport.sent[i + 1] == "ATZ"  # Kontroll-Scan
    assert [c.code for c in clearable_codes(result.before)] == ["P0133", "P0300", "P0171", "P0133"]
    assert result.after.codes == []
    assert result.backup_path == tmp_path / "dtc-backup-20261007-123005.json"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["dtc-backup-20261007-123005.json"]

    # Vor und nach dem Löschen wird in der gewünschten Sprache nachgeschlagen.
    assert catalog.lookups == [("P0133", "en"), ("P0300", "en"), ("P0171", "en")]

    data = json.loads(result.backup_path.read_text(encoding="utf-8"))
    assert data["created"] == "2026-10-07T12:30:05+00:00"
    assert data["tool_version"] == __version__
    assert data["adapter"] == "ELM327 v1.5"
    assert data["protocol"] == "ISO 15765-4 (CAN 11/500)"
    assert data["scan"]["voltage"] == 12.4
    assert data["scan"]["low_voltage"] is False
    assert data["scan"]["codes"][0] == {
        "code": "P0133",
        "kind": "stored",
        "info": {
            "code": "P0133",
            "title": "Lambdasonde reagiert zu langsam",
            "description": None,
            "causes": [],
            "symptoms": [],
            "mil": None,
            "emissions_relevant": None,
            "repair_difficulty": None,
            "cost_eur": None,
        },
        "online": None,
    }
    assert data["freeze_frame"] == {
        "dtc": "P0133",
        "raw": {
            "020200": "4202000133",
            "020500": "42050073",
            "020C00": "420C001AF8",
        },
        "values": {"coolant_temp_c": 75, "rpm": 1726.0},
    }


def test_backups_never_overwrite(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(clear, "_now", lambda: datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC))
    paths = [clear_codes(Elm327(_car()), None, backup_dir=tmp_path).backup_path for _ in range(3)]
    assert [p.name for p in paths] == [
        "dtc-backup-20260102-030405.json",
        "dtc-backup-20260102-030405-2.json",
        "dtc-backup-20260102-030405-3.json",
    ]
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(p.name for p in paths)


def test_default_backup_location(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    result = clear_codes(Elm327(_car()), None)
    assert result.backup_path.parent == tmp_path / "obd-diag" / "backups"


@pytest.mark.parametrize(
    "overrides",
    [
        {"010C": "410C0AF0"},  # Motor läuft
        {"010C": "NO DATA"},  # Drehzahl unbekannt
        {"ATRV": "11.0V"},  # Batterie schwach
        {"03": "4300", "07": "4700"},  # nichts zu löschen
    ],
)
def test_nothing_is_cleared_when_refused(overrides: dict[str, str], tmp_path: Path) -> None:
    transport = _car(**overrides)
    with pytest.raises(ClearRefused):
        clear_codes(Elm327(transport), None, backup_dir=tmp_path)
    assert "04" not in transport.sent
    assert list(tmp_path.iterdir()) == []


def test_only_permanent_codes_are_not_cleared(tmp_path: Path) -> None:
    transport = _car(**{"03": "4300", "07": "4700", "0A": "4A010420"})
    with pytest.raises(
        ClearRefused,
        match=r"^Keine gespeicherten oder ausstehenden Fehlercodes, nichts zu löschen\.$",
    ):
        clear_codes(Elm327(transport), None, backup_dir=tmp_path)
    assert "04" not in transport.sent


@pytest.mark.parametrize(
    "overrides",
    [
        {"0100": "SEARCHING...\rUNABLE TO CONNECT"},  # schon der Scan scheitert
        {"03": "?"},
        {"020C00": "OK"},  # Freeze Frame nicht lesbar
        {"020200": "STOPPED"},
    ],
)
def test_nothing_is_cleared_after_adapter_errors(overrides: dict[str, str], tmp_path: Path) -> None:
    transport = _car(**overrides)
    with pytest.raises(ElmError):
        clear_codes(Elm327(transport), None, backup_dir=tmp_path)
    assert "04" not in transport.sent
    assert list(tmp_path.iterdir()) == []


def test_nothing_is_cleared_when_backup_fails(tmp_path: Path) -> None:
    blocker = tmp_path / "datei"
    blocker.write_text("kein Ordner")
    transport = _car()
    with pytest.raises(ClearRefused, match=r"Sicherung nach .* fehlgeschlagen"):
        clear_codes(Elm327(transport), None, backup_dir=blocker / "backups")
    assert "04" not in transport.sent


def test_negative_response_keeps_backup(tmp_path: Path) -> None:
    transport = _car(**{"04": "7F0422"})
    with pytest.raises(ClearRefused, match="lehnt das Löschen ab: Bedingungen nicht erfüllt") as e:
        clear_codes(Elm327(transport), None, backup_dir=tmp_path)
    (backup,) = tmp_path.iterdir()
    assert str(backup) in str(e.value)
    assert transport.sent[-1] == "04"  # kein Kontroll-Scan, kein zweiter Versuch
    assert transport.sent.count("04") == 1


@pytest.mark.parametrize(
    ("response", "message"),
    [("NO DATA", r"nicht bestätigt \(NO DATA\)"), ("OK", "Löschen nicht bestätigt")],
)
def test_unconfirmed_clear(response: str, message: str, tmp_path: Path) -> None:
    transport = _car(**{"04": response})
    with pytest.raises(ClearRefused, match=message):
        clear_codes(Elm327(transport), None, backup_dir=tmp_path)
    assert len(list(tmp_path.iterdir())) == 1


@pytest.mark.parametrize("lang", [None, "en"])
def test_codes_that_return_remain_visible(tmp_path: Path, lang: str | None) -> None:
    # Der Fehler besteht weiter: das Steuergerät setzt P0133 sofort wieder.
    transport = FakeTransport(CAN_CAR_ENGINE_OFF, CLEARED | {"03": "43010133"})
    catalog = FakeCatalog({"P0133": "Lambdasonde reagiert zu langsam"})
    if lang is None:
        result = clear_codes(Elm327(transport), catalog, backup_dir=tmp_path)
    else:
        result = clear_codes(Elm327(transport), catalog, backup_dir=tmp_path, lang=lang)
    assert [(c.code, c.kind) for c in result.after.codes] == [("P0133", DtcKind.STORED)]
    # Auch der Kontroll-Scan erklärt die Codes, standardmäßig auf Deutsch.
    (code,) = result.after.codes
    assert code.info is not None
    assert code.info.title == "Lambdasonde reagiert zu langsam"
    assert catalog.lookups[-1] == ("P0133", lang or "de")
    assert {used for _, used in catalog.lookups} == {lang or "de"}
