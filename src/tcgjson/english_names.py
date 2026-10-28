"""English names for non-English PTCG-database cards (spec: "English-name pipeline").

Routes, first hit wins, recorded as ``nameSource``:

1. ``linked_en``: English PTCG cards with the same ``print_key`` or ``card_key`` whose names all
   agree (ignoring case and punctuation); European languages then TCGdex's English edition at the
   same set id and number.
2. ``linked_ja`` (Korean, Traditional Chinese): the Japanese card at the same set code and number,
   named by TCGplayer Japan, only in a trusted set.
3. ``translated``: hand overrides, then the committed translation cache.
4. ``native``: the native name.
"""
from __future__ import annotations

import collections
import gzip
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .config import normalize_key
from .ptcg_import import Card, Language, norm_number
from .translate import Translations

LINKED_SOURCES = ("official", "linked_en", "linked_ja")

TRUST_MIN_COMPARED = 5
TRUST_MIN_SHARE = 0.95

_QUOTES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"'})


def clean_english(name: str) -> str:
    return " ".join(name.translate(_QUOTES).split())


def unanimous(names: Iterable[str]) -> str | None:
    """The shared name when every candidate folds to the same key, else None."""
    cleaned = [clean_english(n) for n in names if n and n.strip()]
    if not cleaned:
        return None
    if len({normalize_key(n) for n in cleaned}) != 1:
        return None
    counts = collections.Counter(cleaned)
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


_NUMBER_TOKEN = re.compile(r"\s+-\s+[^\s()]*\d[^\s()]*(?=\s*(\(|$))")
_TRAILING_PAREN = re.compile(r"\s*\(([^()]*)\)$")
# Parenthesised TCGplayer labels that name a printing (finish, pattern, stamp), not a card.
_PRINTING_WORDS = ("pattern", "holo", "foil", "stamp", "jumbo", "oversize", "reverse", "cosmos",
                   "prerelease", "staff", "promo")


def _printing_bases(name: str) -> list[str]:
    """The name and each shorter form with a trailing printing label removed."""
    bases = [name]
    while True:
        match = _TRAILING_PAREN.search(name)
        if not match or not any(w in match.group(1).casefold() for w in _PRINTING_WORDS):
            return bases
        name = name[: match.start()].rstrip()
        bases.append(name)


def tcgplayer_base_name(names: Iterable[str]) -> str | None:
    """Base card name over the TCGplayer products at one set code and number.

    `` - 001/073`` tokens are removed and printing labels such as ``(Master Ball Pattern)`` or
    ``(Mirror Holofoil)`` are folded, so the products of one card share a base. Names without a
    common base are ambiguous (None).
    """
    cleaned = {clean_english(_NUMBER_TOKEN.sub("", n)) for n in names if n and n.strip()}
    if not cleaned:
        return None
    common: set[str] | None = None
    for name in cleaned:
        options = set(_printing_bases(name))
        common = options if common is None else common & options
    if not common:
        return None
    return min(common, key=lambda base: (len(base), base))


class EnglishIndex:
    """PTCG-database English cards by ``print_key`` and ``card_key``."""

    def __init__(self, cards: Iterable[Card]):
        self.by_print: dict[str, list[str]] = collections.defaultdict(list)
        self.by_card: dict[str, list[str]] = collections.defaultdict(list)
        for card in cards:
            if not card.name:
                continue
            if card.print_key:
                self.by_print[card.print_key].append(card.name)
            if card.card_key:
                self.by_card[card.card_key].append(card.name)

    def candidates(self, card: Card) -> list[str]:
        names: list[str] = []
        if card.print_key:
            names += self.by_print.get(card.print_key, [])
        if card.card_key:
            names += self.by_card.get(card.card_key, [])
        return names


class TcgdexEnglish:
    """TCGdex's English editions: ``{set id: {name, abbreviation, cards: {number: name}}}``."""

    def __init__(self, snapshot: dict):
        self.sets = snapshot

    @classmethod
    def load(cls, path: Path | None) -> "TcgdexEnglish":
        if path is None or not Path(path).exists():
            return cls({})
        return cls(json.loads(Path(path).read_text(encoding="utf-8")).get("sets", {}))

    def card_name(self, print_key: str | None) -> str | None:
        if not print_key or not print_key.startswith("tcgdex:"):
            return None
        set_id, _, number = print_key[len("tcgdex:"):].rpartition("-")
        cards = (self.sets.get(set_id) or {}).get("cards") or {}
        name = cards.get(norm_number(number))
        return clean_english(name) if name else None

    def set_info(self, set_id: str) -> dict | None:
        return self.sets.get(set_id)


def _japan_key(print_key: str | None) -> str | None:
    if print_key and print_key.startswith("asia:"):
        return print_key
    return None


class JapanLinks:
    """PTCG Japanese cards plus TCGplayer Japan's English names by set code and number."""

    def __init__(self, jp_by_print: dict[str, list[Card]], names: dict[tuple[str, str], str | None],
                 set_names: dict[str, str | None]):
        self.jp_by_print = jp_by_print
        self.names = names
        self.set_names = set_names
        self._folded_codes = collections.defaultdict(set)
        for code, _number in names:
            self._folded_codes[code.casefold()].add(code)
        for code in set_names:
            self._folded_codes[code.casefold()].add(code)

    @classmethod
    def load(cls, jp_cards: Iterable[Card], japan_catalog: Path | None) -> "JapanLinks":
        jp_by_print: dict[str, list[Card]] = collections.defaultdict(list)
        for card in jp_cards:
            if _japan_key(card.print_key):
                jp_by_print[card.print_key].append(card)
        names: dict[tuple[str, str], str | None] = {}
        set_names: dict[str, str | None] = {}
        if japan_catalog is not None and Path(japan_catalog).exists():
            with gzip.open(japan_catalog, "rt", encoding="utf-8") as fh:
                data = json.load(fh)
            codes: dict[int, str] = {}
            per_code_names: dict[str, set[str]] = collections.defaultdict(set)
            for s in data.get("sets") or []:
                code = str(s.get("abbreviation") or "").strip()
                if code:
                    codes[s.get("setId")] = code
                    per_code_names[code].add(str(s.get("name") or "").strip())
            for code, set_name_options in per_code_names.items():
                set_names[code] = next(iter(set_name_options)) if len(set_name_options) == 1 else None
            grouped: dict[tuple[str, str], list[str]] = collections.defaultdict(list)
            for p in data.get("products") or []:
                code = codes.get(p.get("setId"))
                number = str(p.get("collectorNumber") or "").split("/")[0].strip()
                if code and number and p.get("name"):
                    grouped[(code, norm_number(number))].append(p["name"])
            names = {key: tcgplayer_base_name(group) for key, group in grouped.items()}
        return cls(dict(jp_by_print), names, set_names)

    def resolve_code(self, code: str) -> str | None:
        """The TCGplayer code for a PTCG code: exact, else a unique case-insensitive match."""
        if code in self.set_names or any(c == code for c, _n in self.names):
            return code
        options = self._folded_codes.get(code.casefold(), set())
        return next(iter(options)) if len(options) == 1 else None

    def english_name(self, code: str, number: str) -> str | None:
        resolved = self.resolve_code(code)
        if resolved is None:
            return None
        return self.names.get((resolved, number))

    def english_set_name(self, code: str) -> str | None:
        resolved = self.resolve_code(code)
        return self.set_names.get(resolved) if resolved else None


def _agrees(card: Card, jp_cards: list[Card]) -> bool:
    return any(j.dex == card.dex and j.hp == card.hp for j in jp_cards if j.dex)


def trusted_sets(cards: Iterable[Card], japan: JapanLinks) -> dict[str, str]:
    """``trusted`` / ``untrusted`` / ``unknown`` per set code (Pokedex number and HP agreement)."""
    compared: collections.Counter[str] = collections.Counter()
    agreed: collections.Counter[str] = collections.Counter()
    codes: set[str] = set()
    for card in cards:
        codes.add(card.set_code)
        key = _japan_key(card.print_key)
        if not card.dex or not key:
            continue
        jp_cards = [j for j in japan.jp_by_print.get(key, []) if j.dex]
        if not jp_cards:
            continue
        compared[card.set_code] += 1
        agreed[card.set_code] += _agrees(card, jp_cards)
    trust: dict[str, str] = {}
    for code in sorted(codes):
        if compared[code] < TRUST_MIN_COMPARED:
            trust[code] = "unknown"
        elif agreed[code] / compared[code] >= TRUST_MIN_SHARE:
            trust[code] = "trusted"
        else:
            trust[code] = "untrusted"
    return trust


@dataclass(slots=True)
class NameContext:
    language: Language
    english: EnglishIndex
    tcgdex: TcgdexEnglish | None
    japan: JapanLinks | None
    translations: Translations
    species: dict[int, str]


@dataclass(slots=True)
class Resolved:
    card: Card
    name: str
    source: str
    alias: str | None


def link_en(card: Card, ctx: NameContext) -> str | None:
    name = unanimous(ctx.english.candidates(card))
    if name:
        return name
    if ctx.language.group == "tcgdex" and ctx.tcgdex is not None:
        return ctx.tcgdex.card_name(card.print_key)
    return None


def link_ja(card: Card, ctx: NameContext, trust: dict[str, str]) -> str | None:
    if ctx.language.group != "asia" or ctx.japan is None:
        return None
    if trust.get(card.set_code) != "trusted":
        return None
    key = _japan_key(card.print_key)
    jp_cards = ctx.japan.jp_by_print.get(key, []) if key else []
    if not jp_cards:
        return None
    if card.dex:
        with_dex = [j for j in jp_cards if j.dex]
        if with_dex and not _agrees(card, with_dex):
            return None
    code, _, number = key[len("asia:"):].rpartition("-")
    name = ctx.japan.english_name(code, norm_number(number))
    # Safety: a Pokemon card's English name must contain its species (catches numbering drift
    # between PTCG-database and TCGplayer, e.g. a trainer name on a Pokemon card).
    species = ctx.species.get(card.dex) if card.dex else None
    if name and species and normalize_key(species) not in normalize_key(name):
        return None
    return name


def resolve_names(cards: list[Card], ctx: NameContext) -> list[Resolved]:
    trust = trusted_sets(cards, ctx.japan) if ctx.japan is not None else {}
    out: list[Resolved] = []
    for card in cards:
        alias = ctx.species.get(card.dex) if card.dex else None
        override = ctx.translations.override_for_card(card.source_id)
        if override:
            out.append(Resolved(card, override[0], override[1], alias))
            continue
        name = link_en(card, ctx)
        if name:
            out.append(Resolved(card, name, "linked_en", alias))
            continue
        name = link_ja(card, ctx, trust)
        if name:
            out.append(Resolved(card, name, "linked_ja", alias))
            continue
        translated = ctx.translations.lookup(card.kind, card.name)
        if translated:
            out.append(Resolved(card, translated, "translated", alias))
            continue
        out.append(Resolved(card, card.name, "native", alias))
    return out


def set_trust(cards: list[Card], ctx: NameContext) -> dict[str, str]:
    return trusted_sets(cards, ctx.japan) if ctx.japan is not None else {}
