"""Add official Japanese card names (``nameNative``) to the Pokemon Japan catalog.

TCGplayer's "Pokemon Japan" product line has English names only. PTCG-database
(github.com/type-null/PTCG-database, MIT licence) has every card of pokemon-card.com
with its Japanese name, set code and collector number in ``data_jp``. This module
links a TCGplayer row to the PTCG card at the same set code and number, and only
inside sets where that link is *trusted*.

TCGplayer rows carry no HP or Pokedex number, so trust compares the PTCG card's
Pokedex number, turned into an English species word with ``data_en``, against the
TCGplayer English name:

* A row's *words* are its name without the " - 001/073" tail and without anything in
  parentheses, split on spaces and folded to ASCII ("Pikachu (Master Ball Pattern)"
  -> ["pikachu"]). A row agrees with a Pokemon card when one word is the species word,
  or the species word plus a card suffix ("pikachugx") or a Nidoran gender letter.
* Rows whose collector number names another set ("028/DPt-P" when the code is not
  DPt-P) are ignored.
* A set is trusted against a PTCG code when at least 5 distinct collector numbers sit
  on a PTCG Pokemon card with a known Pokedex number, and at least 95% of those numbers
  have every row printed there agreeing.
* Every PTCG code is scored. The named candidates are the TCGplayer abbreviation
  (exact, then ignoring case) and the set-name prefix before ``:`` (same). A trusted
  named candidate with the highest agreeing count wins. Otherwise the single best
  trusted code wins only if it has at least twice the agreeing numbers of the next
  trusted code ("content" route); anything else links nothing ("ambiguous").
* Inside a trusted set a row is still refused when PTCG has two different names at
  that number, when the card is a Pokemon the row does not agree with, when the card
  is not a Pokemon but the row's last word is a species word, when the card is an
  Energy but the row has no word "energy", or when the row's last word is "energy"
  but the card is not an Energy.

Rows that are not linked get no ``nameNative`` (a blank one in the input is dropped);
nothing else in the file changes.
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
# A content-route winner needs this many times the agreeing numbers of the runner-up.
MIN_MARGIN = 2
# A word shared by the names of more than this many species is not a species word
# ("Galarian", "Team", "Rocket's").
MAX_SPECIES_PER_WORD = 2
# Card-name suffixes; dropped before looking at a row's last word.
NAME_SUFFIXES = {"ex", "v", "vmax", "vstar", "gx", "break", "lvx", "prime", "legend", "star"}
# What may follow a species word inside one folded word: "pikachugx", "nidoranf".
_WORD_TAILS = {"", "f", "m"} | NAME_SUFFIXES

_NUMBER = re.compile(r"0*(\d+)")
_NAME_TAIL = re.compile(r"\s+-\s+\S*\d\S*.*$")  # " - 001/073", " - 132/SV-P (Holo)"
_PARENS = re.compile(r"\([^)]*\)")
_ENERGY_JA = "エネルギー"  # エネルギー


@dataclass(frozen=True)
class JpCard:
    name: str
    kind: str  # "pokemon", "energy" or "trainer"
    dex: int | None

    @property
    def is_pokemon(self) -> bool:
        return self.kind == "pokemon"


# code -> collector number key -> cards printed there
CardIndex = dict[str, dict[str, tuple[JpCard, ...]]]


@dataclass(frozen=True)
class Row:
    key: str  # collector number key ("1" for "001/073")
    suffix: str | None  # folded part after "/" when it has letters ("svp" for "132/SV-P")
    words: tuple[str, ...]


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


def card_kind(record: dict[str, Any]) -> str:
    card_type = unicodedata.normalize("NFC", str(record.get("card_type") or ""))
    if card_type == "Pokémon":
        return "pokemon"
    return "energy" if _ENERGY_JA in card_type else "trainer"


def _dex(value: Any) -> int | None:
    try:
        dex = int(value)
    except (TypeError, ValueError):
        return None
    return dex if dex > 0 else None


def load_ptcg_folder(folder: Path) -> tuple[list[dict[str, Any]], int]:
    """(card objects under ``folder``, number of JSON files skipped as unreadable)."""
    records, skipped = [], 0
    for path in sorted(Path(folder).rglob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            skipped += 1
            continue
        if isinstance(record, dict):
            records.append(record)
        else:
            skipped += 1
    return records, skipped


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
        if name and dex and card_kind(record) == "pokemon":
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
        card = JpCard(name=name, kind=card_kind(record), dex=_dex(record.get("pokedex_number")))
        index[code.strip()][number].append(card)
    return {code: {n: tuple(cards) for n, cards in by_number.items()} for code, by_number in index.items()}


def _slot_card(cards: tuple[JpCard, ...] | None) -> JpCard | None:
    """The card at a slot, or None when the slot is empty or holds two different names."""
    if not cards or len({c.name for c in cards}) != 1:
        return None
    return cards[0]


def row_words(row_name: str) -> tuple[str, ...]:
    """Folded words of a row name without its number tail and parentheses."""
    name = _PARENS.sub(" ", _NAME_TAIL.sub("", row_name))
    return tuple(w for w in (fold(word) for word in name.split()) if w)


def _row(product: dict[str, Any]) -> Row | None:
    number = product.get("collectorNumber")
    key = number_key(number)
    if key is None:
        return None
    text = str(number)
    tail = text.split("/", 1)[1].strip() if "/" in text else ""
    suffix = fold(tail) if re.search(r"[A-Za-z]", tail) else None
    return Row(key=key, suffix=suffix, words=row_words(str(product.get("name") or "")))


def _row_in_code(row: Row, code: str) -> bool:
    return row.suffix is None or row.suffix == fold(code)


def _species_agrees(card: JpCard, words: tuple[str, ...], tokens: dict[int, str]) -> bool | None:
    """True/False for a Pokemon card with a known species word, None when not comparable."""
    if not card.is_pokemon or card.dex not in tokens:
        return None
    token = tokens[card.dex]
    return any(w.startswith(token) and w[len(token):] in _WORD_TAILS for w in words)


def _row_accepts(card: JpCard, words: tuple[str, ...], tokens: dict[int, str],
                 species_words: set[str]) -> bool:
    if card.kind == "energy":
        return "energy" in words
    if words and words[-1] == "energy":
        return False
    agrees = _species_agrees(card, words, tokens)
    if agrees is not None:
        return agrees
    if card.is_pokemon:
        return True  # a Pokemon without a usable Pokedex number inherits the set's trust
    trimmed = list(words)
    while trimmed and trimmed[-1] in NAME_SUFFIXES:
        trimmed.pop()
    return not (trimmed and trimmed[-1] in species_words)


def _trust(rows: list[Row], code: str, cards: dict[str, tuple[JpCard, ...]],
           tokens: dict[int, str]) -> tuple[int, int]:
    """(numbers compared, numbers agreeing) for one PTCG code, over Pokemon slots with a
    species word; a number agrees when every row printed at it agrees."""
    verdicts: dict[str, bool] = {}
    for row in rows:
        if not _row_in_code(row, code):
            continue
        card = _slot_card(cards.get(row.key))
        if card is None:
            continue
        agrees = _species_agrees(card, row.words, tokens)
        if agrees is not None:
            verdicts[row.key] = verdicts.get(row.key, True) and agrees
    return len(verdicts), sum(verdicts.values())


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
    """Pick the PTCG set code for one TCGplayer set, or none (see the module docstring)."""
    link = SetLink(set_id=set_row.get("setId"), set_name=str(set_row.get("name") or ""),
                   abbreviation=str(set_row.get("abbreviation") or ""), code=None, route="none",
                   rows=len(products))
    rows = [r for r in (_row(p) for p in products) if r is not None]
    named = _candidates(set_row, index)
    scores = {code: _trust(rows, code, index[code], tokens) for code in index}
    trusted = {code: score for code, score in scores.items() if _is_trusted(*score)}
    if not trusted:
        if named:  # report the first named candidate's numbers
            code = named[0][0]
            link.route = "untrusted:" + code
            link.compared, link.agreed = scores[code]
        return link
    ranked = sorted(trusted, key=lambda c: (-trusted[c][1], c))
    best = trusted[ranked[0]][1]
    named_best = [(c, r) for c, r in named if c in trusted and trusted[c][1] == best]
    if named_best:
        code, route = named_best[0]
    elif len(ranked) == 1 or best >= MIN_MARGIN * trusted[ranked[1]][1]:
        code, route = ranked[0], "content"
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
        if "nameNative" in product and not clean_name(product["nameNative"]):
            del product["nameNative"]
        by_set[product.get("setId")].append(product)
    species_words = set(tokens.values())
    report = LinkReport(rows_total=len(products))
    for set_row in out.get("sets") or []:
        set_products = by_set.get(set_row.get("setId"), [])
        link = resolve_set(set_row, set_products, index, tokens)
        report.sets.append(link)
        if not link.trusted:
            continue
        cards = index[link.code]
        for product in set_products:
            if "nameNative" in product:
                continue
            row = _row(product)
            if row is None or not _row_in_code(row, link.code):
                continue
            card = _slot_card(cards.get(row.key))
            if card and _row_accepts(card, row.words, tokens, species_words):
                product["nameNative"] = card.name
                link.named += 1
    report.rows_named = sum(1 for p in products if "nameNative" in p)
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
    parser.add_argument("--min-jp-cards", type=int, default=20000,
                        help="fail when data_jp has fewer cards than this (default 20000)")
    parser.add_argument("--min-en-cards", type=int, default=18000,
                        help="fail when data_en has fewer cards than this (default 18000)")
    parser.add_argument("--min-species", type=int, default=1000,
                        help="fail when data_en names fewer species than this (default 1000)")
    args = parser.parse_args(argv)

    jp_records, jp_skipped = load_ptcg_folder(args.ptcg_root / "data_jp")
    en_records, en_skipped = load_ptcg_folder(args.ptcg_root / "data_en")
    tokens = build_species_tokens(en_records)
    print(f"PTCG-database: {len(jp_records)} data_jp cards ({jp_skipped} files skipped), "
          f"{len(en_records)} data_en cards ({en_skipped} skipped), {len(tokens)} species words")
    if (len(jp_records) < args.min_jp_cards or len(en_records) < args.min_en_cards
            or len(tokens) < args.min_species):
        print("error: PTCG-database checkout looks incomplete (see the counts above); "
              "catalog not written", file=sys.stderr)
        return 1
    catalog = _read_catalog(args.catalog)
    named, report = add_native_names(catalog, build_card_index(jp_records), tokens)
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
