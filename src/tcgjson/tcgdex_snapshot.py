"""Committed snapshot of TCGdex's English editions (route 1 for the European languages).

``data/tcgdex-en.json``: ``{"sets": {set id: {name, abbreviation, cards: {number: name}}}}``.
Only missing sets are fetched, so a name read from the snapshot never changes between builds;
``--refresh`` refetches on purpose. A failed fetch leaves that set out (its cards fall through
to later routes) and never fails the build.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable

import requests

from .ptcg_import import LANGUAGE_BY_CODE, norm_number

TCGDEX_API = "https://api.tcgdex.net/v2/en/sets/{set_id}"
EUROPEAN = ("de", "fr", "es", "it", "pt")


def needed_set_ids(root: Path, codes: list[str] | tuple[str, ...] = EUROPEAN) -> list[str]:
    """TCGdex set ids used by the given languages' physical cards (print keys only, fast)."""
    ids: set[str] = set()
    for code in codes:
        base = Path(root) / f"data_{LANGUAGE_BY_CODE[code].folder}"
        for dirpath, _dirs, files in os.walk(base):
            for filename in files:
                if not filename.endswith(".json"):
                    continue
                try:
                    record = json.loads((Path(dirpath) / filename).read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                key = record.get("print_key") if isinstance(record, dict) else None
                if isinstance(key, str) and key.startswith("tcgdex:"):
                    ids.add(key[len("tcgdex:"):].rpartition("-")[0])
    return sorted(ids)


def fetch_set(set_id: str) -> dict:
    response = requests.get(TCGDEX_API.format(set_id=set_id), timeout=40)
    response.raise_for_status()
    return response.json()


def _entry(data: dict) -> dict:
    abbreviation = data.get("abbreviation")
    if isinstance(abbreviation, dict):
        abbreviation = abbreviation.get("official")
    cards = {}
    for card in data.get("cards") or []:
        local_id = str(card.get("localId") or "").strip()
        if local_id and card.get("name"):
            cards[norm_number(local_id)] = card["name"]
    return {"name": data.get("name") or "", "abbreviation": abbreviation or "", "cards": cards}


def update_snapshot(path: Path, set_ids: list[str], *, refresh: bool = False,
                    fetch: Callable[[str], dict] = fetch_set) -> dict:
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"sets": {}}
    sets = data.setdefault("sets", {})
    fetched, failed, kept = 0, [], 0
    for set_id in set_ids:
        if set_id in sets and not refresh:
            kept += 1
            continue
        try:
            sets[set_id] = _entry(fetch(set_id))
            fetched += 1
        except (OSError, ValueError, requests.RequestException):
            failed.append(set_id)
    data["sets"] = dict(sorted(sets.items()))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                    encoding="utf-8")
    return {"fetched": fetched, "failed": failed, "kept": kept}
