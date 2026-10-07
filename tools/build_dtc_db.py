"""Baut src/obd_diag/data/dtc_catalog.sqlite aus den OBDex-Daten.

Quelle: https://github.com/foerbsnavi/OBDex (Daten CC0-1.0, Code MIT), fest auf einen
Commit gepinnt. Ohne ``--source`` werden die YAML-Dateien von GitHub geladen.

    uv run python tools/build_dtc_db.py [--source PFAD/ZU/OBDex] [--out DATEI]
"""

import argparse
import sys
import urllib.request
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from obd_diag.data.catalog_build import CatalogBuildError, build
from obd_diag.data.dtc_catalog import DEFAULT_PATH

OBDEX_REPO = "https://github.com/foerbsnavi/OBDex"
OBDEX_COMMIT = "5563d7b1f08620b0b265da62cf9f02f6b8ed382c"
RAW_BASE = f"https://raw.githubusercontent.com/foerbsnavi/OBDex/{OBDEX_COMMIT}"
FAMILIES = ("B0", "C0", "P0", "P2", "P3", "U0", "U3")
FILES = tuple(f"data/generic/{family}xxx_enriched.yaml" for family in FAMILIES)

# libyaml ist um ein Vielfaches schneller, aber nicht überall mitkompiliert.
_Loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def _read(rel_path: str, source: Path | None) -> str:
    if source is not None:
        return (source / rel_path).read_text(encoding="utf-8")
    url = f"{RAW_BASE}/{rel_path}"
    print(f"Lade {url}", file=sys.stderr)
    with urllib.request.urlopen(url, timeout=60) as resp:
        data: bytes = resp.read()
    return data.decode("utf-8")


def parse_entries(text: str, name: str = "<yaml>") -> list[Mapping[str, Any]]:
    """Parst eine OBDex-YAML-Datei (Liste von Code-Einträgen)."""
    data = yaml.load(text, Loader=_Loader)  # nur sichere Tags
    if data is None:
        return []
    if not isinstance(data, list) or not all(isinstance(e, dict) for e in data):
        raise CatalogBuildError(f"{name}: erwartet eine Liste von Einträgen")
    return data


def load_entries(source: Path | None) -> Iterator[Mapping[str, Any]]:
    for rel_path in FILES:
        yield from parse_entries(_read(rel_path, source), rel_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Baut den Offline-DTC-Katalog aus OBDex.")
    parser.add_argument(
        "--source", type=Path, help="lokaler OBDex-Checkout statt Download (Commit prüfen!)"
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_PATH, help="Zieldatei")
    args = parser.parse_args(argv)

    meta = {
        "source": OBDEX_REPO,
        "source_commit": OBDEX_COMMIT,
        "license": "CC0-1.0",
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    try:
        count = build(load_entries(args.source), args.out, meta)
    except (OSError, CatalogBuildError, yaml.YAMLError) as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1
    size_kib = args.out.stat().st_size / 1024
    print(f"{count} Codes nach {args.out} geschrieben ({size_kib:.0f} KiB).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
