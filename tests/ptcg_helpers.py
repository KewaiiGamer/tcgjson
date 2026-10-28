"""Small PTCG-database fixture trees for the converter tests."""
from __future__ import annotations

import gzip
import json
from pathlib import Path


def write_card(root: Path, folder: str, rel: str, **fields) -> Path:
    """Write one PTCG card record at data_<folder>/<rel>.json."""
    path = root / f"data_{folder}" / f"{rel}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fields, ensure_ascii=False), encoding="utf-8")
    return path


def write_species(root: Path, table: dict[str, int]) -> None:
    path = root / "code" / "species_dex.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(table), encoding="utf-8")


def write_japan_catalog(path: Path, sets: list[dict], products: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump({"meta": {}, "sets": sets, "products": products}, fh)
    return path
