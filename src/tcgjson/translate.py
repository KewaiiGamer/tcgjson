"""Translation cache, hand overrides and the machine translator (spec: "Machine translation").

``translations/<lang>.json`` maps ``"<kind>|<native name>"`` (kind: pokemon, trainer, energy,
set) to ``{en, source, model, date}``. A cached key is never translated again and the build never
calls the translator. ``translations/overrides.json`` (hand edited) maps a language code to
entries keyed the same way, or by a card's ``sourceId``; overrides always win over the cache, and
a ``sourceId`` override wins over every route.
"""
from __future__ import annotations

import json
from pathlib import Path

OVERRIDES_FILE = "overrides.json"
OVERRIDE_SOURCES = ("translated", "official")


def cache_key(kind: str, native: str) -> str:
    return f"{kind}|{native}"


def _is_card_key(key: str) -> bool:
    return "|" not in key and ":" in key


class Translations:
    def __init__(self, language: str, cache: dict[str, dict], overrides: dict[str, dict]):
        self.language = language
        self.cache = cache
        self.overrides = overrides

    @classmethod
    def load(cls, directory: Path | None, language: str) -> "Translations":
        if directory is None:
            return cls(language, {}, {})
        directory = Path(directory)
        cache = _read_json(directory / f"{language}.json")
        overrides = _read_json(directory / OVERRIDES_FILE).get(language, {})
        return cls(language, cache, overrides)

    def lookup(self, kind: str, native: str) -> str | None:
        """English name from an override or the cache, else None."""
        key = cache_key(kind, native)
        for table in (self.overrides, self.cache):
            entry = table.get(key)
            if isinstance(entry, dict) and str(entry.get("en") or "").strip():
                return str(entry["en"]).strip()
        return None

    def is_overridden(self, kind: str, native: str) -> bool:
        return cache_key(kind, native) in self.overrides

    def override_for_card(self, source_id: str) -> tuple[str, str] | None:
        """``(name, nameSource)`` for a card pinned by its ``sourceId``, else None."""
        entry = self.overrides.get(source_id)
        if not isinstance(entry, dict) or not str(entry.get("en") or "").strip():
            return None
        source = entry.get("source") if entry.get("source") in OVERRIDE_SOURCES else "translated"
        return str(entry["en"]).strip(), source

    def save_cache(self, directory: Path) -> Path:
        path = Path(directory) / f"{self.language}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(self.cache, ensure_ascii=False, indent=1, sort_keys=True)
        path.write_text(text + "\n", encoding="utf-8")
        return path


def _read_json(path: Path) -> dict:
    """A JSON object file; missing means empty, but a broken file is an error (never overwrite it)."""
    path = Path(path)
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must hold a JSON object")
    return data
