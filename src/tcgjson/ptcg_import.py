"""Read PTCG-database card records (github.com/type-null/PTCG-database, MIT).

One JSON file per card under ``data_<folder>/<set>/<n>.json``. TCG Pocket records
(``print_key`` starting with ``pocket:``) are skipped. Nothing from the repository is
executed; only its JSON files are read.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class Language:
    code: str  # app registry code
    folder: str  # PTCG-database folder suffix (data_<folder>)
    asset: str  # release asset file name
    label: str
    group: str  # print_key group: "asia" or "tcgdex"


LANGUAGES: tuple[Language, ...] = (
    Language("ko", "ko", "pokemon-korean.json.gz", "Korean", "asia"),
    Language("zh-tw", "tc", "pokemon-chinese-traditional.json.gz", "Chinese (Traditional)", "asia"),
    Language("de", "de", "pokemon-german.json.gz", "German", "tcgdex"),
    Language("fr", "fr", "pokemon-french.json.gz", "French", "tcgdex"),
    Language("es", "es", "pokemon-spanish.json.gz", "Spanish", "tcgdex"),
    Language("it", "it", "pokemon-italian.json.gz", "Italian", "tcgdex"),
    Language("pt", "pt", "pokemon-portuguese.json.gz", "Portuguese", "tcgdex"),
)

LANGUAGE_BY_CODE = {lang.code: lang for lang in LANGUAGES}

# Words that mark an energy card in each source language's ``card_type``.
_ENERGY_WORDS = ("energy", "에너지", "能量", "エネルギー", "energie", "énergie", "energía", "energia")


@dataclass(slots=True)
class Card:
    source_id: str
    name: str
    set_code: str
    set_full_name: str
    number: str  # normalized: leading zeros dropped
    collector_number: str
    rarity: str | None
    kind: str  # pokemon | trainer | energy
    dex: int | None
    hp: str | None
    print_key: str | None
    card_key: str | None
    img: str | None
    rules_text: str | None
    stable_ref: str = ""  # the source site's own card id, for collision suffixes
    raw: dict[str, Any] = field(repr=False, default_factory=dict)


def norm_number(value: Any) -> str:
    """Card number with leading zeros dropped ("007" -> "7", "000" -> "0")."""
    text = str(value).strip() if value is not None else ""
    if text.isdigit():
        return text.lstrip("0") or "0"
    return text


def _total(total: Any) -> str:
    """The set total as printed, or "" when missing or a placeholder ("0", "-1", "None")."""
    tot = str(total).strip() if total not in (None, "") else ""
    if not tot or tot == "None" or tot.startswith("-") or (tot.isdigit() and int(tot) == 0):
        return ""
    return tot


def collector_number(number: Any, total: Any) -> str:
    """``NNN/TTT``: the printed number over the set total, padded to the number's width."""
    num = str(number).strip() if number is not None else ""
    if not num or num == "None":
        return ""
    tot = _total(total)
    if not tot:
        return num
    if tot.isdigit() and num.isdigit():
        tot = tot.zfill(len(num))
    return f"{num}/{tot}"


def card_kind(card_type: Any, dex: Any) -> str:
    """pokemon when there is a Pokedex number or the type is Pokémon, energy by keyword, else trainer."""
    if dex not in (None, "", "None", 0):
        return "pokemon"
    text = str(card_type or "").strip().casefold()
    if text in ("pokémon", "pokemon"):
        return "pokemon"
    if any(word in text for word in _ENERGY_WORDS):
        return "energy"
    return "trainer"


def _dex(value: Any) -> int | None:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _hp(value: Any) -> str | None:
    if value in (None, "", "None"):
        return None
    return str(value).strip()


def _rules_text(record: dict[str, Any]) -> str | None:
    effect = record.get("effect")
    if isinstance(effect, str) and effect.strip():
        return effect.strip()
    if isinstance(effect, list):
        text = " ".join(str(part) for part in effect if part)
        return text.strip() or None
    return None


def _stable_ref(record: dict[str, Any], rel: str) -> str:
    """The source site's card id (Korean ``ko_id``, else the last part of the card URL)."""
    if record.get("ko_id"):
        return str(record["ko_id"]).strip()
    url = str(record.get("url") or "").rstrip("/")
    if url:
        return url.rsplit("/", 1)[-1]
    return rel.rsplit("/", 1)[-1]


def load_language(root: Path, code_or_folder: str) -> list[Card]:
    """Every physical card of one language folder, sorted by source id."""
    lang = LANGUAGE_BY_CODE.get(code_or_folder)
    folder = lang.folder if lang else code_or_folder
    code = lang.code if lang else code_or_folder
    base = Path(root) / f"data_{folder}"
    cards: list[Card] = []
    for dirpath, _dirnames, filenames in os.walk(base):
        for filename in filenames:
            if not filename.endswith(".json"):
                continue
            path = Path(dirpath) / filename
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(record, dict):
                continue
            print_key = record.get("print_key") or None
            if isinstance(print_key, str) and print_key.startswith("pocket:"):
                continue
            rel = path.relative_to(base).with_suffix("").as_posix()
            dex = _dex(record.get("pokedex_number"))
            set_code = str(record.get("set_name") or rel.split("/")[0]).strip()
            total = _total(record.get("set_total"))
            if set_code == "promo" and total and not total.isdigit():
                # Korean promos of every series share the folder "promo"; the series code
                # (SV-P, S-P, ...) is printed where the set total would be.
                set_code = total
            rarity = record.get("rarity")
            cards.append(
                Card(
                    source_id=f"{code}:{rel}",
                    name=str(record.get("name") or "").strip(),
                    set_code=set_code,
                    set_full_name=str(record.get("set_full_name") or "").strip(),
                    number=norm_number(record.get("number")),
                    collector_number=collector_number(record.get("number"), record.get("set_total")),
                    rarity=str(rarity).strip() if rarity not in (None, "") else None,
                    kind=card_kind(record.get("card_type"), dex),
                    dex=dex,
                    hp=_hp(record.get("hp")),
                    print_key=print_key,
                    card_key=record.get("card_key") or None,
                    img=record.get("img") or None,
                    rules_text=_rules_text(record),
                    stable_ref=_stable_ref(record, rel),
                    raw=record,
                )
            )
    cards.sort(key=lambda card: card.source_id)
    return cards


_SPECIES_SPECIAL = {
    "ho-oh": "Ho-Oh",
    "porygon-z": "Porygon-Z",
    "jangmo-o": "Jangmo-o",
    "hakamo-o": "Hakamo-o",
    "kommo-o": "Kommo-o",
    "mime-jr": "Mime Jr.",
    "mr-mime": "Mr. Mime",
    "mr-rime": "Mr. Rime",
    "nidoran-f": "Nidoran♀",
    "nidoran-m": "Nidoran♂",
    "type-null": "Type: Null",
    "chi-yu": "Chi-Yu",
    "chien-pao": "Chien-Pao",
    "ting-lu": "Ting-Lu",
    "wo-chien": "Wo-Chien",
    "farfetchd": "Farfetch'd",
    "sirfetchd": "Sirfetch'd",
    "flabebe": "Flabébé",
}


def species_display(slug: str) -> str:
    if slug in _SPECIES_SPECIAL:
        return _SPECIES_SPECIAL[slug]
    return " ".join(part[:1].upper() + part[1:] for part in slug.replace("-", " ").split())


def load_species(root: Path) -> dict[int, str]:
    """Pokedex number -> English species name, from ``code/species_dex.json``."""
    path = Path(root) / "code" / "species_dex.json"
    try:
        table = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    species: dict[int, str] = {}
    for slug, dex in sorted(table.items()):
        number = _dex(dex)
        if number is not None and number not in species:
            species[number] = species_display(slug)
    return species
