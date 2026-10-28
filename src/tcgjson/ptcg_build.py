"""Convert PTCG-database languages into tcgjson catalog files (spec: "Output format").

One gzip per language with the same top level as ``pokemon.json.gz`` (``meta``, ``sets``,
``products``). Products carry ``nameNative``, ``nameAlias``, ``nameSource`` and ``sourceId``;
``meta.coverage`` counts rows per ``nameSource``. Hard gates fail a language (its file is not
written): a ``linked_ja`` row outside a trusted set, more than 5% fewer rows than the previous
release, or a translated name that changed without an override.
"""
from __future__ import annotations

import collections
import datetime as dt
import gzip
import json
import zlib
from pathlib import Path
from typing import Iterable

from .english_names import (
    LINKED_SOURCES,
    EnglishIndex,
    JapanLinks,
    NameContext,
    Resolved,
    TcgdexEnglish,
    resolve_names,
    set_trust,
)
from .ptcg_import import LANGUAGE_BY_CODE, Card, Language, load_language, load_species
from .translate import Translations

NAME_SOURCES = ("official", "linked_en", "linked_ja", "translated", "native")
MAX_ROW_LOSS = 0.05
MAX_LINKED_DROP = 5.0  # percentage points of linked share lost against the previous release
MAX_SKIPPED_SHARE = 0.01  # unreadable source files tolerated per language
PTCG_REPO = "https://github.com/type-null/PTCG-database"


class GateError(RuntimeError):
    """A Phase 2 hard gate failed for one language."""


def set_id_for(code: str, set_code: str) -> int:
    return zlib.crc32(f"{code}:{set_code}".encode("utf-8")) & 0x7FFFFFFF


def _most_common(values: Iterable[str]) -> str:
    counts = collections.Counter(v for v in values if v)
    if not counts:
        return ""
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _strip_code_prefix(name: str, code: str) -> str:
    head, sep, rest = name.partition(":")
    if sep and head.strip().casefold() == code.casefold():
        return rest.strip()
    return name.strip()


def official_set_name(lang: Language, code: str, ctx: NameContext) -> tuple[str, str | None]:
    """``(abbreviation, English set name or None)`` from TCGplayer Japan or TCGdex English."""
    abbreviation, name = code, None
    if lang.group == "asia" and ctx.japan is not None:
        japanese = ctx.japan.english_set_name(code)
        if japanese:
            name = _strip_code_prefix(japanese, code)
    elif lang.group == "tcgdex" and ctx.tcgdex is not None:
        info = ctx.tcgdex.set_info(code) or {}
        if info.get("name"):
            name = str(info["name"]).strip()
        if info.get("abbreviation"):
            abbreviation = str(info["abbreviation"]).strip() or code
    return abbreviation, name


def sets_by_code(cards: list[Card]) -> dict[str, list[Card]]:
    by_code: dict[str, list[Card]] = collections.defaultdict(list)
    for card in cards:
        by_code[card.set_code].append(card)
    return by_code


def native_set_name(members: list[Card]) -> str:
    return _most_common(c.set_full_name for c in members)


def unique_abbreviations(proposed: dict[str, str], previous: dict[str, str] | None = None
                         ) -> dict[str, str]:
    """Final abbreviation per set code, unique within the language.

    The previous release's abbreviation is kept when it is still free (so a set's key does not
    flip); otherwise the proposed official code when no other set proposes it and it is not
    another set's code; otherwise the set code itself (codes are unique).
    """
    previous = previous or {}
    codes = set(proposed)
    counts = collections.Counter(proposed.values())
    final: dict[str, str] = {}
    for code in sorted(proposed):
        abbreviation = proposed[code]
        if counts[abbreviation] == 1 and (abbreviation == code or abbreviation not in codes):
            final[code] = abbreviation
        else:
            final[code] = code
    for code, old in sorted(previous.items()):
        if code not in final or final[code] == old:
            continue
        holders = [c for c, a in final.items() if a == old]
        if not holders and (old == code or old not in codes):
            final[code] = old
    if len(set(final.values())) != len(final):
        raise GateError("set abbreviations are not unique")
    return final


def set_rows(lang: Language, cards: list[Card], ctx: NameContext,
             previous: dict | None = None) -> dict[str, dict]:
    """``set code -> {setId, name, abbreviation}`` with ``CODE: English name`` names."""
    by_code = sets_by_code(cards)
    abbreviations: dict[str, str] = {}
    english: dict[str, str | None] = {}
    for code, members in by_code.items():
        native = native_set_name(members)
        abbreviation, name = official_set_name(lang, code, ctx)
        if not name and native:
            name = ctx.translations.lookup("set", native) or native
        abbreviations[code] = abbreviation
        english[code] = name
    id_to_code = {set_id_for(lang.code, code): code for code in by_code}
    if len(id_to_code) != len(by_code):
        raise GateError(f"{lang.code}: two set codes share a setId")
    old = {id_to_code[s["setId"]]: s.get("abbreviation") for s in (previous or {}).get("sets") or []
           if s.get("setId") in id_to_code and s.get("abbreviation")}
    final = unique_abbreviations(abbreviations, old)
    rows: dict[str, dict] = {}
    for code in sorted(by_code):
        abbreviation = final[code]
        name = f"{abbreviation}: {english[code]}" if english[code] else abbreviation
        rows[code] = {"setId": set_id_for(lang.code, code), "name": name,
                      "abbreviation": abbreviation}
    seen = collections.Counter(row["name"] for row in rows.values())
    for code, row in rows.items():
        if seen[row["name"]] > 1:
            row["name"] = f"{row['name']} ({code})"
    return rows


def _unique_names(resolved: list[Resolved], previous_names: dict[str, str] | None = None
                  ) -> list[str]:
    """Final product names: collisions on (set, number, name) get a suffix.

    A card keeps its previous release name when that name still fits its base name and is free,
    so a newly added print never takes over an existing card's name. Otherwise the suffix is the
    rarity, else the source site's card id, else a counter. Rows are never dropped.
    """
    previous_names = previous_names or {}
    taken: set[tuple[str, str, str]] = set()
    out: list[str | None] = [None] * len(resolved)

    def base_of(item: Resolved) -> str:
        card = item.card
        return item.name or f"Unknown card {card.collector_number or card.number}".strip()

    for index, item in enumerate(resolved):
        card = item.card
        old = previous_names.get(card.source_id)
        base = base_of(item)
        if old and (old == base or old.startswith(base + " (")):
            key = (card.set_code, card.collector_number, old)
            if key not in taken:
                taken.add(key)
                out[index] = old
    for index, item in enumerate(resolved):
        if out[index] is not None:
            continue
        card = item.card
        base = base_of(item)
        candidates = [base]
        if card.rarity:
            candidates.append(f"{base} ({card.rarity})")
        if card.stable_ref:
            candidates.append(f"{base} ({card.stable_ref})")
        name = next((c for c in candidates if (card.set_code, card.collector_number, c) not in taken),
                    None)
        counter = 2
        while name is None:
            option = f"{base} ({counter})"
            if (card.set_code, card.collector_number, option) not in taken:
                name = option
            counter += 1
        taken.add((card.set_code, card.collector_number, name))
        out[index] = name
    return [name for name in out if name is not None]


def products_for(lang: Language, resolved: list[Resolved], sets: dict[str, dict],
                 previous: dict | None = None) -> list[dict]:
    previous_names = {p.get("sourceId"): p.get("name")
                      for p in (previous or {}).get("products") or [] if p.get("sourceId")}
    products = []
    for item, name in zip(resolved, _unique_names(resolved, previous_names)):
        card = item.card
        product = {
            "name": name,
            "nameNative": card.name,
            "nameSource": item.source,
            "sourceId": card.source_id,
            "setId": sets[card.set_code]["setId"],
            "collectorNumber": card.collector_number,
            "rarity": card.rarity,
            "imageUrls": [card.img] if card.img else [],
        }
        if item.alias:
            product["nameAlias"] = item.alias
        if not card.rarity:
            product.pop("rarity")
        products.append(product)
    return products


def coverage_counts(products: list[dict]) -> dict[str, int]:
    counts = {source: 0 for source in NAME_SOURCES}
    for product in products:
        counts[product["nameSource"]] = counts.get(product["nameSource"], 0) + 1
    counts["total"] = len(products)
    return counts


def coverage_line(code: str, counts: dict[str, int]) -> str:
    total = counts.get("total") or 0

    def pct(value: int) -> str:
        return f"{(100.0 * value / total) if total else 0:.1f}%"

    linked = sum(counts.get(source, 0) for source in LINKED_SOURCES)
    parts = " ".join(f"{source} {counts.get(source, 0)} ({pct(counts.get(source, 0))})"
                     for source in NAME_SOURCES)
    return f"{code}: {total} rows, linked {pct(linked)}; {parts}"


def check_gates(code: str, products: list[dict], trust: dict[str, str], *, previous: dict | None,
                overridden: set[str]) -> None:
    """Raise GateError when a hard gate fails for this language."""
    for product in products:
        if product["nameSource"] == "linked_ja" and trust.get(product["setCode"]) != "trusted":
            raise GateError(f"{code}: linked_ja row {product['sourceId']} is in an untrusted or "
                            f"unknown set {product['setCode']}")
    if previous is None:
        return
    before = previous.get("products") or []
    if before and len(products) < (1 - MAX_ROW_LOSS) * len(before):
        raise GateError(f"{code}: {len(products)} rows, previous release had {len(before)} "
                        f"(more than {MAX_ROW_LOSS:.0%} lost)")
    # A translated name stays identical, unless an override changed it or an official link
    # (linked_en / linked_ja) replaced it; falling back to native or another translation fails.
    old_translated = {p.get("sourceId"): p.get("name") for p in before
                      if p.get("nameSource") == "translated"}
    for product in products:
        if product["sourceId"] in overridden or product["nameSource"] in LINKED_SOURCES:
            continue
        old = old_translated.get(product["sourceId"])
        if old is not None and old != product["name"]:
            raise GateError(f"{code}: translated name of {product['sourceId']} changed from "
                            f"{old!r} to {product['name']!r} ({product['nameSource']}) "
                            "without an override")
    old_counts = ((previous.get("meta") or {}).get("coverage") or {}).get(code)
    if old_counts and old_counts.get("total"):
        old_share = linked_share(old_counts)
        new_share = linked_share(coverage_counts(products))
        if old_share - new_share > MAX_LINKED_DROP:
            raise GateError(f"{code}: linked share fell from {old_share:.1f}% to {new_share:.1f}% "
                            "(missing Japan catalog or TCGdex snapshot?)")


def linked_share(counts: dict[str, int]) -> float:
    total = counts.get("total") or 0
    linked = sum(counts.get(source, 0) for source in LINKED_SOURCES)
    return 100.0 * linked / total if total else 0.0


def _read_previous(previous_dir: Path | None, lang: Language) -> dict | None:
    if previous_dir is None:
        return None
    path = Path(previous_dir) / lang.asset
    if not path.exists():
        return None
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def _overridden(resolved: list[Resolved], translations: Translations) -> set[str]:
    out = set()
    for item in resolved:
        card = item.card
        if (translations.override_for_card(card.source_id)
                or translations.is_overridden(card.kind, card.name)):
            out.add(card.source_id)
    return out


def build_context(root: Path, lang: Language, *, english: EnglishIndex, japan: JapanLinks | None,
                  tcgdex: TcgdexEnglish, translations_dir: Path | None,
                  species: dict[int, str]) -> NameContext:
    return NameContext(
        language=lang,
        english=english,
        tcgdex=tcgdex if lang.group == "tcgdex" else None,
        japan=japan if lang.group == "asia" else None,
        translations=Translations.load(translations_dir, lang.code),
        species=species,
    )


def build_languages(root: Path, output: Path, codes: list[str], *, japan_catalog: Path | None,
                    translations_dir: Path | None, tcgdex_snapshot: Path | None,
                    previous_dir: Path | None = None, source_commit: str = "",
                    generated_at: str | None = None) -> dict[str, dict]:
    """Write one catalog file per language. Returns ``{code: {coverage, file} | {error}}``."""
    root = Path(root)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    generated_at = generated_at or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    languages = [LANGUAGE_BY_CODE[code] for code in codes]
    # Missing link inputs would silently turn linked names into native ones: refuse to build.
    if any(lang.group == "asia" for lang in languages):
        if japan_catalog is None or not Path(japan_catalog).exists():
            raise FileNotFoundError(f"Korean / Traditional Chinese need the Japan catalog "
                                    f"(--japan-catalog), not found: {japan_catalog}")
    if any(lang.group == "tcgdex" for lang in languages):
        if tcgdex_snapshot is None or not Path(tcgdex_snapshot).exists():
            raise FileNotFoundError(f"European languages need the TCGdex English snapshot, "
                                    f"not found: {tcgdex_snapshot}")
    english = EnglishIndex(load_language(root, "en"))
    species = load_species(root)
    tcgdex = TcgdexEnglish.load(tcgdex_snapshot)
    japan = None
    if any(lang.group == "asia" for lang in languages):
        japan = JapanLinks.load(load_language(root, "jp"), japan_catalog)
    report: dict[str, dict] = {}
    for lang in languages:
        stats: dict[str, int] = {}
        cards = load_language(root, lang.code, stats=stats)
        previous = _read_previous(previous_dir, lang)
        ctx = build_context(root, lang, english=english, japan=japan, tcgdex=tcgdex,
                            translations_dir=translations_dir, species=species)
        resolved = resolve_names(cards, ctx)
        trust = set_trust(cards, ctx)
        try:
            skipped = stats.get("skipped", 0)
            if skipped > MAX_SKIPPED_SHARE * max(1, skipped + len(cards)):
                raise GateError(f"{lang.code}: {skipped} unreadable source files")
            sets = set_rows(lang, cards, ctx, previous)
            products = products_for(lang, resolved, sets, previous)
            counts = coverage_counts(products)
            gate_rows = [dict(p, setCode=item.card.set_code) for p, item in zip(products, resolved)]
            check_gates(lang.code, gate_rows, trust, previous=previous,
                        overridden=_overridden(resolved, ctx.translations))
        except GateError as exc:
            report[lang.code] = {"error": str(exc),
                                 "coverage": coverage_counts(
                                     [{"nameSource": r.source} for r in resolved])}
            continue
        trust_counts = collections.Counter(trust.values())
        data = {
            "meta": {
                "object": "tcgjson_compact_catalog",
                "version": 3,
                "generatedAt": generated_at,
                "productLine": f"Pokemon {lang.label}",
                "slug": lang.asset.removesuffix(".json.gz"),
                "language": lang.code,
                "setCount": len(sets),
                "productCount": len(products),
                "source": {"name": "PTCG-database", "repo": PTCG_REPO, "commit": source_commit,
                           "license": "MIT"},
                "coverage": {lang.code: counts},
                "skippedSourceFiles": stats.get("skipped", 0),
                "setTrust": {k: trust_counts.get(k, 0) for k in ("trusted", "untrusted", "unknown")},
            },
            "sets": sorted(sets.values(), key=lambda s: s["name"]),
            "products": products,
        }
        path = output / lang.asset
        tmp = path.with_suffix(".tmp")
        with gzip.open(tmp, "wt", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
        tmp.replace(path)
        report[lang.code] = {"coverage": counts, "file": str(path),
                             "skipped": stats.get("skipped", 0)}
    return report
