import gzip
import json
import unicodedata

import pytest

from tcgjson.jp_native import (
    add_native_names,
    build_card_index,
    build_species_tokens,
    load_ptcg_folder,
    main,
    number_key,
)

POKEMON = "Pokémon"
TRAINER = "サポート"  # "Supporter"

# Dex -> (English name, Japanese name)
SPECIES = {
    1: ("Bulbasaur", "フシギダネ"),
    4: ("Charmander", "ヒトカゲ"),
    7: ("Squirtle", "ゼニガメ"),
    25: ("Pikachu", "ピカチュウ"),
    133: ("Eevee", "イーブイ"),
    143: ("Snorlax", "カビゴン"),
}
ORDER = [1, 4, 7, 25, 133, 143]


def en_card(name, dex):
    return {"name": name, "card_type": POKEMON, "card_key": f"{dex}|60|1|Someone|10|1"}


def en_records():
    out = [en_card(SPECIES[d][0], d) for d in ORDER]
    out += [en_card("Pikachu ex", 25), en_card("Ash’s Pikachu", 25)]
    out += [en_card("Nidoran Female", 29), en_card("Nidoran Female", 29)]
    return out


def jp_card(code, number, name, dex=None, card_type=POKEMON, jp_id=None):
    return {
        "name": name,
        "set_name": code,
        "number": number,
        "card_type": card_type,
        "pokedex_number": dex,
        "jp_id": jp_id,
    }


def jp_set(code, total=6):
    """Six Pokemon cards 001-006 in ORDER, plus a supporter at 007."""
    cards = [jp_card(code, f"{i + 1:03d}", SPECIES[d][1], d) for i, d in enumerate(ORDER[:total])]
    cards.append(jp_card(code, "007", "ナンジャモ", None, TRAINER))
    return cards


def tcg_set(set_id, name, abbreviation, total=6):
    products = [
        {"productId": set_id * 100 + i, "name": SPECIES[d][0], "setId": set_id,
         "collectorNumber": f"{i + 1:03d}/007", "rarity": "Common"}
        for i, d in enumerate(ORDER[:total])
    ]
    products.append({"productId": set_id * 100 + 7, "name": "Professor's Research", "setId": set_id,
                     "collectorNumber": "007/007", "rarity": "Uncommon"})
    return {"setId": set_id, "name": name, "abbreviation": abbreviation}, products


def catalog_of(*pairs):
    return {"meta": {"slug": "pokemon-japan"}, "sets": [s for s, _ in pairs],
            "products": [p for _, ps in pairs for p in ps]}


def natives(catalog):
    return {(p["setId"], p["collectorNumber"], p["name"]): p.get("nameNative") for p in catalog["products"]}


@pytest.fixture
def tokens():
    return build_species_tokens(en_records())


def test_number_key():
    assert number_key("001/073") == "1"
    assert number_key("132/SV-P") == "132"
    assert number_key("010") == "10"
    assert number_key(" 7 ") == "7"
    assert number_key("") is None
    assert number_key(None) is None
    assert number_key("SV-P") is None
    assert number_key("LIG") is None
    assert number_key("DPBP#523") is None


def test_species_tokens_pick_the_shared_word(tokens):
    assert tokens[25] == "pikachu"
    assert tokens[29] == "nidoran"
    assert tokens[1] == "bulbasaur"


def test_species_tokens_ignore_non_pokemon_and_bad_keys():
    recs = [{"name": "Potion", "card_type": "Trainer", "card_key": "5|1"},
            {"name": "Odd", "card_type": POKEMON, "card_key": None},
            {"name": "Odd", "card_type": POKEMON, "card_key": "x|1"}]
    assert build_species_tokens(recs) == {}


def test_links_exact_abbreviation_in_trusted_set(tokens):
    index = build_card_index(jp_set("SV2a"))
    out, report = add_native_names(catalog_of(tcg_set(1, "SV2a: Pokemon Card 151", "SV2a")), index, tokens)
    got = natives(out)
    assert got[(1, "001/007", "Bulbasaur")] == SPECIES[1][1]
    assert got[(1, "004/007", "Pikachu")] == SPECIES[25][1]
    assert got[(1, "007/007", "Professor's Research")] == "ナンジャモ"
    assert report.rows_total == 7 and report.rows_named == 7
    (s,) = report.sets
    assert (s.code, s.route, s.compared, s.agreed, s.trusted) == ("SV2a", "abbreviation", 6, 6, True)


def test_input_catalog_is_not_mutated(tokens):
    cat = catalog_of(tcg_set(1, "SV2a: X", "SV2a"))
    before = json.dumps(cat, sort_keys=True)
    add_native_names(cat, build_card_index(jp_set("SV2a")), tokens)
    assert json.dumps(cat, sort_keys=True) == before


def test_output_shape_is_unchanged_except_name_native(tokens):
    cat = catalog_of(tcg_set(1, "SV2a: X", "SV2a"))
    out, _ = add_native_names(cat, build_card_index(jp_set("SV2a")), tokens)
    assert out["meta"] == cat["meta"] and out["sets"] == cat["sets"]
    for before, after in zip(cat["products"], out["products"]):
        extra = {k: v for k, v in after.items() if k not in before}
        assert set(extra) <= {"nameNative"}
        assert {k: after[k] for k in before} == before


def test_case_insensitive_abbreviation(tokens):
    index = build_card_index(jp_set("SVHK"))
    out, report = add_native_names(catalog_of(tcg_set(1, "Stellar", "svHK")), index, tokens)
    assert natives(out)[(1, "001/007", "Bulbasaur")] == SPECIES[1][1]
    assert report.sets[0].route == "abbreviation-case"


def test_name_prefix_when_abbreviation_missing(tokens):
    index = build_card_index(jp_set("SVM"))
    out, report = add_native_names(catalog_of(tcg_set(1, "SVM: Generations Start Decks", "")), index, tokens)
    assert natives(out)[(1, "002/007", "Charmander")] == SPECIES[4][1]
    assert report.sets[0].route == "name-prefix"


def test_content_search_finds_the_one_trusted_code(tokens):
    index = build_card_index(jp_set("BW1-Bb") + jp_set("ZZZ", total=0))
    out, report = add_native_names(catalog_of(tcg_set(1, "BW1: Black Collection", "BW1")), index, tokens)
    assert natives(out)[(1, "003/007", "Squirtle")] == SPECIES[7][1]
    assert (report.sets[0].code, report.sets[0].route) == ("BW1-Bb", "content")


def test_content_search_with_two_trusted_codes_links_nothing(tokens):
    index = build_card_index(jp_set("HSZt") + jp_set("HSZp"))
    out, report = add_native_names(catalog_of(tcg_set(1, "HSZ: National Beginning Set", "HSZ")), index, tokens)
    assert all(v is None for v in natives(out).values())
    assert report.sets[0].code is None and report.sets[0].route == "ambiguous"


def test_wrong_abbreviation_falls_through_to_content_search(tokens):
    # TCGplayer says SVAW but the cards are PTCG's SVAL; PTCG's SVAW is a different deck.
    wrong = [jp_card("SVAW", f"{i + 1:03d}", SPECIES[d][1], d) for i, d in enumerate(reversed(ORDER))]
    index = build_card_index(wrong + jp_set("SVAL"))
    out, report = add_native_names(catalog_of(tcg_set(1, "SV: ex Starter Set", "svAW")), index, tokens)
    assert natives(out)[(1, "001/007", "Bulbasaur")] == SPECIES[1][1]
    assert (report.sets[0].code, report.sets[0].route) == ("SVAL", "content")


def test_trusted_code_covering_more_rows_beats_a_small_trusted_abbreviation(tokens):
    # PTCG's "XY6" holds a few of the cards at the same numbers; "XY6-B" is the full set.
    cards, (s, products) = big_set("XY6-B", 1)
    cards += [c for c in big_set("XY6", 1, rows=6)[0]]
    s["abbreviation"], s["name"] = "XY6", "XY6: Emerald Break"
    out, report = add_native_names(catalog_of((s, products)), build_card_index(cards), tokens)
    assert (report.sets[0].code, report.sets[0].route, report.sets[0].agreed) == ("XY6-B", "content", 20)
    assert report.rows_named == 20


def test_tie_between_named_and_other_code_keeps_the_named_code(tokens):
    index = build_card_index(jp_set("SV2a") + jp_set("SV2aX"))
    out, report = add_native_names(catalog_of(tcg_set(1, "SV2a: X", "SV2a")), index, tokens)
    assert (report.sets[0].code, report.sets[0].route) == ("SV2a", "abbreviation")


def test_untrusted_set_gets_no_names(tokens):
    # 5 of 6 agree (83%), under 95%: nothing is linked, not even the agreeing rows.
    cards = jp_set("S5R")
    cards[0] = jp_card("S5R", "001", SPECIES[143][1], 143)
    out, report = add_native_names(catalog_of(tcg_set(1, "S5R: X", "S5R")), build_card_index(cards), tokens)
    assert all(v is None for v in natives(out).values())
    assert report.rows_named == 0
    assert report.sets[0].trusted is False


def test_fewer_than_five_comparable_rows_is_not_trusted(tokens):
    index = build_card_index(jp_set("SP1", total=4))
    out, report = add_native_names(catalog_of(tcg_set(1, "sp1: Box", "SP1", total=4)), index, tokens)
    assert all(v is None for v in natives(out).values())
    assert report.sets[0].compared == 4 and report.sets[0].trusted is False


def test_exactly_95_percent_is_trusted(tokens):
    # 20 Pokemon rows, the first one names another species: 19/20 = 95%.
    order = ORDER * 4
    cards, products = [], []
    for i in range(20):
        d = order[i]
        cards.append(jp_card("BIG", str(i + 1), SPECIES[d][1], d))
        eng = SPECIES[d][0] if i else "Snorlax" if d != 143 else "Eevee"
        products.append({"name": eng, "setId": 1, "collectorNumber": f"{i + 1:03d}/020"})
    cat = {"meta": {}, "sets": [{"setId": 1, "name": "BIG: X", "abbreviation": "BIG"}], "products": products}
    out, report = add_native_names(cat, build_card_index(cards), tokens)
    assert (report.sets[0].compared, report.sets[0].agreed, report.sets[0].trusted) == (20, 19, True)
    named = [p.get("nameNative") for p in out["products"]]
    assert named[0] is None  # the one mismatching Pokemon row is still refused
    assert all(n for n in named[1:])


def test_variants_sharing_a_number_get_the_same_name(tokens):
    s, products = tcg_set(1, "SV2a: X", "SV2a")
    products.append({"name": "Pikachu (Master Ball Pattern)", "setId": 1, "collectorNumber": "004/007"})
    products.append({"name": "Pikachu - 004/007 (Poke Ball Pattern)", "setId": 1, "collectorNumber": "004/007"})
    out, _ = add_native_names(catalog_of((s, products)), build_card_index(jp_set("SV2a")), tokens)
    pika = [p["nameNative"] for p in out["products"] if p["collectorNumber"] == "004/007"]
    assert pika == [SPECIES[25][1]] * 3


def big_set(code, set_id, rows=20):
    order = ORDER * 4
    cards = [jp_card(code, str(i + 1), SPECIES[order[i]][1], order[i]) for i in range(rows)]
    products = [{"name": SPECIES[order[i]][0], "setId": set_id, "collectorNumber": f"{i + 1:03d}/{rows:03d}"}
                for i in range(rows)]
    return cards, ({"setId": set_id, "name": f"{code}: Big", "abbreviation": code}, products)


def test_pokemon_row_with_other_species_is_refused_in_trusted_set(tokens):
    cards, (s, products) = big_set("SV11W", 1)
    products.append({"name": "Herdier (Master Ball Pattern)", "setId": 1, "collectorNumber": "005/020"})
    out, report = add_native_names(catalog_of((s, products)), build_card_index(cards), tokens)
    assert (report.sets[0].compared, report.sets[0].agreed, report.sets[0].trusted) == (20, 19, True)
    got = natives(out)
    assert got[(1, "005/020", "Eevee")] == SPECIES[133][1]
    assert got[(1, "005/020", "Herdier (Master Ball Pattern)")] is None


def test_non_pokemon_card_on_a_species_named_row_is_refused(tokens):
    s, products = tcg_set(1, "SV2a: X", "SV2a")
    products.append({"name": "Taipei's Pikachu - 007/SV-P", "setId": 1, "collectorNumber": "007/007"})
    products.append({"name": "Snorlax Doll", "setId": 1, "collectorNumber": "007/007"})
    out, _ = add_native_names(catalog_of((s, products)), build_card_index(jp_set("SV2a")), tokens)
    got = natives(out)
    assert got[(1, "007/007", "Taipei's Pikachu - 007/SV-P")] is None
    assert got[(1, "007/007", "Snorlax Doll")] == "ナンジャモ"


def test_two_different_ptcg_names_at_one_number_are_skipped(tokens):
    cards = jp_set("SV2a") + [jp_card("SV2a", "7", "ボタン", None, TRAINER)]
    out, _ = add_native_names(catalog_of(tcg_set(1, "SV2a: X", "SV2a")), build_card_index(cards), tokens)
    assert natives(out)[(1, "007/007", "Professor's Research")] is None
    assert natives(out)[(1, "001/007", "Bulbasaur")] == SPECIES[1][1]


def test_duplicate_ptcg_cards_with_the_same_name_are_fine(tokens):
    cards = jp_set("SV2a") + [jp_card("SV2a", "1", SPECIES[1][1], 1)]
    out, _ = add_native_names(catalog_of(tcg_set(1, "SV2a: X", "SV2a")), build_card_index(cards), tokens)
    assert natives(out)[(1, "001/007", "Bulbasaur")] == SPECIES[1][1]


def test_names_are_nfc_and_stripped_and_empty_names_ignored(tokens):
    decomposed = unicodedata.normalize("NFD", "フシギダネ")  # ダ splits in NFD
    assert decomposed != SPECIES[1][1]
    cards = jp_set("SV2a")
    cards[0] = jp_card("SV2a", "001", "  " + decomposed + " ", 1)
    cards[-1] = jp_card("SV2a", "007", "   ", None, TRAINER)
    out, _ = add_native_names(catalog_of(tcg_set(1, "SV2a: X", "SV2a")), build_card_index(cards), tokens)
    got = natives(out)
    assert got[(1, "001/007", "Bulbasaur")] == SPECIES[1][1]
    assert got[(1, "007/007", "Professor's Research")] is None


def test_non_numeric_numbers_and_missing_codes_are_ignored(tokens):
    cards = jp_set("SV2a") + [jp_card("SV2a", "LIG", "基本雷エネルギー"),
                              jp_card(None, "001", "X"), jp_card("", "002", "Y")]
    s, products = tcg_set(1, "SV2a: X", "SV2a")
    products.append({"name": "Lightning Energy", "setId": 1, "collectorNumber": ""})
    out, report = add_native_names(catalog_of((s, products)), build_card_index(cards), tokens)
    assert natives(out)[(1, "", "Lightning Energy")] is None
    assert report.rows_named == 7


def test_existing_name_native_is_kept(tokens):
    s, products = tcg_set(1, "SV2a: X", "SV2a")
    products[0]["nameNative"] = "keep me"
    out, _ = add_native_names(catalog_of((s, products)), build_card_index(jp_set("SV2a")), tokens)
    assert natives(out)[(1, "001/007", "Bulbasaur")] == "keep me"


def test_two_tcgplayer_sets_may_share_one_ptcg_code(tokens):
    a = tcg_set(1, "SV2a: X", "SV2a")
    b = tcg_set(2, "SV2a: X (Master Ball)", "SV2a")
    out, report = add_native_names(catalog_of(a, b), build_card_index(jp_set("SV2a")), tokens)
    assert report.rows_named == 14


def test_load_ptcg_folder_reads_nested_json_and_skips_bad_files(tmp_path):
    (tmp_path / "SV2a").mkdir()
    (tmp_path / "SV2a" / "1.json").write_text(json.dumps(jp_card("SV2a", "001", "A")), encoding="utf-8")
    (tmp_path / "SV2a" / "2.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "SV2a" / "3.json").write_text("[1, 2]", encoding="utf-8")
    (tmp_path / "SV2a" / "notes.txt").write_text("x", encoding="utf-8")
    recs, skipped = load_ptcg_folder(tmp_path)
    assert [r["name"] for r in recs] == ["A"] and skipped == 2


SMALL = ["--min-jp-cards", "1", "--min-en-cards", "1", "--min-species", "1"]


def write_ptcg(root, jp_records, en):
    for lang, recs in (("jp", jp_records), ("en", en)):
        folder = root / f"data_{lang}" / "x"
        folder.mkdir(parents=True)
        for i, rec in enumerate(recs):
            (folder / f"{i}.json").write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")


def write_catalog(path, catalog):
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(catalog, fh)


def read_catalog(path):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def test_main_writes_catalog_and_report(tmp_path, capsys):
    write_ptcg(tmp_path / "ptcg", jp_set("SV2a"), en_records())
    src = tmp_path / "pokemon-japan.json.gz"
    write_catalog(src, catalog_of(tcg_set(1, "SV2a: X", "SV2a")))
    out = tmp_path / "out" / "pokemon-japan.json.gz"
    report = tmp_path / "report.json"
    code = main(["--catalog", str(src), "--ptcg-root", str(tmp_path / "ptcg"), "--output", str(out),
                 "--report", str(report), "--min-share", "0.5", *SMALL])
    assert code == 0
    assert natives(read_catalog(out))[(1, "001/007", "Bulbasaur")] == SPECIES[1][1]
    rep = json.loads(report.read_text(encoding="utf-8"))
    assert rep["rowsTotal"] == 7 and rep["rowsNamed"] == 7
    assert rep["sets"][0]["code"] == "SV2a"
    assert "7/7" in capsys.readouterr().out


def test_main_overwrites_input_when_no_output(tmp_path):
    write_ptcg(tmp_path / "ptcg", jp_set("SV2a"), en_records())
    src = tmp_path / "pokemon-japan.json.gz"
    write_catalog(src, catalog_of(tcg_set(1, "SV2a: X", "SV2a")))
    assert main(["--catalog", str(src), "--ptcg-root", str(tmp_path / "ptcg"), *SMALL]) == 0
    assert natives(read_catalog(src))[(1, "001/007", "Bulbasaur")] == SPECIES[1][1]


def test_main_fails_below_min_share_and_leaves_file_alone(tmp_path):
    other = [jp_card("OTHER", f"{i + 1:03d}", SPECIES[d][1], d) for i, d in enumerate(reversed(ORDER))]
    write_ptcg(tmp_path / "ptcg", other, en_records())
    src = tmp_path / "pokemon-japan.json.gz"
    write_catalog(src, catalog_of(tcg_set(1, "Unrelated", "")))
    before = src.read_bytes()
    assert main(["--catalog", str(src), "--ptcg-root", str(tmp_path / "ptcg"), *SMALL]) == 1
    assert src.read_bytes() == before


def test_main_fails_when_ptcg_folder_is_too_small(tmp_path):
    write_ptcg(tmp_path / "ptcg", jp_set("SV2a"), en_records())
    src = tmp_path / "pokemon-japan.json.gz"
    write_catalog(src, catalog_of(tcg_set(1, "SV2a: X", "SV2a")))
    assert main(["--catalog", str(src), "--ptcg-root", str(tmp_path / "ptcg")]) == 1


def test_main_fails_when_data_en_is_too_small(tmp_path):
    write_ptcg(tmp_path / "ptcg", jp_set("SV2a"), en_records())
    src = tmp_path / "pokemon-japan.json.gz"
    write_catalog(src, catalog_of(tcg_set(1, "SV2a: X", "SV2a")))
    base = ["--catalog", str(src), "--ptcg-root", str(tmp_path / "ptcg"), "--min-jp-cards", "1"]
    assert main(base + ["--min-en-cards", "100", "--min-species", "1"]) == 1
    assert main(base + ["--min-en-cards", "1", "--min-species", "100"]) == 1


ENERGY = "基本エネルギー"  # "Basic Energy"
JP_LIGHTNING = "基本雷エネルギー"


def test_energy_rows_and_energy_cards_only_link_to_each_other(tokens):
    cards = jp_set("SV2a") + [jp_card("SV2a", "008", JP_LIGHTNING, None, ENERGY)]
    s, products = tcg_set(1, "SV2a: X", "SV2a")
    products += [
        {"name": "Basic Darkness Energy", "setId": 1, "collectorNumber": "007/007"},  # trainer slot
        {"name": "Lightning Energy", "setId": 1, "collectorNumber": "008/007"},
        {"name": "Switch", "setId": 1, "collectorNumber": "008/007"},  # energy slot
    ]
    out, _ = add_native_names(catalog_of((s, products)), build_card_index(cards), tokens)
    got = natives(out)
    assert got[(1, "007/007", "Basic Darkness Energy")] is None
    assert got[(1, "008/007", "Lightning Energy")] == JP_LIGHTNING
    assert got[(1, "008/007", "Switch")] is None


def test_row_numbered_in_another_set_is_refused(tokens):
    s, products = tcg_set(1, "SV2a: X", "SV2a")
    products.append({"name": "Professor's Research", "setId": 1, "collectorNumber": "007/DPt-P"})
    out, _ = add_native_names(catalog_of((s, products)), build_card_index(jp_set("SV2a")), tokens)
    got = natives(out)
    assert got[(1, "007/DPt-P", "Professor's Research")] is None
    assert got[(1, "007/007", "Professor's Research")] is not None


def test_promo_suffix_matching_the_code_is_accepted(tokens):
    cards = [dict(c, set_name="XYP") for c in jp_set("XYP")]
    s, products = tcg_set(1, "XY-P: XY Promos", "XY-P")
    for p in products:
        p["collectorNumber"] = p["collectorNumber"].split("/")[0] + "/XY-P"
    out, report = add_native_names(catalog_of((s, products)), build_card_index(cards), tokens)
    assert report.sets[0].code == "XYP" and report.rows_named == 7


def test_species_word_must_be_a_whole_word_outside_parentheses(tokens):
    cards, (s, products) = big_set("SV2a", 1)
    products += [
        {"name": "Pikachuish", "setId": 1, "collectorNumber": "004/020"},
        {"name": "Potion (Pikachu Stamped)", "setId": 1, "collectorNumber": "004/020"},
        {"name": "Pikachu-GX", "setId": 1, "collectorNumber": "004/020"},
        {"name": "Pikachu (Master Ball Pattern)", "setId": 1, "collectorNumber": "004/020"},
    ]
    out, _ = add_native_names(catalog_of((s, products)), build_card_index(cards), tokens)
    got = natives(out)
    assert got[(1, "004/020", "Pikachuish")] is None
    assert got[(1, "004/020", "Potion (Pikachu Stamped)")] is None
    assert got[(1, "004/020", "Pikachu-GX")] == SPECIES[25][1]
    assert got[(1, "004/020", "Pikachu (Master Ball Pattern)")] == SPECIES[25][1]


def test_nidoran_gender_letter_is_accepted(tokens):
    cards = jp_set("SV2a") + [jp_card("SV2a", "008", "ニドラン♀", 29)]
    s, products = tcg_set(1, "SV2a: X", "SV2a")
    products += [{"name": "NidoranF", "setId": 1, "collectorNumber": "008/007"},
                 {"name": "Nidoran F", "setId": 1, "collectorNumber": "008/007"}]
    out, _ = add_native_names(catalog_of((s, products)), build_card_index(cards), tokens)
    got = natives(out)
    assert got[(1, "008/007", "NidoranF")] and got[(1, "008/007", "Nidoran F")]


def test_trust_counts_distinct_numbers_not_variant_rows(tokens):
    s, products = tcg_set(1, "SP1: Box", "SP1", total=3)
    products = [p for p in products if p["collectorNumber"] != "007/007"]
    products += [dict(p, name=p["name"] + " (Mirror Holofoil)") for p in products]
    out, report = add_native_names(catalog_of((s, products)), build_card_index(jp_set("SP1")), tokens)
    assert report.sets[0].compared == 3 and report.sets[0].trusted is False


def test_two_trusted_codes_without_a_clear_margin_link_nothing(tokens):
    small, _ = big_set("AAA", 1, rows=11)
    cards, (s, products) = big_set("BBB", 1, rows=20)
    s["name"], s["abbreviation"] = "Unnamed", ""
    out, report = add_native_names(catalog_of((s, products)), build_card_index(small + cards), tokens)
    assert report.sets[0].route == "ambiguous" and report.rows_named == 0


def test_named_code_not_best_and_no_margin_links_nothing(tokens):
    small, _ = big_set("XY6", 1, rows=11)
    cards, (s, products) = big_set("XY6-B", 1, rows=20)
    s["abbreviation"], s["name"] = "XY6", "XY6: Emerald Break"
    out, report = add_native_names(catalog_of((s, products)), build_card_index(small + cards), tokens)
    assert report.sets[0].route == "ambiguous" and report.rows_named == 0


def test_blank_input_name_native_is_dropped(tokens):
    s, products = tcg_set(1, "Unlinked", "")
    products[0]["nameNative"] = "  "
    out, _ = add_native_names(catalog_of((s, products)), build_card_index([]), tokens)
    assert "nameNative" not in out["products"][0]
