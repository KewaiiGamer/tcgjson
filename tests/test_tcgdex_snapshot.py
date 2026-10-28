from __future__ import annotations

import json

from ptcg_helpers import write_card

from tcgjson.tcgdex_snapshot import needed_set_ids, update_snapshot


def test_needed_set_ids_are_the_tcgdex_sets_of_the_european_languages(tmp_path):
    write_card(tmp_path, "de", "sv01/001", name="a", number="001", set_name="sv01",
               print_key="tcgdex:sv01-1")
    write_card(tmp_path, "fr", "swsh1/001", name="b", number="001", set_name="swsh1",
               print_key="tcgdex:swsh1-1")
    write_card(tmp_path, "fr", "A1/001", name="c", number="001", set_name="A1",
               print_key="pocket:A1-1")
    assert needed_set_ids(tmp_path, ["de", "fr"]) == ["sv01", "swsh1"]


def test_update_snapshot_fetches_only_missing_sets_and_keeps_failures_out(tmp_path):
    path = tmp_path / "tcgdex-en.json"
    path.write_text(json.dumps({"sets": {"sv01": {"name": "Old", "abbreviation": "SVI",
                                                  "cards": {"1": "Pineco"}}}}), encoding="utf-8")
    calls = []

    def fetch(set_id):
        calls.append(set_id)
        if set_id == "broken":
            raise OSError("down")
        return {"id": set_id, "name": "Sword & Shield", "abbreviation": {"official": "SSH"},
                "cards": [{"localId": "001", "name": "Celebi V"}, {"localId": "TG01", "name": "X"}]}

    result = update_snapshot(path, ["sv01", "swsh1", "broken"], fetch=fetch)
    assert calls == ["swsh1", "broken"]
    assert result == {"fetched": 1, "failed": ["broken"], "kept": 1}
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["sets"]["sv01"]["name"] == "Old"
    assert data["sets"]["swsh1"] == {"name": "Sword & Shield", "abbreviation": "SSH",
                                     "cards": {"1": "Celebi V", "TG01": "X"}}
    assert "broken" not in data["sets"]
