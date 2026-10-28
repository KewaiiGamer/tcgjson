from __future__ import annotations

from ptcg_helpers import write_card, write_japan_catalog, write_species

from tcgjson.english_names import (
    EnglishIndex,
    JapanLinks,
    NameContext,
    TcgdexEnglish,
    clean_english,
    resolve_names,
    tcgplayer_base_name,
    trusted_sets,
    unanimous,
)
from tcgjson.ptcg_import import LANGUAGE_BY_CODE, load_language, load_species
from tcgjson.translate import Translations


def test_clean_english_straightens_quotes_and_spaces():
    assert clean_english("Erika’s  Bellsprout ") == "Erika's Bellsprout"


def test_unanimous_folds_case_and_punctuation_and_rejects_disagreement():
    assert unanimous([]) is None
    assert unanimous(["Pikachu", "PIKACHU", "Pikachu"]) == "Pikachu"
    assert unanimous(["Professor's Research", "Professors Research"]) == "Professor's Research"
    assert unanimous(["Black Kyurem EX", "White Kyurem EX"]) is None


def test_tcgplayer_base_name_strips_numbers_and_variant_parentheses():
    assert tcgplayer_base_name(["Tropius - 001/073"]) == "Tropius"
    assert tcgplayer_base_name(
        ["Pikachu - 025/165", "Pikachu (Master Ball Pattern) - 025/165"]
    ) == "Pikachu"
    assert tcgplayer_base_name(["Pikachu - 025/165", "Raichu - 025/165"]) is None
    assert tcgplayer_base_name([]) is None
    assert tcgplayer_base_name(
        ["Bulbasaur - 001/165", "Bulbasaur - 001/165 (Master Ball Pattern)",
         "Bulbasaur - 001/165 (Poke Ball Pattern)"]
    ) == "Bulbasaur"
    assert tcgplayer_base_name(["Ekans (Master Ball Pattern)", "Ekans (Poke Ball Pattern)"]) == "Ekans"
    assert tcgplayer_base_name(["Oddish - 001/190", "Oddish - 001/190 (Mirror Holofoil)"]) == "Oddish"
    # labels that name a different card are never folded
    assert tcgplayer_base_name(["Pikachu (Delta Species)", "Pikachu"]) is None
    assert tcgplayer_base_name(["Mewtwo (Delta Species) - 012/086"]) == "Mewtwo (Delta Species)"
    # every product carries a printing label: the card name is the base
    assert tcgplayer_base_name(["Glass Trumpet (Mirror Foil)"]) == "Glass Trumpet"


def _tree(tmp_path):
    root = tmp_path / "ptcg"
    write_species(root, {"pikachu": 25, "bulbasaur": 1})
    # English cards
    write_card(root, "en", "SV/SVI/1", name="Pikachu", number="1", card_type="Pokémon",
               print_key="en:SVI-1", card_key="25|60|1|A|10|1", pokedex_number=25)
    write_card(root, "en", "SV/SVI/2", name="Black Kyurem EX", number="2", card_type="Pokémon",
               print_key="en:SVI-2", card_key="646|180|3|B|x|1", pokedex_number=646)
    write_card(root, "en", "SV/SVI/3", name="White Kyurem EX", number="3", card_type="Pokémon",
               print_key="en:SVI-3", card_key="646|180|3|B|x|1", pokedex_number=646)
    # Japanese cards: set M1 has 5 Pokemon matching Korean, set X has disagreeing ones
    for n in range(1, 6):
        write_card(root, "jp", f"M1/{n}", name=f"jp{n}", number=f"{n:03d}", set_name="M1",
                   card_type="Pokémon", print_key=f"asia:M1-{n}",
                   pokedex_number=25 if n == 1 else n, hp=60)
    write_card(root, "jp", "M1/6", name="jp-trainer", number="006", set_name="M1",
               card_type="サポート", print_key="asia:M1-6")
    for n in range(1, 6):
        write_card(root, "jp", f"X/{n}", name=f"jpx{n}", number=f"{n:03d}", set_name="X",
                   card_type="Pokémon", print_key=f"asia:X-{n}", pokedex_number=100 + n, hp=60)
    write_card(root, "jp", "X/6", name="jpx-trainer", number="006", set_name="X",
               card_type="サポート", print_key="asia:X-6")
    # Korean cards
    write_card(root, "ko", "M1/001", name="피카츄", number="001", set_name="M1", set_total="010",
               set_full_name="메가", card_type="Pokémon", print_key="asia:M1-1",
               card_key="25|60|1|A|10|1", pokedex_number=25, hp=60, rarity="C")
    for n in range(2, 6):
        write_card(root, "ko", f"M1/{n:03d}", name=f"포켓몬{n}", number=f"{n:03d}", set_name="M1",
                   set_total="010", card_type="Pokémon", print_key=f"asia:M1-{n}",
                   pokedex_number=n, hp=60)
    write_card(root, "ko", "M1/006", name="박사의 연구", number="006", set_name="M1",
               set_total="010", card_type="서포트", print_key="asia:M1-6")
    write_card(root, "ko", "M1/007", name="큐레무 EX", number="007", set_name="M1",
               set_total="010", card_type="Pokémon", print_key="asia:M1-7",
               card_key="646|180|3|B|x|1", pokedex_number=646, hp=180)
    for n in range(1, 6):
        write_card(root, "ko", f"X/{n:03d}", name=f"엑스{n}", number=f"{n:03d}", set_name="X",
                   set_total="010", card_type="Pokémon", print_key=f"asia:X-{n}",
                   pokedex_number=n, hp=60)
    write_card(root, "ko", "X/006", name="박사의 연구", number="006", set_name="X",
               set_total="010", card_type="서포트", print_key="asia:X-6")
    japan = write_japan_catalog(
        tmp_path / "pokemon-japan.json.gz",
        [{"setId": 1, "name": "M1: Mega Brave", "abbreviation": "M1"},
         {"setId": 2, "name": "Other", "abbreviation": "X"}],
        [{"name": "Pikachu - 001/010", "setId": 1, "collectorNumber": "001/010"}]
        + [{"name": f"Mon{n} - {n:03d}/010", "setId": 1, "collectorNumber": f"{n:03d}/010"}
           for n in range(2, 6)]
        + [{"name": "Professor's Research - 006/010", "setId": 1, "collectorNumber": "006/010"},
           {"name": "Xmon - 001/010", "setId": 2, "collectorNumber": "001/010"},
           {"name": "Boss - 006/010", "setId": 2, "collectorNumber": "006/010"}],
    )
    return root, japan


def _context(root, japan, code="ko", translations=None, tcgdex=None):
    english = EnglishIndex(load_language(root, "en"))
    links = JapanLinks.load(load_language(root, "jp"), japan)
    return NameContext(
        language=LANGUAGE_BY_CODE[code],
        english=english,
        tcgdex=tcgdex or TcgdexEnglish({}),
        japan=links,
        translations=translations or Translations(code, {}, {}),
        species=load_species(root),
    )


def test_trusted_sets_need_five_agreeing_pokemon(tmp_path):
    root, japan = _tree(tmp_path)
    ctx = _context(root, japan)
    trust = trusted_sets(load_language(root, "ko"), ctx.japan)
    assert trust == {"M1": "trusted", "X": "untrusted"}


def test_trust_thresholds_are_exactly_five_cards_and_95_percent():
    from tcgjson.ptcg_import import Card

    def card(code, n, dex):
        return Card(source_id=f"ko:{code}/{n}", name="x", set_code=code, set_full_name="",
                    number=str(n), collector_number=str(n), rarity=None, kind="pokemon", dex=dex,
                    hp="60", print_key=f"asia:{code}-{n}", card_key=None, img=None,
                    rules_text=None)

    jp, ko = [], []
    for n in range(4):  # A: 4 agreeing cards -> unknown
        jp.append(card("A", n, n + 1))
        ko.append(card("A", n, n + 1))
    for n in range(5):  # B: exactly 5 agreeing -> trusted
        jp.append(card("B", n, n + 1))
        ko.append(card("B", n, n + 1))
    for n in range(20):  # C: 19 of 20 agree = 95% -> trusted; D: 18 of 19 = 94.7% -> untrusted
        jp.append(card("C", n, n + 1))
        ko.append(card("C", n, n + 1 if n else 999))
    for n in range(19):
        jp.append(card("D", n, n + 1))
        ko.append(card("D", n, n + 1 if n else 999))
    links = JapanLinks.load(jp, None)
    assert trusted_sets(ko, links) == {"A": "unknown", "B": "trusted", "C": "trusted",
                                       "D": "untrusted"}


def test_tcgplayer_side_mismatch_makes_a_set_untrusted(tmp_path):
    root, japan = _tree(tmp_path)
    write_species(root, {"pikachu": 25, "ivysaur": 2, "venusaur": 3, "charmander": 4,
                         "charmeleon": 5})
    # TCGplayer's M1 is another product: its Pokemon names do not match the species
    write_japan_catalog(japan, [{"setId": 1, "name": "M1: Mega Brave", "abbreviation": "M1"}],
                        [{"name": f"Other{n} - {n:03d}/010", "setId": 1,
                          "collectorNumber": f"{n:03d}/010"} for n in range(1, 7)])
    ctx = _context(root, japan)
    assert trusted_sets(load_language(root, "ko"), ctx.japan, ctx.species)["M1"] == "untrusted"
    resolved = {r.card.source_id: r for r in resolve_names(load_language(root, "ko"), ctx)}
    assert resolved["ko:M1/006"].source != "linked_ja"


def test_tcgplayer_printed_total_mismatch_makes_a_set_untrusted(tmp_path):
    root, japan = _tree(tmp_path)
    write_japan_catalog(japan, [{"setId": 1, "name": "M1: Mega Brave", "abbreviation": "M1"}],
                        [{"name": f"Mon{n} - {n:03d}/012", "setId": 1,
                          "collectorNumber": f"{n:03d}/012"} for n in range(1, 7)])
    ctx = _context(root, japan)
    assert trusted_sets(load_language(root, "ko"), ctx.japan, ctx.species)["M1"] == "untrusted"


def test_resolve_names_routes(tmp_path):
    root, japan = _tree(tmp_path)
    translations = Translations("ko", {"trainer|박사의 연구": {"en": "Professor's Research",
                                                            "source": "machine"}}, {})
    ctx = _context(root, japan, translations=translations)
    resolved = {r.card.source_id: r for r in resolve_names(load_language(root, "ko"), ctx)}
    pika = resolved["ko:M1/001"]
    assert (pika.name, pika.source, pika.alias) == ("Pikachu", "linked_en", "Pikachu")
    mon2 = resolved["ko:M1/002"]
    assert (mon2.name, mon2.source) == ("Mon2", "linked_ja")
    trainer = resolved["ko:M1/006"]
    assert (trainer.name, trainer.source, trainer.alias) == ("Professor's Research", "linked_ja", None)
    # ambiguous card_key (Black/White Kyurem) and no Japanese card at 7: not linked
    kyurem = resolved["ko:M1/007"]
    assert kyurem.source == "native" and kyurem.name == "큐레무 EX"
    # untrusted set: never linked_ja, translated from the cache
    x_trainer = resolved["ko:X/006"]
    assert (x_trainer.name, x_trainer.source) == ("Professor's Research", "translated")
    x1 = resolved["ko:X/001"]
    assert x1.source == "native" and x1.alias == "Bulbasaur"


def test_pokemon_that_disagrees_with_its_japanese_card_is_not_linked(tmp_path):
    root, japan = _tree(tmp_path)
    # 20 more agreeing Pokemon keep M1 trusted (25 of 26 agree, 96%)
    for n in range(100, 120):
        write_card(root, "jp", f"M1/{n}", name="j", number=f"{n:03d}", set_name="M1",
                   card_type="Pokémon", print_key=f"asia:M1-{n}", pokedex_number=n, hp=60)
        write_card(root, "ko", f"M1/{n:03d}", name="k", number=f"{n:03d}",
                   set_name="M1", card_type="Pokémon", print_key=f"asia:M1-{n}",
                   pokedex_number=n, hp=60)
    write_card(root, "jp", "M1/30", name="j", number="030", set_name="M1", card_type="Pokémon",
               print_key="asia:M1-30", pokedex_number=30, hp=60)
    write_card(root, "ko", "M1/030", name="다른", number="030", set_name="M1",
               card_type="Pokémon", print_key="asia:M1-30", pokedex_number=31, hp=60)
    write_japan_catalog(japan, [{"setId": 1, "name": "M1: Mega Brave", "abbreviation": "M1"}],
                        [{"name": "Thirty - 030/010", "setId": 1, "collectorNumber": "030/010"}])
    ctx = _context(root, japan)
    resolved = {r.card.source_id: r for r in resolve_names(load_language(root, "ko"), ctx)}
    assert resolved["ko:M1/030"].source == "native"


def test_linked_ja_pokemon_name_must_contain_its_species(tmp_path):
    root, japan = _tree(tmp_path)
    # Korean M1/001 is Pikachu (dex 25); TCGplayer names number 1 "Super Rod": never linked.
    write_japan_catalog(japan, [{"setId": 1, "name": "M1: Mega Brave", "abbreviation": "M1"}],
                        [{"name": "Super Rod - 001/010", "setId": 1, "collectorNumber": "001/010"},
                         {"name": "Bulbasaur - 002/010", "setId": 1, "collectorNumber": "002/010"}])
    write_species(root, {"pikachu": 25, "ivysaur": 2})
    ctx = _context(root, japan)
    ctx.english = EnglishIndex([])
    resolved = {r.card.source_id: r for r in resolve_names(load_language(root, "ko"), ctx)}
    assert resolved["ko:M1/001"].source == "native"
    assert resolved["ko:M1/002"].source == "native"  # dex 2 is Ivysaur, not Bulbasaur


def test_overrides_by_source_id_win_and_by_name_beat_the_cache(tmp_path):
    root, japan = _tree(tmp_path)
    translations = Translations(
        "ko",
        {"trainer|박사의 연구": {"en": "Prof Research", "source": "machine"}},
        {"ko:M1/001": {"en": "Pikachu (Fixed)"},
         "trainer|박사의 연구": {"en": "Professor's Research"}},
    )
    ctx = _context(root, japan, translations=translations)
    resolved = {r.card.source_id: r for r in resolve_names(load_language(root, "ko"), ctx)}
    assert (resolved["ko:M1/001"].name, resolved["ko:M1/001"].source) == (
        "Pikachu (Fixed)", "translated")
    assert resolved["ko:X/006"].name == "Professor's Research"


def test_european_route_one_uses_tcgdex_english_when_ptcg_english_has_nothing(tmp_path):
    root = tmp_path / "ptcg"
    write_species(root, {"pikachu": 25})
    write_card(root, "en", "SV/SVI/1", name="Pikachu", number="1", card_type="Pokémon",
               print_key="en:SVI-1", card_key="25|60|1|A|10|1", pokedex_number=25)
    write_card(root, "de", "sv01/001", name="Pikachu", number="001", set_name="sv01",
               set_total=258, card_type="Pokémon", print_key="tcgdex:sv01-1",
               card_key="25|60|1|A|10|1", pokedex_number=25)
    write_card(root, "de", "sv01/002", name="Professorin", number="002", set_name="sv01",
               set_total=258, card_type="Trainer", print_key="tcgdex:sv01-2")
    write_card(root, "de", "sv01/003", name="Unbekannt", number="003", set_name="sv01",
               set_total=258, card_type="Trainer", print_key="tcgdex:sv01-3")
    tcgdex = TcgdexEnglish({"sv01": {"name": "Scarlet & Violet", "abbreviation": "SVI",
                                     "cards": {"2": "Professor’s Research"}}})
    ctx = NameContext(language=LANGUAGE_BY_CODE["de"],
                      english=EnglishIndex(load_language(root, "en")), tcgdex=tcgdex, japan=None,
                      translations=Translations("de", {}, {}), species=load_species(root))
    resolved = {r.card.source_id: r for r in resolve_names(load_language(root, "de"), ctx)}
    assert resolved["de:sv01/001"].source == "linked_en"
    assert (resolved["de:sv01/002"].name, resolved["de:sv01/002"].source) == (
        "Professor's Research", "linked_en")
    assert resolved["de:sv01/003"].source == "native"
