"""Add official Japanese card names (``nameNative``) to the Pokemon Japan catalog.

TCGplayer's "Pokemon Japan" product line has English names only. PTCG-database
(github.com/type-null/PTCG-database, MIT licence) has every card of pokemon-card.com
with its Japanese name, set code and collector number in ``data_jp``. This module
links a TCGplayer row to the PTCG card at the same set code and number, and only
inside sets where that link is *trusted*.

TCGplayer rows carry no HP or Pokedex number, so trust compares the PTCG card's
Pokedex number, turned into an English species word with ``data_en``, against the
TCGplayer English name:

* A set is trusted when at least 5 of its rows sit on a PTCG Pokemon card with a
  known Pokedex number and at least 95% of those rows contain that species word.
* Set code candidates, in order: the TCGplayer abbreviation (exact, then ignoring
  case), the set-name prefix before ``:`` (exact, then ignoring case). The first
  trusted candidate wins. Failing that, every PTCG code is tried and the set links
  only if exactly one code is trusted.
* Inside a trusted set a row is still refused when the PTCG card is a Pokemon whose
  species word is not in the row name, when it is not a Pokemon but the row name ends
  in a species name, or when PTCG has two different names at that number.

Rows that are not linked keep no ``nameNative``; nothing else in the file changes.
"""
from __future__ import annotations

import argparse
import copy
import gzip
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .atomic import atomic_write_json, atomic_write_json_gzip

MIN_COMPARED = 5
MIN_AGREEMENT = 0.95
# A word shared by the names of more than this many species is not a species word
# ("Galarian", "Team", "Rocket's").
MAX_SPECIES_PER_WORD = 2
# Card-name suffixes dropped before looking at the last word of a row name.
NAME_SUFFIXES = {"ex", "v", "vmax", "vstar", "gx", "break", "lvx", "prime", "legend", "star"}

_NUMBER = re.compile(r"0*(\d+)")
_NAME_TAIL = re.compile(r"\s+-\s+\S*\d\S*.*$")  # " - 001/073", " - 132/SV-P (Holo)"
_PARENS = re.compile(r"\([^)]*\)")


@dataclass(frozen=True)
class JpCard:
    name: str
    is_pokemon: bool
    dex: int | None


# code -> collector number key -> cards printed there
CardIndex = dict[str, dict[str, tuple[JpCard, ...]]]


@dataclass
class SetLink:
    set_id: int
    set_name: str
    abbreviation: str
    code: str | None
    route: str
    compared: int = 0
    agreed: int = 0
    trusted: bool = False
    rows: int = 0
    named: int = 0


@dataclass
class LinkReport:
    rows_total: int = 0
    rows_named: int = 0
    sets: list[SetLink] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "rowsTotal": self.rows_total,
            "rowsNamed": self.rows_named,
            "sets": [
                {
                    "setId": s.set_id,
                    "setName": s.set_name,
                    "abbreviation": s.abbreviation,
                    "code": s.code,
                    "route": s.route,
                    "compared": s.compared,
                    "agreed": s.agreed,
                    "trusted": s.trusted,
                    "rows": s.rows,
                    "named": s.named,
                }
                for s in self.sets
            ],
        }


def fold(text: str) -> str:
    """Lowercase ASCII letters and digits only, accents removed ("Flabébé" -> "flabebe")."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]", "", text.lower())


def number_key(number: Any) -> str | None:
    """The collector number as a plain integer string ("001/073" -> "1"); None if not numeric."""
    if number is None:
        return None
    head = str(number).split("/")[0].strip()
    match = _NUMBER.fullmatch(head)
    return str(int(match.group(1))) if match else None


def clean_name(name: Any) -> str | None:
    if not isinstance(name, str):
        return None
    name = unicodedata.normalize("NFC", name).strip()
    return name or None


def is_pokemon(record: dict[str, Any]) -> bool:
    return unicodedata.normalize("NFC", str(record.get("card_type") or "")) == "Pokémon"


def _dex(value: Any) -> int | None:
    try:
        dex = int(value)
    except (TypeError, ValueError):
        return None
    return dex if dex > 0 else None


def load_ptcg_folder(folder: Path) -> list[dict[str, Any]]:
    """Every card object under ``folder`` (one JSON file per card); unreadable files are skipped."""
    records = []
    for path in sorted(Path(folder).rglob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def build_species_tokens(en_records: Iterable[dict[str, Any]]) -> dict[int, str]:
    """Pokedex number -> the folded English word that names the species ("pikachu").

    ``data_en`` has no Pokedex field; the number is the first part of ``card_key``.
    For each number, the word found in the most card names wins (ties: the longer
    word), ignoring words used by more than two species.
    """
    names: dict[int, Counter[str]] = defaultdict(Counter)
    for record in en_records:
        name = clean_name(record.get("name"))
        dex = _dex(str(record.get("card_key") or "").split("|")[0])
        if name and dex and is_pokemon(record):
            names[dex][name] += 1
    spread: dict[str, set[int]] = defaultdict(set)
    for dex, counter in names.items():
        for name in counter:
            for word in name.split():
                if fold(word):
                    spread[fold(word)].add(dex)
    tokens = {}
    for dex, counter in names.items():
        for limit in (MAX_SPECIES_PER_WORD, None):
            words: Counter[str] = Counter()
            for name, count in counter.items():
                for word in {fold(w) for w in name.split()}:
                    if word and (limit is None or len(spread[word]) <= limit):
                        words[word] += count
            if words:
                tokens[dex] = max(words.items(), key=lambda kv: (kv[1], len(kv[0]), kv[0]))[0]
                break
    return tokens


def build_card_index(jp_records: Iterable[dict[str, Any]]) -> CardIndex:
    """Index ``data_jp`` cards by set code and numeric collector number."""
    index: dict[str, dict[str, list[JpCard]]] = defaultdict(lambda: defaultdict(list))
    for record in jp_records:
        code = record.get("set_name")
        number = number_key(record.get("number"))
        name = clean_name(record.get("name"))
        if not isinstance(code, str) or not code.strip() or number is None or name is None:
            continue
        card = JpCard(name=name, is_pokemon=is_pokemon(record), dex=_dex(record.get("pokedex_number")))
        index[code.strip()][number].append(card)
    return {code: {n: tuple(cards) for n, cards in by_number.items()} for code, by_number in index.items()}


def _slot_card(cards: tuple[JpCard, ...] | None) -> JpCard | None:
    """The card at a slot, or None when the slot is empty or holds two different names."""
    if not cards or len({c.name for c in cards}) != 1:
        return None
    return cards[0]


def _base_words(row_name: str) -> list[str]:
    name = _PARENS.sub(" ", _NAME_TAIL.sub("", row_name))
    words = [fold(w) for w in name.split()]
    words = [w for w in words if w]
    while words and words[-1] in NAME_SUFFIXES:
        words.pop()
    return words


def _species_agrees(card: JpCard, row_name: str, tokens: dict[int, str]) -> bool | None:
    """True/False for a Pokemon card with a known species word, None when not comparable."""
    if not card.is_pokemon or card.dex not in tokens:
        return None
    return tokens[card.dex] in fold(row_name)


def _row_accepts(card: JpCard, row_name: str, tokens: dict[int, str], species_words: set[str]) -> bool:
    agrees = _species_agrees(card, row_name, tokens)
    if agrees is not None:
        return agrees
    if card.is_pokemon:
        return True  # a Pokemon without a usable Pokedex number inherits the set's trust
    words = _base_words(row_name)
    return not (words and words[-1] in species_words)


def _keyed(products: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """(number key, folded name) of each row with a numeric collector number."""
    out = []
    for product in products:
        key = number_key(product.get("collectorNumber"))
        if key is not None:
            out.append((key, fold(str(product.get("name") or ""))))
    return out


def _trust(rows: list[tuple[str, str]], cards: dict[str, tuple[JpCard, ...]],
           tokens: dict[int, str]) -> tuple[int, int]:
    """(rows compared, rows agreeing) for one PTCG code: Pokemon slots with a species word."""
    compared = agreed = 0
    for key, folded_name in rows:
        card = _slot_card(cards.get(key))
        if card is not None and card.is_pokemon and card.dex in tokens:
            compared += 1
            agreed += tokens[card.dex] in folded_name
    return compared, agreed


def _is_trusted(compared: int, agreed: int) -> bool:
    return compared >= MIN_COMPARED and agreed >= MIN_AGREEMENT * compared


def _candidates(set_row: dict[str, Any], codes: Iterable[str]) -> list[tuple[str, str]]:
    by_fold: dict[str, list[str]] = defaultdict(list)
    exact = set()
    for code in codes:
        exact.add(code)
        by_fold[code.casefold()].append(code)
    abbreviation = str(set_row.get("abbreviation") or "").strip()
    name = str(set_row.get("name") or "")
    prefix = name.split(":", 1)[0].strip() if ":" in name else ""
    out: list[tuple[str, str]] = []
    for text, route in ((abbreviation, "abbreviation"), (prefix, "name-prefix")):
        if not text:
            continue
        if text in exact:
            out.append((text, route))
        folded = by_fold.get(text.casefold(), [])
        if len(folded) == 1 and folded[0] != text:
            out.append((folded[0], route + "-case"))
    seen: set[str] = set()
    return [(c, r) for c, r in out if not (c in seen or seen.add(c))]


def resolve_set(set_row: dict[str, Any], products: list[dict[str, Any]], index: CardIndex,
                tokens: dict[int, str]) -> SetLink:
    """Pick the PTCG set code for one TCGplayer set, or none.

    Every PTCG code is scored; among the trusted ones the code with the most agreeing
    rows wins. On a tie the named candidate (abbreviation or name prefix) wins; a tie
    between codes that are not named candidates links nothing ("ambiguous").
    """
    link = SetLink(set_id=set_row.get("setId"), set_name=str(set_row.get("name") or ""),
                   abbreviation=str(set_row.get("abbreviation") or ""), code=None, route="none",
                   rows=len(products))
    rows = _keyed(products)
    named = _candidates(set_row, index)
    scores = {code: _trust(rows, index[code], tokens) for code in index}
    trusted = {code: score for code, score in scores.items() if _is_trusted(*score)}
    if not trusted:
        if named:  # report the first named candidate's numbers
            code = named[0][0]
            link.route = "untrusted:" + code
            link.compared, link.agreed = scores[code]
        return link
    best = max(agreed for _compared, agreed in trusted.values())
    top = [code for code, (_c, agreed) in trusted.items() if agreed == best]
    named_top = [(code, route) for code, route in named if code in top]
    if named_top:
        code, route = named_top[0]
    elif len(top) == 1:
        code, route = top[0], "content"
    else:
        link.route = "ambiguous"
        return link
    link.code, link.route, link.trusted = code, route, True
    link.compared, link.agreed = trusted[code]
    return link


def add_native_names(catalog: dict[str, Any], index: CardIndex,
                     tokens: dict[int, str]) -> tuple[dict[str, Any], LinkReport]:
    """Return a copy of ``catalog`` whose linked products carry ``nameNative``, and a report."""
    out = copy.deepcopy(catalog)
    products = out.get("products") or []
    by_set: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for product in products:
        by_set[product.get("setId")].append(product)
    species_words = set(tokens.values())
    report = LinkReport(rows_total=len(products))
    for set_row in out.get("sets") or []:
        rows = by_set.get(set_row.get("setId"), [])
        link = resolve_set(set_row, rows, index, tokens)
        report.sets.append(link)
        if not link.trusted:
            continue
        cards = index[link.code]
        for product in rows:
            if clean_name(product.get("nameNative")):
                continue
            card = _slot_card(cards.get(number_key(product.get("collectorNumber")) or ""))
            if card and _row_accepts(card, str(product.get("name") or ""), tokens, species_words):
                product["nameNative"] = card.name
                link.named += 1
    report.rows_named = sum(1 for p in products if clean_name(p.get("nameNative")))
    return out, report


def _read_catalog(path: Path) -> dict[str, Any]:
    opener = gzip.open if path.name.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def _summary(report: LinkReport) -> str:
    trusted = [s for s in report.sets if s.trusted]
    routes = Counter(s.route for s in trusted)
    share = report.rows_named / report.rows_total if report.rows_total else 0.0
    return (
        f"Japanese names: {report.rows_named}/{report.rows_total} rows ({share:.1%}); "
        f"{len(trusted)}/{len(report.sets)} sets linked ("
        + ", ".join(f"{route} {count}" for route, count in sorted(routes.items()))
        + ")"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tcgjson.jp_native",
        description="Add official Japanese names (nameNative) to pokemon-japan.json.gz from PTCG-database.",
    )
    parser.add_argument("--catalog", type=Path, required=True, help="pokemon-japan.json.gz to read")
    parser.add_argument("--ptcg-root", type=Path, required=True,
                        help="PTCG-database checkout holding data_jp and data_en")
    parser.add_argument("--output", type=Path, help="where to write (default: overwrite --catalog)")
    parser.add_argument("--report", type=Path, help="also write the per-set link report as JSON")
    parser.add_argument("--min-share", type=float, default=0.5,
                        help="fail when fewer than this share of rows get a name (default 0.5)")
    parser.add_argument("--min-jp-cards", type=int, default=10000,
                        help="fail when data_jp has fewer cards than this (default 10000)")
    args = parser.parse_args(argv)

    jp_records = load_ptcg_folder(args.ptcg_root / "data_jp")
    en_records = load_ptcg_folder(args.ptcg_root / "data_en")
    if len(jp_records) < args.min_jp_cards or not en_records:
        print(f"error: PTCG-database checkout looks incomplete: {len(jp_records)} data_jp cards, "
              f"{len(en_records)} data_en cards", file=sys.stderr)
        return 1
    catalog = _read_catalog(args.catalog)
    named, report = add_native_names(catalog, build_card_index(jp_records), build_species_tokens(en_records))
    print(_summary(report))
    if args.report:
        atomic_write_json(args.report, report.to_json())
    share = report.rows_named / report.rows_total if report.rows_total else 0.0
    if share < args.min_share:
        print(f"error: only {share:.1%} of rows got a Japanese name (minimum {args.min_share:.0%}); "
              "catalog not written", file=sys.stderr)
        return 1
    atomic_write_json_gzip(args.output or args.catalog, named)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
