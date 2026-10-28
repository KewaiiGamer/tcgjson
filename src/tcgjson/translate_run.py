"""``tcgjson translate``: fill ``translations/<lang>.json`` for names with no English route.

Order per pending (kind, native name):
1. the glossary: the same native name already linked (routes 1 and 2) in this language, when all
   its links agree -> cached with ``source: "glossary"``;
2. the OpenAI-compatible endpoint (``TRANSLATE_API_BASE`` or ``API_BASE``, ``API_KEY``,
   ``MODEL``) -> cached with ``source: "machine"`` and the model name.
A cached key is never sent again. Without a key, or when the endpoint fails, the run stops
cleanly: earlier batches stay cached and the rest stays pending (route 4 in the build).
"""
from __future__ import annotations

import collections
import datetime as dt
import json
import os
import re
from pathlib import Path
from typing import Callable, Protocol

import requests

from .english_names import (
    LINKED_SOURCES,
    EnglishIndex,
    JapanLinks,
    TcgdexEnglish,
    resolve_names,
    unanimous,
)
from .ptcg_build import build_context, native_set_name, official_set_name, sets_by_code
from .ptcg_import import LANGUAGE_BY_CODE, load_language, load_species
from .translate import Translations, cache_key

DEFAULT_API_BASE = "https://ai.kewaii.com/v1"
DEFAULT_MODEL = "qwen3.8"
MAX_CANDIDATES = 10
MAX_RULES_CHARS = 300


class Translator(Protocol):
    model: str

    def translate(self, language_label: str, items: list[dict]) -> dict[int, str | None]: ...


def _bigrams(text: str) -> set[str]:
    compact = "".join(text.split())
    if len(compact) < 2:
        return {compact} if compact else set()
    return {compact[i:i + 2] for i in range(len(compact) - 1)}


def bigram_similarity(a: str, b: str) -> float:
    left, right = _bigrams(a), _bigrams(b)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def parse_reply(text: str) -> dict[int, str | None]:
    """``{id: English name}`` from a model reply holding ``{"items": [{"id", "en"}]}``."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return {}
    out: dict[int, str | None] = {}
    rows = data.get("items") if isinstance(data, dict) else None
    for item in rows if isinstance(rows, list) else []:
        if not isinstance(item, dict) or "id" not in item:
            continue
        en = item.get("en")
        if isinstance(en, str) and en.strip() and en.strip().casefold() != "none of these":
            try:
                out[int(item["id"])] = " ".join(en.split())
            except (TypeError, ValueError):
                continue
    return out


SYSTEM_PROMPT = (
    "You translate Pokemon Trading Card Game card names and set names from {language} into the "
    "official English name printed on English cards. Use the official English names of Pokemon, "
    "trainers, items, tools, stadiums and energy. Keep mechanics suffixes as English cards write "
    "them (ex, EX, GX, V, VMAX, VSTAR, BREAK, LV.X, Prism Star). 'species_hint' is the English "
    "species of the card's Pokedex number. If 'candidates' are given and one of them is the "
    "official English name of this card, answer exactly that candidate; if none of them is, "
    "translate the name yourself. 'rules_text' is the card's own text, for context only. "
    "Answer with JSON only: {{\"items\": [{{\"id\": <id>, \"en\": \"<English name>\"}}]}}."
)


class ChatTranslator:
    """Minimal OpenAI-compatible chat client (no SDK dependency)."""

    def __init__(self, base_url: str, api_key: str, model: str, *, timeout: float = 120.0,
                 post: Callable = requests.post):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._post = post

    def translate(self, language_label: str, items: list[dict]) -> dict[int, str | None]:
        body = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT.format(language=language_label)},
                {"role": "user", "content": json.dumps({"items": items}, ensure_ascii=False)},
            ],
        }
        response = self._post(self.url, headers={"Authorization": f"Bearer {self.api_key}",
                                                 "Content-Type": "application/json"},
                              json=body, timeout=self.timeout)
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return parse_reply(content)


def translator_from_env(env: dict | None = None) -> ChatTranslator | None:
    env = os.environ if env is None else env
    key = env.get("API_KEY")
    if not key:
        return None
    base = env.get("TRANSLATE_API_BASE") or env.get("API_BASE") or DEFAULT_API_BASE
    return ChatTranslator(base, key, env.get("MODEL") or DEFAULT_MODEL)


def pending_items(root: Path, code: str, translations: Translations, *,
                  japan_catalog: Path | None, tcgdex_snapshot: Path | None,
                  english: EnglishIndex | None = None, japan: JapanLinks | None = None,
                  ) -> tuple[list[dict], dict[str, str]]:
    """Distinct untranslated (kind, native) names with their hints, plus the language glossary."""
    lang = LANGUAGE_BY_CODE[code]
    root = Path(root)
    english = english or EnglishIndex(load_language(root, "en"))
    if japan is None and lang.group == "asia":
        japan = JapanLinks.load(load_language(root, "jp"), japan_catalog)
    ctx = build_context(root, lang, english=english, japan=japan,
                        tcgdex=TcgdexEnglish.load(tcgdex_snapshot), translations_dir=None,
                        species=load_species(root))
    ctx.translations = translations
    cards = load_language(root, code)
    resolved = resolve_names(cards, ctx)

    links: dict[str, list[str]] = collections.defaultdict(list)
    for item in resolved:
        if item.source in LINKED_SOURCES and item.card.name:
            links[cache_key(item.card.kind, item.card.name)].append(item.name)
    glossary = {key: name for key, names in sorted(links.items())
                if (name := unanimous(names)) is not None}

    grouped: dict[str, dict] = {}
    for item in resolved:
        card = item.card
        if item.source != "native" or not card.name:
            continue
        key = cache_key(card.kind, card.name)
        entry = grouped.setdefault(key, {"key": key, "kind": card.kind, "name": card.name,
                                         "count": 0, "dex": collections.Counter(), "rules": None})
        entry["count"] += 1
        if card.dex:
            entry["dex"][card.dex] += 1
        if not entry["rules"] and card.rules_text:
            entry["rules"] = card.rules_text[:MAX_RULES_CHARS]
    for code_, members in sets_by_code(cards).items():
        if official_set_name(lang, code_, ctx)[1]:
            continue
        native = native_set_name(members)
        key = cache_key("set", native)
        if native and translations.lookup("set", native) is None and key not in grouped:
            grouped[key] = {"key": key, "kind": "set", "name": native, "count": len(members),
                            "dex": collections.Counter(), "rules": None}

    items = []
    for key in sorted(grouped):
        entry = grouped[key]
        item = {"key": key, "kind": entry["kind"], "name": entry["name"], "count": entry["count"]}
        if entry["dex"]:
            dex = entry["dex"].most_common(1)[0][0]
            if ctx.species.get(dex):
                item["species_hint"] = ctx.species[dex]
        if entry["rules"]:
            item["rules_text"] = entry["rules"]
        items.append(item)
    return items, glossary


def candidates_for(item: dict, glossary: dict[str, str]) -> list[str]:
    """Official English names of same-kind glossary entries whose native names look closest."""
    if item["kind"] not in ("trainer", "energy"):
        return []
    scored = []
    for key, english in glossary.items():
        kind, _, native = key.partition("|")
        if kind != item["kind"]:
            continue
        score = bigram_similarity(item["name"], native)
        if score > 0:
            scored.append((-score, english))
    seen, out = set(), []
    for _score, english in sorted(scored):
        if english not in seen:
            seen.add(english)
            out.append(english)
        if len(out) >= MAX_CANDIDATES:
            break
    return out


def translate_language(root: Path, code: str, translations_dir: Path, *,
                       translator: Translator | None, japan_catalog: Path | None,
                       tcgdex_snapshot: Path | None, limit: int | None = None,
                       batch_size: int = 20, today: str | None = None,
                       log: Callable[[str], None] = lambda _m: None) -> dict:
    lang = LANGUAGE_BY_CODE[code]
    today = today or dt.date.today().isoformat()
    translations = Translations.load(translations_dir, code)
    items, glossary = pending_items(root, code, translations, japan_catalog=japan_catalog,
                                    tcgdex_snapshot=tcgdex_snapshot)
    counts = {"glossary": 0, "machine": 0, "pending": 0, "error": None}
    remaining = []
    for item in items:
        english = glossary.get(item["key"])
        if english:
            translations.cache[item["key"]] = {"en": english, "source": "glossary", "date": today}
            counts["glossary"] += 1
        else:
            remaining.append(item)
    translations.save_cache(translations_dir)
    if translator is None:
        counts["pending"] = len(remaining)
        return counts
    todo = remaining if limit is None else remaining[:limit]
    left = len(remaining) - len(todo)
    for start in range(0, len(todo), batch_size):
        batch = todo[start:start + batch_size]
        payload = []
        for index, item in enumerate(batch):
            entry = {"id": index, "name": item["name"], "kind": item["kind"]}
            for field in ("species_hint", "rules_text"):
                if item.get(field):
                    entry[field] = item[field]
            candidates = candidates_for(item, glossary)
            if candidates:
                entry["candidates"] = candidates
            payload.append(entry)
        try:
            answers = translator.translate(lang.label, payload)
        except (OSError, ValueError, KeyError, IndexError, TypeError,
                requests.RequestException) as exc:
            counts["error"] = str(exc)
            left += len(todo) - start
            break
        for index, item in enumerate(batch):
            english = answers.get(index)
            if english:
                translations.cache[item["key"]] = {"en": english, "source": "machine",
                                                   "model": translator.model, "date": today}
                counts["machine"] += 1
            else:
                left += 1
        translations.save_cache(translations_dir)
        log(f"{code}: {counts['machine']} translated so far")
    counts["pending"] = left
    return counts


def run_translate(args) -> int:
    translator = None if args.glossary_only else translator_from_env()
    if translator is None and not args.glossary_only:
        print("translate: no API_KEY in the environment; filling the glossary only, "
              "the rest stays native (route 4)")
    codes = args.languages or [code for code in LANGUAGE_BY_CODE]
    for code in codes:
        result = translate_language(
            args.ptcg_root, code, args.translations, translator=translator,
            japan_catalog=args.japan_catalog, tcgdex_snapshot=args.tcgdex_snapshot,
            limit=args.limit, batch_size=args.batch_size, log=print,
        )
        print(f"{code}: glossary {result['glossary']}, machine {result['machine']}, "
              f"pending {result['pending']}" + (f", stopped: {result['error']}"
                                                 if result["error"] else ""))
    return 0
