from __future__ import annotations

import gzip
import json

from ptcg_helpers import write_card, write_species

from tcgjson.chs_import import build_chs, fingerprint, load_chs


def _card(card_id, name, number, *, card_type="1", dex=None, hp=None, retreat=None, artist=None,
          rarity="C", yoren="P0025", code="CSV1C", image=None, rule=None):
    details = {"id": card_id, "cardName": name, "collectionNumber": number, "commodityCode": code,
               "rarityText": rarity, "yorenCode": yoren, "cardType": card_type}
    if dex is not None:
        details["pokedexCode"] = f"{dex:04d}"
    if hp is not None:
        details["hp"] = hp
    if retreat is not None:
        details["retreatCost"] = retreat
    if artist:
        details["illustratorName"] = [artist]
    if rule:
        details["ruleText"] = rule
    return {"id": card_id, "name": name, "yorenCode": yoren, "cardType": card_type,
            "commodityCode": code, "image": image or f"img/1/{card_id}.png", "details": details}


def _dataset(tmp_path, extra_cards=()):
    cards = [
        _card(1, "皮卡丘", "001/010", dex=25, hp=60, retreat=1, artist="A"),
        _card(2, "皮卡丘", "001/010", dex=25, hp=60, retreat=1, artist="A", rarity="C★"),
        _card(3, "酋雷姆", "002/010", dex=646, hp=180, retreat=3, artist="B"),
        _card(4, "宝可梦交替", "003/010", card_type="2", yoren="Y118", rule="交换。|物品卡。"),
        *extra_cards,
    ]
    data = {"dict": {}, "collections": [
        {"id": 1, "commodityCode": "CSV1C", "name": "朱&紫", "salesDate": "2024-01-01",
         "cards": cards},
        # a box that reprints card 1: never imported twice
        {"id": 2, "commodityCode": "BOX1", "name": "礼盒", "cards": [
            dict(cards[0], image="img/2/1.png")]},
    ]}
    path = tmp_path / "ptcg_chs_infos.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    root = tmp_path / "ptcg"
    write_species(root, {"pikachu": 25, "kyurem": 646})
    write_card(root, "en", "x/1", name="Pikachu", card_type="Pokémon", card_key="25|60|1|A|10|1")
    write_card(root, "en", "x/2", name="Black Kyurem EX", card_type="Pokémon",
               card_key="646|180|3|B|x|1")
    write_card(root, "en", "x/3", name="White Kyurem EX", card_type="Pokémon",
               card_key="646|180|3|b|y|1")
    return path, root


def _read(path):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def test_fingerprint():
    assert fingerprint(25, 60, 1, " A ") == "25|60|1|a"
    assert fingerprint(None, 60, 1, "A") is None
    assert fingerprint(25, None, 1, "A") is None


def test_load_chs_imports_each_card_once_from_its_own_collection(tmp_path):
    path, _root = _dataset(tmp_path)
    cards, collections_by_code = load_chs(path, "https://raw/x/sha/")
    assert [c.source_id for c in cards] == ["zh-cn:1", "zh-cn:2", "zh-cn:3", "zh-cn:4"]
    assert collections_by_code["BOX1"]["name"] == "礼盒"
    first = cards[0]
    assert first.set_code == "CSV1C" and first.collector_number == "001/010"
    assert first.img == "https://raw/x/sha/img/1/1.png"
    assert first.card_key == "25|60|1|a" and first.kind == "pokemon"
    assert cards[3].kind == "trainer" and cards[3].rules_text == "交换。 物品卡。"
    assert cards[3].raw["yorenCode"] == "Y118"


def test_build_chs_routes_and_shape(tmp_path):
    path, root = _dataset(tmp_path)
    tdir = tmp_path / "translations"
    tdir.mkdir()
    (tdir / "zh-cn.json").write_text(json.dumps({
        "trainer|宝可梦交替": {"en": "Switch", "source": "glossary"},
        "set|朱&紫": {"en": "Scarlet & Violet", "source": "machine"}}, ensure_ascii=False),
        encoding="utf-8")
    report = build_chs(path, root, tmp_path / "out", translations_dir=tdir,
                       source_commit="c" * 40)
    data = _read(tmp_path / "out" / "pokemon-chinese-simplified.json.gz")
    assert data["meta"]["slug"] == "pokemon-chinese-simplified"
    assert [s["name"] for s in data["sets"]] == ["CSV1C: Scarlet & Violet"]
    products = {p["sourceId"]: p for p in data["products"]}
    assert products["zh-cn:1"]["name"] == "Pikachu"
    assert products["zh-cn:1"]["nameSource"] == "linked_en"
    assert products["zh-cn:1"]["imageUrls"] == [
        f"https://raw.githubusercontent.com/KewaiiGamer/PTCG-CHS-Datasets/{'c' * 40}/img/1/1.png"]
    assert products["zh-cn:2"]["name"] == "Pikachu (C★)"  # same number, other rarity
    # Black/White Kyurem share the fingerprint: ambiguous, falls through
    assert products["zh-cn:3"]["nameSource"] == "native"
    assert products["zh-cn:3"]["nameAlias"] == "Kyurem"
    assert products["zh-cn:4"]["name"] == "Switch"
    assert products["zh-cn:4"]["yorenCode"] == "Y118"
    assert report["pokemonFingerprint"] == {"pokemon": 3, "linked": 2}
    assert data["meta"]["coverage"]["zh-cn"]["total"] == 4


def test_translate_chs_uses_glossary_then_translator_once_per_name(tmp_path):
    from tcgjson.chs_import import translate_chs

    # a reprint of the trainer in another set, and an unlinked Pikachu ex
    path, root = _dataset(tmp_path, extra_cards=[
        _card(5, "宝可梦交替", "004/010", card_type="2", yoren="Y118"),
        _card(6, "皮卡丘ex", "005/010", dex=25, hp=200, retreat=1, artist="Z"),
        _card(7, "皮卡丘", "006/010", dex=25, hp=70, retreat=1, artist="Q"),
    ])

    class Fake:
        model = "m"

        def __init__(self):
            self.sent = []

        def translate(self, label, items):
            self.sent += [i["name"] for i in items]
            assert label == "Chinese (Simplified)"
            return {i["id"]: {"皮卡丘ex": "Pikachu ex", "宝可梦交替": "Switch"}.get(i["name"])
                    for i in items}

    tdir = tmp_path / "translations"
    fake = Fake()
    result = translate_chs(path, root, tdir, translator=fake, today="2026-10-28")
    cache = json.loads((tdir / "zh-cn.json").read_text(encoding="utf-8"))
    # 皮卡丘 (unlinked HP 70 print) comes from the glossary of the linked 皮卡丘 cards
    assert cache["pokemon|皮卡丘"] == {"en": "Pikachu", "source": "glossary", "date": "2026-10-28"}
    assert cache["pokemon|皮卡丘ex"]["en"] == "Pikachu ex"
    assert cache["trainer|宝可梦交替"]["source"] == "machine"
    assert fake.sent.count("宝可梦交替") == 1  # one name for both reprints
    assert result["glossary"] == 1
    fake2 = Fake()
    translate_chs(path, root, tdir, translator=fake2)
    assert "宝可梦交替" not in fake2.sent and "皮卡丘ex" not in fake2.sent


def test_changed_yoren_code_or_card_id_stops_the_build(tmp_path):
    path, root = _dataset(tmp_path)
    out = tmp_path / "out"
    build_chs(path, root, out, translations_dir=None)
    # same id, new yorenCode
    data = json.loads(path.read_text(encoding="utf-8"))
    data["collections"][0]["cards"][3]["yorenCode"] = "Y999"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    report = build_chs(path, root, tmp_path / "out2", translations_dir=None, previous_dir=out)
    assert "yorenCode" in report["error"]
    # same card slot, new id
    data["collections"][0]["cards"][3]["yorenCode"] = "Y118"
    data["collections"][0]["cards"][3]["details"]["id"] = 44
    data["collections"][0]["cards"][3]["id"] = 44
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    report = build_chs(path, root, tmp_path / "out3", translations_dir=None, previous_dir=out)
    assert "details.id" in report["error"]
