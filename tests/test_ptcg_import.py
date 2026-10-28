from __future__ import annotations

from ptcg_helpers import write_card, write_species

from tcgjson.ptcg_import import (
    LANGUAGES,
    card_kind,
    collector_number,
    load_language,
    load_species,
    norm_number,
)


def test_languages_table_has_the_seven_phase_2a_languages():
    assert [lang.code for lang in LANGUAGES] == ["ko", "zh-tw", "de", "fr", "es", "it", "pt"]
    by_code = {lang.code: lang for lang in LANGUAGES}
    assert by_code["zh-tw"].folder == "tc"
    assert by_code["zh-tw"].asset == "pokemon-chinese-traditional.json.gz"
    assert by_code["ko"].asset == "pokemon-korean.json.gz"
    assert by_code["pt"].asset == "pokemon-portuguese.json.gz"
    assert by_code["ko"].group == "asia" and by_code["de"].group == "tcgdex"


def test_load_language_skips_pocket_and_bad_files_and_sorts(tmp_path):
    write_card(tmp_path, "de", "sv01/002", name="Zwei", number="002", set_name="sv01",
               set_total=258, card_type="Pokémon", print_key="tcgdex:sv01-2", pokedex_number=2)
    write_card(tmp_path, "de", "sv01/001", name="Eins", number="001", set_name="sv01",
               set_total=258, card_type="Trainer", print_key="tcgdex:sv01-1")
    write_card(tmp_path, "de", "A1/001", name="Pocket", number="001", set_name="A1",
               print_key="pocket:A1-1")
    bad = tmp_path / "data_de" / "sv01" / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    cards = load_language(tmp_path, "de")
    assert [c.source_id for c in cards] == ["de:sv01/001", "de:sv01/002"]
    first = cards[0]
    assert first.name == "Eins" and first.set_code == "sv01" and first.kind == "trainer"
    assert first.number == "1" and first.collector_number == "001/258"
    assert cards[1].kind == "pokemon" and cards[1].dex == 2


def test_card_kind_uses_dex_then_localized_energy_words():
    assert card_kind("Pokémon", None) == "pokemon"
    assert card_kind("V진화", 25) == "pokemon"
    assert card_kind("기본 에너지", None) == "energy"
    assert card_kind("基本能量卡", None) == "energy"
    assert card_kind("Énergie", None) == "energy"
    assert card_kind("Energía", None) == "energy"
    assert card_kind("서포트", None) == "trainer"
    assert card_kind("", None) == "trainer"


def test_collector_number_and_norm_number():
    assert collector_number("001", "063") == "001/063"
    assert collector_number("001", 158) == "001/158"
    assert collector_number("1", "149") == "1/149"
    assert collector_number("SV-P", None) == "SV-P"
    assert collector_number("12", "") == "12"
    assert collector_number(None, "10") == ""
    assert norm_number("007") == "7"
    assert norm_number("000") == "0"
    assert norm_number("SWSH001") == "SWSH001"
    assert norm_number(12) == "12"


def test_load_species_maps_dex_to_title_case_name(tmp_path):
    write_species(tmp_path, {"pikachu": 25, "mr-mime": 122, "ho-oh": 250, "iron-hands": 992,
                             "farfetchd": 83, "nidoran-f": 29})
    species = load_species(tmp_path)
    assert species[25] == "Pikachu"
    assert species[122] == "Mr. Mime"
    assert species[250] == "Ho-Oh"
    assert species[992] == "Iron Hands"
    assert species[83] == "Farfetch'd"
    assert species[29] == "Nidoran♀"
