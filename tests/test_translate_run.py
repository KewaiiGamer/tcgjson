from __future__ import annotations

import json

from ptcg_helpers import write_card, write_species

from tcgjson.translate import Translations
from tcgjson.translate_run import (
    ChatTranslator,
    bigram_similarity,
    parse_reply,
    pending_items,
    translate_language,
)


def _tree(tmp_path):
    root = tmp_path / "ptcg"
    write_species(root, {"pikachu": 25})
    write_card(root, "en", "SV/SVI/1", name="Pikachu", number="1", card_type="Pokémon",
               print_key="en:SVI-1", card_key="25|60|1|A|10|1", pokedex_number=25)
    write_card(root, "en", "SV/SVI/2", name="Professor’s Research", number="2",
               card_type="Supporter", print_key="en:SVI-2", card_key="trainer-key")
    # Korean: one linked Pikachu, one linked trainer, an unlinked reprint of that trainer,
    # an unlinked Pikachu ex (dex hint) and an unlinked new trainer.
    write_card(root, "ko", "K1/001", name="피카츄", number="001", set_name="K1", set_total="005",
               set_full_name="한국 세트", card_type="Pokémon", card_key="25|60|1|A|10|1",
               pokedex_number=25, hp=60)
    write_card(root, "ko", "K1/002", name="박사의 연구", number="002", set_name="K1",
               set_total="005", card_type="서포트", card_key="trainer-key")
    write_card(root, "ko", "K2/001", name="박사의 연구", number="001", set_name="K2",
               set_total="005", set_full_name="두번째", card_type="서포트")
    write_card(root, "ko", "K2/002", name="피카츄 ex", number="002", set_name="K2",
               set_total="005", card_type="Pokémon", pokedex_number=25, hp=200)
    write_card(root, "ko", "K2/003", name="박사의 새 연구", number="003", set_name="K2",
               set_total="005", card_type="서포트", effect="카드를 7장 뽑는다.")
    return root


class FakeTranslator:
    model = "fake-model"

    def __init__(self, answers):
        self.answers = answers
        self.batches = []

    def translate(self, language_label, items):
        self.batches.append(items)
        return {item["id"]: self.answers.get(item["name"]) for item in items}


def test_bigram_similarity():
    assert bigram_similarity("박사의 연구", "박사의 연구") == 1.0
    assert bigram_similarity("박사의 새 연구", "박사의 연구") > bigram_similarity("피카츄", "박사의 연구")


def test_parse_reply_tolerates_think_blocks_and_fences():
    text = '<think>hmm {"x":1}</think>\n```json\n{"items": [{"id": 1, "en": "Pikachu ex"}]}\n```'
    assert parse_reply(text) == {1: "Pikachu ex"}
    assert parse_reply("nothing here") == {}


def test_pending_items_lists_distinct_unlinked_names_with_hints(tmp_path):
    root = _tree(tmp_path)
    items, glossary = pending_items(root, "ko", Translations("ko", {}, {}), japan_catalog=None,
                                    tcgdex_snapshot=None)
    keys = sorted(item["key"] for item in items)
    assert keys == ["pokemon|피카츄 ex", "set|두번째", "set|한국 세트", "trainer|박사의 새 연구",
                    "trainer|박사의 연구"]
    by_key = {item["key"]: item for item in items}
    assert by_key["pokemon|피카츄 ex"]["species_hint"] == "Pikachu"
    assert by_key["trainer|박사의 새 연구"]["rules_text"] == "카드를 7장 뽑는다."
    assert glossary == {"pokemon|피카츄": "Pikachu", "trainer|박사의 연구": "Professor's Research"}


def test_translate_language_uses_glossary_then_translator_and_never_repeats(tmp_path):
    root = _tree(tmp_path)
    tdir = tmp_path / "translations"
    fake = FakeTranslator({"피카츄 ex": "Pikachu ex", "박사의 새 연구": "Professor's New Research",
                           "한국 세트": "Korean Set"})
    result = translate_language(root, "ko", tdir, translator=fake, japan_catalog=None,
                                tcgdex_snapshot=None, today="2026-10-28")
    cache = json.loads((tdir / "ko.json").read_text(encoding="utf-8"))
    assert cache["trainer|박사의 연구"] == {"en": "Professor's Research", "source": "glossary",
                                         "date": "2026-10-28"}
    assert cache["pokemon|피카츄 ex"] == {"en": "Pikachu ex", "source": "machine",
                                       "model": "fake-model", "date": "2026-10-28"}
    assert "set|두번째" not in cache  # translator had no answer: stays pending
    assert result == {"glossary": 1, "machine": 3, "pending": 1, "error": None}
    trainer_item = next(i for b in fake.batches for i in b if i["name"] == "박사의 새 연구")
    assert trainer_item["candidates"][0] == "Professor's Research"
    # second run: nothing cached is sent again
    fake2 = FakeTranslator({})
    translate_language(root, "ko", tdir, translator=fake2, japan_catalog=None,
                       tcgdex_snapshot=None, today="2026-10-29")
    sent = [i["name"] for b in fake2.batches for i in b]
    assert sent == ["두번째"]


def test_translate_language_without_translator_only_fills_the_glossary(tmp_path):
    root = _tree(tmp_path)
    tdir = tmp_path / "translations"
    result = translate_language(root, "ko", tdir, translator=None, japan_catalog=None,
                                tcgdex_snapshot=None, today="2026-10-28")
    assert result == {"glossary": 1, "machine": 0, "pending": 4, "error": None}


def test_translator_error_stops_cleanly_and_keeps_earlier_batches(tmp_path):
    root = _tree(tmp_path)
    tdir = tmp_path / "translations"

    class Broken(FakeTranslator):
        def translate(self, language_label, items):
            raise OSError("endpoint down")

    result = translate_language(root, "ko", tdir, translator=Broken({}), japan_catalog=None,
                                tcgdex_snapshot=None, today="2026-10-28")
    assert result["error"] == "endpoint down"
    assert result["glossary"] == 1


def test_chat_translator_posts_openai_shape():
    calls = []

    class Response:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": '{"items":[{"id":0,"en":"Pikachu ex"}]}'}}]}

    def post(url, headers, json, timeout):
        calls.append((url, headers, json))
        return Response()

    translator = ChatTranslator("https://ai.example/v1/", "secret", "qwen", post=post)
    out = translator.translate("Korean", [{"id": 0, "name": "피카츄 ex", "kind": "pokemon"}])
    assert out == {0: "Pikachu ex"}
    url, headers, body = calls[0]
    assert url == "https://ai.example/v1/chat/completions"
    assert headers["Authorization"] == "Bearer secret"
    assert body["model"] == "qwen" and body["temperature"] == 0
