"""Simplified Chinese from PTCG-CHS-Datasets (``ptcg_chs_infos.json``), phase 2b.

The dataset lists each card under every product that contains it; a card's own set is the
collection whose ``commodityCode`` equals the card's, so each ``details.id`` is imported once,
from that collection. English names (spec, "English-name pipeline"):

1. ``linked_en``: Pokemon cards whose fingerprint (Pokedex number, HP, retreat cost, first
   illustrator) matches English PTCG-database cards that all share one name;
3. ``translated``: overrides, then ``translations/zh-cn.json`` (glossary or machine);
4. ``native``.

There is no route 2. Images point at the pinned commit of the owner's fork of the dataset; the
app serves them as cached thumbnails.
"""
from __future__ import annotations

import collections
import datetime as dt
import gzip
import json
import shutil
from pathlib import Path

from .english_names import LINKED_SOURCES, Resolved, unanimous
from .ptcg_build import (
    GateError,
    _readable,
    _unique_names,
    check_gates,
    coverage_counts,
    renamed_since,
    set_id_for,
)
from .ptcg_import import Card, Language, collector_number, load_language, load_species, norm_number
from .translate import Translations, cache_key

CHS = Language("zh-cn", "", "pokemon-chinese-simplified.json.gz", "Chinese (Simplified)", "chs")
DEFAULT_IMAGE_REPO = "KewaiiGamer/PTCG-CHS-Datasets"
KINDS = {"1": "pokemon", "2": "trainer", "3": "energy"}


def _int(value) -> int | None:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def fingerprint(dex, hp, retreat, artist) -> str | None:
    """``dex|hp|retreat|artist`` (artist lowercased), or None without a Pokedex number or HP."""
    if not dex or hp in (None, ""):
        return None
    return f"{int(dex)}|{hp}|{retreat if retreat is not None else ''}|{(artist or '').strip().lower()}"


def english_fingerprints(en_cards: list[Card]) -> dict[str, list[str]]:
    """English names by fingerprint, from the first four fields of each English ``card_key``."""
    index: dict[str, list[str]] = collections.defaultdict(list)
    for card in en_cards:
        parts = (card.card_key or "").split("|")
        if len(parts) < 4 or not card.name:
            continue
        key = fingerprint(_int(parts[0]), parts[1], parts[2], parts[3])
        if key:
            index[key].append(card.name)
    return index


def load_chs(path: Path, image_base: str) -> tuple[list[Card], dict[str, dict]]:
    """Cards (each ``details.id`` once, from its own collection) and collections by code."""
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    collections_by_code: dict[str, dict] = {}
    cards: list[Card] = []
    seen: set[int] = set()
    for col in data.get("collections") or []:
        code = str(col.get("commodityCode") or "").strip()
        if not code:
            continue
        collections_by_code[code] = {"id": col.get("id"), "name": str(col.get("name") or "").strip(),
                                     "salesDate": col.get("salesDate")}
        for entry in col.get("cards") or []:
            details = entry.get("details") or {}
            card_id = details.get("id", entry.get("id"))
            if str(entry.get("commodityCode") or "").strip() != code or card_id in seen:
                continue  # a reprint listed under another product, or a duplicate
            seen.add(card_id)
            number, _, total = str(details.get("collectionNumber") or "").partition("/")
            dex = _int(details.get("pokedexCode"))
            hp = details.get("hp")
            artists = details.get("illustratorName") or []
            artist = artists[0] if artists else ""
            kind = KINDS.get(str(entry.get("cardType") or details.get("cardType")), "trainer")
            rules = details.get("ruleText") or " ".join(
                str(a.get("abilityText") or "") for a in details.get("abilityItemList") or [])
            image = str(entry.get("image") or "").strip()
            cards.append(Card(
                source_id=f"zh-cn:{card_id}",
                name=str(details.get("cardName") or entry.get("name") or "").strip(),
                set_code=code,
                set_full_name=collections_by_code[code]["name"],
                number=norm_number(number.strip()),
                collector_number=collector_number(number.strip(), total.strip()),
                rarity=str(details.get("rarityText") or "").strip() or None,
                kind=kind,
                dex=dex if kind == "pokemon" else None,
                hp=str(hp) if hp is not None else None,
                print_key=None,
                card_key=fingerprint(dex, hp, details.get("retreatCost"), artist)
                if kind == "pokemon" else None,
                img=image_base + image if image else None,
                rules_text=(rules or "").replace("|", " ").strip() or None,
                stable_ref=str(card_id),
                raw={"yorenCode": entry.get("yorenCode") or details.get("yorenCode")},
            ))
    cards.sort(key=lambda c: (c.set_code, c.collector_number, int(c.stable_ref)
                              if c.stable_ref.isdigit() else 0))
    return cards, collections_by_code


def resolve_chs(cards: list[Card], en_index: dict[str, list[str]], translations: Translations,
                species: dict[int, str]) -> list[Resolved]:
    out = []
    for card in cards:
        alias = species.get(card.dex) if card.dex else None
        override = translations.override_for_card(card.source_id)
        if override:
            out.append(Resolved(card, override[0], override[1], alias))
            continue
        name = unanimous(en_index.get(card.card_key, [])) if card.card_key else None
        if name:
            out.append(Resolved(card, name, "linked_en", alias))
            continue
        translated = translations.lookup(card.kind, card.name)
        if translated:
            out.append(Resolved(card, translated, "translated", alias))
            continue
        out.append(Resolved(card, card.name, "native", alias))
    return out


def chs_set_rows(cards: list[Card], collections_by_code: dict[str, dict],
                 translations: Translations) -> dict[str, dict]:
    rows = {}
    for code in sorted({c.set_code for c in cards}):
        native = collections_by_code.get(code, {}).get("name") or ""
        english = translations.lookup("set", native) if native else None
        label = english or native
        rows[code] = {"setId": set_id_for(CHS.code, code),
                      "name": f"{code}: {label}" if label else code, "abbreviation": code}
        if collections_by_code.get(code, {}).get("salesDate"):
            rows[code]["releaseDate"] = collections_by_code[code]["salesDate"]
    if len({r["setId"] for r in rows.values()}) != len(rows):
        raise GateError("zh-cn: two set codes share a setId")
    return rows


def check_identity(products: list[dict], previous: dict | None) -> None:
    """A changed ``yorenCode`` or ``details.id`` for an existing card stops the build (spec)."""
    if not previous:
        return
    old_by_id = {p.get("sourceId"): p for p in previous.get("products") or []}
    old_by_slot = {(p.get("setId"), p.get("collectorNumber"), p.get("rarity"), p.get("nameNative")):
                   p.get("sourceId") for p in previous.get("products") or []}
    for product in products:
        old = old_by_id.get(product["sourceId"])
        if old is not None and old.get("yorenCode") != product.get("yorenCode"):
            raise GateError(f"zh-cn: yorenCode of {product['sourceId']} changed from "
                            f"{old.get('yorenCode')} to {product.get('yorenCode')}")
        slot = (product["setId"], product["collectorNumber"], product.get("rarity"),
                product["nameNative"])
        old_id = old_by_slot.get(slot)
        if old_id is not None and old_id != product["sourceId"] and old_id not in {
                p["sourceId"] for p in products}:
            raise GateError(f"zh-cn: card {slot} changed its details.id from {old_id} to "
                            f"{product['sourceId']}")


def chs_glossary(resolved: list[Resolved]) -> dict[str, str]:
    links: dict[str, list[str]] = collections.defaultdict(list)
    for item in resolved:
        if item.source in LINKED_SOURCES and item.card.name:
            links[cache_key(item.card.kind, item.card.name)].append(item.name)
    return {key: name for key, names in sorted(links.items())
            if (name := unanimous(names)) is not None}


def chs_pending_items(cards: list[Card], collections_by_code: dict[str, dict],
                      resolved: list[Resolved], translations: Translations,
                      species: dict[int, str]) -> list[dict]:
    grouped: dict[str, dict] = {}
    for item in resolved:
        card = item.card
        if item.source != "native" or not card.name:
            continue
        key = cache_key(card.kind, card.name)
        entry = grouped.setdefault(key, {"key": key, "kind": card.kind, "name": card.name,
                                         "count": 0, "dex": collections.Counter(), "rules": None,
                                         "yorenCodes": set()})
        entry["count"] += 1
        if card.dex:
            entry["dex"][card.dex] += 1
        if card.raw.get("yorenCode"):
            entry["yorenCodes"].add(card.raw["yorenCode"])
        if not entry["rules"] and card.rules_text:
            entry["rules"] = card.rules_text[:300]
    for code in sorted({c.set_code for c in cards}):
        native = collections_by_code.get(code, {}).get("name") or ""
        key = cache_key("set", native)
        if native and translations.lookup("set", native) is None and key not in grouped:
            grouped[key] = {"key": key, "kind": "set", "name": native, "count": 1,
                            "dex": collections.Counter(), "rules": None, "yorenCodes": set()}
    items = []
    for key in sorted(grouped):
        entry = grouped[key]
        item = {"key": key, "kind": entry["kind"], "name": entry["name"], "count": entry["count"]}
        if entry["dex"]:
            dex = entry["dex"].most_common(1)[0][0]
            if species.get(dex):
                item["species_hint"] = species[dex]
        if entry["rules"]:
            item["rules_text"] = entry["rules"]
        items.append(item)
    return items


def translate_chs(chs_json: Path, ptcg_root: Path, translations_dir: Path, *, translator,
                  japan_catalog: Path | None = None, limit: int | None = None,
                  batch_size: int = 20, today: str | None = None, log=lambda _m: None) -> dict:
    """``translate`` for zh-cn. The glossary is this language's own fingerprint links plus the
    Traditional Chinese links whose native name is written identically (needs the Japan
    catalog for Traditional Chinese's linked_ja names; without it only its linked_en)."""
    from .translate_run import fill_cache, pending_items

    translations = Translations.load(translations_dir, CHS.code)
    cards, collections_by_code = load_chs(chs_json, "")
    species = load_species(ptcg_root)
    resolved = resolve_chs(cards, english_fingerprints(load_language(ptcg_root, "en")),
                           translations, species)
    glossary = chs_glossary(resolved)
    if japan_catalog is not None and Path(japan_catalog).exists():
        _items, tc_glossary = pending_items(ptcg_root, "zh-tw", Translations("zh-tw", {}, {}),
                                            japan_catalog=japan_catalog, tcgdex_snapshot=None)
        for key, english in tc_glossary.items():
            glossary.setdefault(key, english)
    items = chs_pending_items(cards, collections_by_code, resolved, translations, species)
    return fill_cache(CHS.code, CHS.label, items, glossary, translations, translations_dir,
                      translator=translator, limit=limit, batch_size=batch_size, today=today,
                      log=log)


def build_chs(chs_json: Path, ptcg_root: Path, output: Path, *, translations_dir: Path | None,
              previous_dir: Path | None = None, source_commit: str = "",
              image_repo: str = DEFAULT_IMAGE_REPO, carry_forward: bool = False,
              generated_at: str | None = None) -> dict:
    """Write ``pokemon-chinese-simplified.json.gz``; returns ``{coverage, file}`` or ``{error}``."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    generated_at = generated_at or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ref = source_commit or "main"
    cards, collections_by_code = load_chs(
        chs_json, f"https://raw.githubusercontent.com/{image_repo}/{ref}/")
    species = load_species(ptcg_root)
    en_index = english_fingerprints(load_language(ptcg_root, "en"))
    translations = Translations.load(translations_dir, CHS.code)
    resolved = resolve_chs(cards, en_index, translations, species)
    try:
        previous = None
        old_file = Path(previous_dir) / CHS.asset if previous_dir is not None else None
        if old_file is not None and old_file.exists():
            if not _readable(old_file):
                raise GateError(f"zh-cn: previous release file {old_file.name} is unreadable")
            with gzip.open(old_file, "rt", encoding="utf-8") as fh:
                previous = json.load(fh)
        sets = chs_set_rows(cards, collections_by_code, translations)
        previous_names = {p.get("sourceId"): p.get("name")
                          for p in (previous or {}).get("products") or []}
        products = []
        for item, name in zip(resolved, _unique_names(resolved, previous_names)):
            card = item.card
            product = {"name": name, "nameNative": card.name, "nameSource": item.source,
                       "sourceId": card.source_id, "setId": sets[card.set_code]["setId"],
                       "collectorNumber": card.collector_number,
                       "imageUrls": [card.img] if card.img else [],
                       "yorenCode": card.raw.get("yorenCode")}
            if card.rarity:
                product["rarity"] = card.rarity
            if item.alias:
                product["nameAlias"] = item.alias
            products.append(product)
        overridden = {r.card.source_id for r in resolved
                      if translations.override_for_card(r.card.source_id)
                      or translations.is_overridden(r.card.kind, r.card.name)}
        check_gates(CHS.code, [dict(p, setCode="") for p in products], {}, previous=previous,
                    overridden=overridden)
        check_identity(products, previous)
    except GateError as exc:
        report = {"error": str(exc),
                  "coverage": coverage_counts([{"nameSource": r.source} for r in resolved])}
        if carry_forward and old_file is not None and old_file.exists() and _readable(old_file):
            shutil.copyfile(old_file, output / CHS.asset)
            report["carriedForward"] = True
        return report
    counts = coverage_counts(products)
    pokemon = [r for r in resolved if r.card.kind == "pokemon"]
    renamed = renamed_since(previous, products)
    data = {
        "meta": {
            "object": "tcgjson_compact_catalog", "version": 3, "generatedAt": generated_at,
            "productLine": "Pokemon Chinese (Simplified)", "slug": "pokemon-chinese-simplified",
            "language": CHS.code, "setCount": len(sets), "productCount": len(products),
            "source": {"name": "PTCG-CHS-Datasets", "repo": f"https://github.com/{image_repo}",
                       "commit": source_commit},
            "coverage": {CHS.code: counts},
            "pokemonFingerprint": {
                "pokemon": len(pokemon),
                "linked": sum(1 for r in pokemon if r.source == "linked_en"),
            },
            "renamedSincePrevious": renamed,
        },
        "sets": sorted(sets.values(), key=lambda s: s["name"]),
        "products": products,
    }
    path = output / CHS.asset
    tmp = path.with_suffix(".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
    tmp.replace(path)
    return {"coverage": counts, "file": str(path), "renamed": renamed,
            "pokemonFingerprint": data["meta"]["pokemonFingerprint"]}
