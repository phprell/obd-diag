import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

from obd_diag.data import dtc_catalog
from obd_diag.data.catalog_build import CatalogBuildError, build
from obd_diag.data.dtc_catalog import Cause, DtcCatalog

FIXTURE = Path(__file__).parent.parent / "fixtures" / "dtc_sample.yaml"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    entries = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    out = tmp_path / "catalog.sqlite"
    count = build(entries, out, {"source_commit": "abc123"})
    assert count == 3
    return out


@pytest.fixture
def catalog(db_path: Path) -> Iterator[DtcCatalog]:
    with DtcCatalog(db_path) as cat:
        yield cat


def test_lookup_german(catalog: DtcCatalog) -> None:
    info = catalog.lookup("P0420")
    assert info is not None
    assert info.code == "P0420"
    assert info.title == "Katalysatorwirkungsgrad unter Schwellwert (Bank 1)"
    assert info.description is not None and info.description.startswith("Die Sauerstoff")
    assert info.mil is True
    assert info.emissions_relevant is True
    assert info.repair_difficulty == "hard"
    assert info.cost_eur == (600, 2500)


def test_causes_ordered_by_likelihood_with_fallback(catalog: DtcCatalog) -> None:
    info = catalog.lookup("P0420")
    assert info is not None
    assert info.causes == (
        Cause("Katalysator gealtert oder vergiftet", "high"),
        Cause("Downstream oxygen sensor drift", "medium"),  # ohne de → en
        Cause("Abgasleck vor Katalysator", "low"),
    )
    assert info.symptoms == ("Motorkontrollleuchte an", "Failed emissions inspection")


def test_lookup_english(catalog: DtcCatalog) -> None:
    info = catalog.lookup("P0420", lang="en")
    assert info is not None
    assert info.title == "Catalyst System Efficiency Below Threshold (Bank 1)"
    assert info.causes[0] == Cause("Catalyst aged or contaminated", "high")


def test_fallback_to_english_without_german(catalog: DtcCatalog) -> None:
    info = catalog.lookup("U0100", lang="de")
    assert info is not None
    assert info.title == 'Lost Communication With ECM/PCM "A"'
    assert info.description is not None and info.description.startswith("The module")
    assert info.causes == (Cause("CAN bus wiring fault", "high"),)
    assert info.mil is False
    assert info.emissions_relevant is None


def test_unknown_language_falls_back_to_english(catalog: DtcCatalog) -> None:
    info = catalog.lookup("P0420", lang="fr")
    assert info is not None
    assert info.title.startswith("Catalyst")


def test_entry_without_optional_fields(catalog: DtcCatalog) -> None:
    info = catalog.lookup("B0001")
    assert info is not None
    assert info.title == "Fahrerairbag Stufe 1 Auslösesteuerung"
    assert info.description is None
    assert info.causes == ()
    assert info.symptoms == ()
    assert info.mil is None
    assert info.repair_difficulty is None
    assert info.cost_eur is None


def test_lookup_case_insensitive(catalog: DtcCatalog) -> None:
    info = catalog.lookup(" p0420 ")
    assert info is not None
    assert info.code == "P0420"


def test_unknown_code(catalog: DtcCatalog) -> None:
    assert catalog.lookup("P9999") is None


def test_meta(catalog: DtcCatalog) -> None:
    meta = catalog.meta()
    assert meta["source_commit"] == "abc123"
    assert meta["code_count"] == "3"


def test_catalog_is_read_only(db_path: Path) -> None:
    with DtcCatalog(db_path) as cat:
        cat.lookup("P0420")
        assert cat._con is not None
        with pytest.raises(sqlite3.OperationalError):
            cat._con.execute("DELETE FROM dtc")


def test_missing_file_is_not_created(tmp_path: Path) -> None:
    path = tmp_path / "fehlt.sqlite"
    with pytest.raises(sqlite3.OperationalError):
        DtcCatalog(path).lookup("P0420")
    assert not path.exists()


def test_default_without_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(dtc_catalog, "DEFAULT_PATH", tmp_path / "fehlt.sqlite")
    assert DtcCatalog.default() is None


def test_default_with_file(monkeypatch: pytest.MonkeyPatch, db_path: Path) -> None:
    monkeypatch.setattr(dtc_catalog, "DEFAULT_PATH", db_path)
    cat = DtcCatalog.default()
    assert cat is not None
    assert cat.path == db_path
    cat.close()


def test_build_rejects_duplicates_and_keeps_old_file(db_path: Path) -> None:
    entry = {"code": "P0001", "category": "powertrain", "title": {"en": "Something"}}
    with pytest.raises(CatalogBuildError):
        build([entry, entry], db_path)
    with DtcCatalog(db_path) as cat:
        assert cat.lookup("P0420") is not None
    assert [p.name for p in db_path.parent.iterdir()] == [db_path.name]


def test_build_rejects_missing_title(tmp_path: Path) -> None:
    with pytest.raises(CatalogBuildError):
        build([{"code": "P0001", "category": "powertrain"}], tmp_path / "x.sqlite")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(not dtc_catalog.DEFAULT_PATH.exists(), reason="Katalog nicht gebaut")
def test_real_catalog_p0420() -> None:
    cat = DtcCatalog.default()
    assert cat is not None
    with cat:
        info = cat.lookup("P0420")
        assert info is not None
        assert "Katalysator" in info.title
        assert info.causes


@pytest.mark.parametrize("missing", ["cost_eur_min", "cost_eur_max"])
def test_incomplete_cost_range_is_dropped(db_path: Path, missing: str) -> None:
    with sqlite3.connect(db_path) as con:
        con.execute(f"UPDATE dtc SET {missing} = NULL WHERE code = 'P0420'")
    con.close()
    with DtcCatalog(db_path) as catalog:
        info = catalog.lookup("P0420")
    assert info is not None and info.cost_eur is None


def test_reopens_after_close(catalog: DtcCatalog) -> None:
    assert catalog.lookup("P0420") is not None
    catalog.close()
    catalog.close()  # zweimal schließen schadet nicht
    assert catalog.lookup("P0420") is not None
    assert catalog.meta()["source_commit"] == "abc123"
