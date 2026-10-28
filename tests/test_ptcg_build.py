from __future__ import annotations

import gzip
import json

import pytest
from ptcg_helpers import write_card, write_japan_catalog, write_species

from tcgjson.ptcg_build import GateError, build_languages, check_gates, coverage_line
from tcgjson.ptcg_import import LANGUAGE_BY_CODE


def _korean_tree(tmp_path):
    root = tmp_path / "ptcg"
    write_species(root, {"pikachu": 25})
    for n in range(1, 6):
        write_card(root, "jp", f"M1/{n}", name="j", number=f"{n:03d}", set_name="M1",
                   card_type="Pokémon", print_key=f"asia:M1-{n}", pokedex_number=n + 20, hp=60)
        write_card(root, "ko", f"M1/{n:03d}", name=f"몬{n}", number=f"{n:03d}", set_name="M1",
                   set_total="010", set_full_name="메가 브레이브", card_type="Pokémon",
                   print_key=f"asia:M1-{n}", pokedex_number=n + 20, hp=60, rarity="C",
                   img=f"https://img/ko/M1/{n}.png")
    # two Korean prints at the same number that both resolve to the same English name
    write_card(root, "ko", "M1/005b", name="몬5", number="005", set_name="M1", set_total="010",
               set_full_name="메가 브레이브", card_type="Pokémon", print_key="asia:M1-5",
               pokedex_number=25, hp=60, rarity="RR")
    write_card(root, "ko", "K9/001", name="한국 전용", number="001", set_name="K9",
               set_total="020", set_full_name="한국 세트", card_type="서포트", print_key="asia:K9-1")
    japan = write_japan_catalog(
        tmp_path / "japan.json.gz",
        [{"setId": 7, "name": "M1: Mega Brave", "abbreviation": "M1"}],
        [{"name": f"Mon{n} - {n:03d}/010", "setId": 7, "collectorNumber": f"{n:03d}/010"}
         for n in range(1, 5)]
        + [{"name": "Pikachu - 005/010", "setId": 7, "collectorNumber": "005/010"}],
    )
    return root, japan


def _read(path):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def test_build_writes_tcgjson_shape_with_extra_fields(tmp_path):
    root, japan = _korean_tree(tmp_path)
    out = tmp_path / "release"
    report = build_languages(root, out, ["ko"], japan_catalog=japan, translations_dir=None,
                             tcgdex_snapshot=None, source_commit="abc123")
    data = _read(out / "pokemon-korean.json.gz")
    assert data["meta"]["slug"] == "pokemon-korean"
    assert data["meta"]["language"] == "ko"
    assert data["meta"]["source"]["commit"] == "abc123"
    sets = {s["abbreviation"]: s for s in data["sets"]}
    assert sets["M1"]["name"] == "M1: Mega Brave"
    assert sets["K9"]["name"] == "K9: 한국 세트"
    products = {p["sourceId"]: p for p in data["products"]}
    mon1 = products["ko:M1/001"]
    assert mon1 == {
        "name": "Mon1",
        "nameNative": "몬1",
        "nameSource": "linked_ja",
        "sourceId": "ko:M1/001",
        "setId": sets["M1"]["setId"],
        "collectorNumber": "001/010",
        "rarity": "C",
        "imageUrls": ["https://img/ko/M1/1.png"],
    }
    assert products["ko:M1/005"]["name"] == "Pikachu"
    # same set, number and name: the later row gets a rarity suffix, nothing is dropped
    assert products["ko:M1/005b"]["name"] == "Pikachu (RR)"
    assert products["ko:M1/005b"]["nameAlias"] == "Pikachu"
    assert products["ko:K9/001"]["nameSource"] == "native"
    assert products["ko:K9/001"]["name"] == "한국 전용"
    assert data["meta"]["coverage"]["ko"] == {
        "official": 0, "linked_en": 0, "linked_ja": 6, "translated": 0, "native": 1, "total": 7}
    assert report["ko"]["coverage"]["linked_ja"] == 6
    assert "linked 85.7%" in coverage_line("ko", data["meta"]["coverage"]["ko"])


def test_collision_suffix_is_rarity_then_source_id_then_counter(tmp_path):
    root, japan = _korean_tree(tmp_path)
    write_card(root, "ko", "M1/005c", name="몬5", number="005", set_name="M1", set_total="010",
               card_type="Pokémon", print_key="asia:M1-5", pokedex_number=25, hp=60,
               ko_id="BS0005c")
    write_card(root, "ko", "M1/005d", name="몬5", number="005", set_name="M1", set_total="010",
               card_type="Pokémon", print_key="asia:M1-5", pokedex_number=25, hp=60, rarity="RR",
               url="https://site/cards/detail/777/")
    write_card(root, "ko", "M1/005e", name="몬5", number="005", set_name="M1", set_total="010",
               card_type="Pokémon", print_key="asia:M1-5", pokedex_number=25, hp=60, rarity="RR",
               url="https://site/cards/detail/777/")
    out = tmp_path / "release"
    build_languages(root, out, ["ko"], japan_catalog=japan, translations_dir=None,
                    tcgdex_snapshot=None)
    names = sorted(p["name"] for p in _read(out / "pokemon-korean.json.gz")["products"]
                   if p["collectorNumber"] == "005/010")
    assert names == ["Pikachu", "Pikachu (2)", "Pikachu (777)", "Pikachu (BS0005c)",
                     "Pikachu (RR)"]


def test_korean_promos_split_by_series_and_placeholder_totals_are_dropped(tmp_path):
    root, japan = _korean_tree(tmp_path)
    write_card(root, "ko", "promo/001", name="프로모", number="001", set_name="promo",
               set_total="SV-P", card_type="Pokémon", print_key="asia:promo-1")
    write_card(root, "ko", "promo/001-2", name="프로모2", number="001", set_name="promo",
               set_total="S-P", card_type="Pokémon", print_key="asia:promo-1")
    write_card(root, "ko", "no_set/DAR", name="기본 악 에너지", number="DAR", set_total="-1",
               card_type="기본 에너지")
    out = tmp_path / "release"
    build_languages(root, out, ["ko"], japan_catalog=japan, translations_dir=None,
                    tcgdex_snapshot=None)
    data = _read(out / "pokemon-korean.json.gz")
    sets = {s["setId"]: s["abbreviation"] for s in data["sets"]}
    rows = {p["sourceId"]: (sets[p["setId"]], p["collectorNumber"]) for p in data["products"]}
    assert rows["ko:promo/001"] == ("SV-P", "001/SV-P")
    assert rows["ko:promo/001-2"] == ("S-P", "001/S-P")
    assert rows["ko:no_set/DAR"] == ("no_set", "DAR")


def test_gate_linked_ja_outside_a_trusted_set_fails():
    products = [{"sourceId": "ko:X/1", "nameSource": "linked_ja", "name": "A", "setCode": "X"}]
    with pytest.raises(GateError, match="linked_ja"):
        check_gates("ko", products, {"X": "untrusted"}, previous=None, overridden=set())


def test_gate_row_loss_over_five_percent_fails():
    previous = {"products": [{"sourceId": f"ko:{i}", "name": "a", "nameSource": "native"}
                             for i in range(100)]}
    products = [{"sourceId": f"ko:{i}", "name": "a", "nameSource": "native", "setCode": "S"}
                for i in range(94)]
    with pytest.raises(GateError, match="rows"):
        check_gates("ko", products, {}, previous=previous, overridden=set())
    check_gates("ko", products + [{"sourceId": "ko:x", "name": "b", "nameSource": "native",
                                   "setCode": "S"}], {}, previous=previous, overridden=set())


def test_gate_translated_name_must_not_change_unless_overridden():
    previous = {"products": [{"sourceId": "ko:1", "name": "Old", "nameSource": "translated"}]}
    products = [{"sourceId": "ko:1", "name": "New", "nameSource": "translated", "setCode": "S"}]
    with pytest.raises(GateError, match="translated"):
        check_gates("ko", products, {}, previous=previous, overridden=set())
    check_gates("ko", products, {}, previous=previous, overridden={"ko:1"})


def test_build_skips_a_failing_language_but_writes_the_others(tmp_path):
    root, japan = _korean_tree(tmp_path)
    write_card(root, "de", "sv01/001", name="Pikachu", number="001", set_name="sv01",
               set_total=258, card_type="Pokémon", print_key="tcgdex:sv01-1", pokedex_number=25)
    prev = tmp_path / "previous"
    prev.mkdir()
    with gzip.open(prev / "pokemon-korean.json.gz", "wt", encoding="utf-8") as fh:
        json.dump({"products": [{"sourceId": f"ko:{i}", "name": "x", "nameSource": "native"}
                                for i in range(100)]}, fh)
    out = tmp_path / "release"
    report = build_languages(root, out, ["ko", "de"], japan_catalog=japan, translations_dir=None,
                             tcgdex_snapshot=None, previous_dir=prev)
    assert "rows" in report["ko"]["error"]
    assert not (out / "pokemon-korean.json.gz").exists()
    assert (out / "pokemon-german.json.gz").exists()
    assert LANGUAGE_BY_CODE["de"].asset == "pokemon-german.json.gz"
