# Phase 2a: Korean, Traditional Chinese and European catalogs (plan)

Spec (binding): `docs/superpowers/specs/2026-10-22-multilingual-catalog-design.md` in the app repo
(KewaiiGamer/tcg-tracker, branch `feat/multilingual-catalog`), sections "Data sources",
"English-name pipeline", "Machine translation", "Output format (fork releases)", "Verification".

Branch: `feat/ptcg-languages` (fork KewaiiGamer/tcgjson, from `origin/main` 4ef97832b).
Shared files touched as little as possible: `cli.py` gets one import and two hook calls; nothing
else outside the new modules. Native Japanese names (`jp_native.py`) belong to another branch.

## Language codes

App registry codes are used everywhere in the fork (`--languages`, `translations/<code>.json`):

| Code | PTCG folder | Release asset |
| --- | --- | --- |
| ko | data_ko | pokemon-korean.json.gz |
| zh-tw | data_tc | pokemon-chinese-traditional.json.gz |
| de / fr / es / it / pt | data_de ... data_pt | pokemon-german / -french / -spanish / -italian / -portuguese |

## Tasks (TDD: a failing pytest on a small fixture tree first, then the code)

1. **Reader** `src/tcgjson/ptcg_import.py`: walk `data_<folder>`, skip `pocket:` print keys and
   unreadable files, give each card a stable `sourceId` (`<code>:<path without .json>`), its kind
   (pokemon / trainer / energy, from Pokedex number and the localized `card_type`), and its
   `collectorNumber` (`NNN/TTT`, total padded to the number's width, number alone when no total).
2. **Name routes** `src/tcgjson/english_names.py`:
   - route 1 `linked_en`: English PTCG cards with the same `print_key` or `card_key`; all names must
     agree after folding case and punctuation, else ambiguous. European languages then try TCGdex's
     English edition at the same set id and number (committed snapshot, see 4).
   - route 2 `linked_ja` (ko, zh-tw): PTCG Japanese card at the same `print_key`, English name from
     TCGplayer Japan (`pokemon-japan.json.gz`) at the same code and number (base name: number suffix
     removed, variant parentheses folded; disagreeing names are ambiguous). Only in trusted sets
     (>= 5 compared Pokemon cards, >= 95% agree on Pokedex number and HP); a Pokemon card that itself
     disagrees with the Japanese card is never linked (stricter than the spec, safety).
   - route 3 `translated`: `translations/overrides.json` (hand edited, wins) then
     `translations/<code>.json` (cache). The build never calls the translator.
   - route 4 `native`.
   - `nameAlias`: English species name for the Pokedex number (`code/species_dex.json`).
3. **Translate** `src/tcgjson/translate.py` + `tcgjson translate`: pending = distinct
   (kind, native name) with no route 1 to 3 result. First the glossary (unanimous exact
   native-to-English pairs from routes 1 and 2 of the same language, cached with source
   `glossary`), then the OpenAI-compatible endpoint (`TRANSLATE_API_BASE` or `API_BASE`, default
   `https://ai.kewaii.com/v1`; `API_KEY`; `MODEL`, default `qwen3.8`) with the species hint, the
   card kind, up to 10 glossary pairs closest to the native name as candidates (trainers and
   energy), rules text when the record has one, and "none of these" allowed. Cached as
   `{en, source: "machine", model, date}`. A cached key is never sent again. No key or endpoint
   error: stop cleanly, names stay pending (route 4). Set names are translated the same way
   (kind `set`).
4. **TCGdex English snapshot** `src/tcgjson/tcgdex_snapshot.py` + `tcgjson tcgdex-snapshot`:
   committed `data/tcgdex-en.json` (set id -> English name, official abbreviation, number -> card
   name), fetched only for missing sets so names stay stable between builds.
5. **Converter** `src/tcgjson/ptcg_build.py` + `tcgjson ptcg-build`: one gzip per language in the
   tcgjson shape (`meta`, `sets`, `products`), products with `name`, `nameNative`, `nameAlias`,
   `nameSource`, `sourceId`, `collectorNumber`, `rarity` (as written), `imageUrls`, `setId`.
   Sets: `abbreviation` = Asian set code / TCGdex official abbreviation (else the TCGdex id),
   `name` = `CODE: English name` (Japanese TCGplayer set of the same code, TCGdex English set
   name, else the translated or native set name). Collisions on (set, number, name) get a
   ` (<rarity>)` suffix, then ` (2)`, never dropped. `meta.coverage` per nameSource.
   Hard gates (exit non-zero): a `linked_ja` row outside a trusted set; a language with more than
   5% fewer rows than the previous release; a row translated in both builds whose name changed
   without an override.
6. **Measure** on the PTCG clone at 18e0de2 against the spec baselines (Korean ~71% + ~15%,
   Traditional Chinese ~55% + ~30%, European ~100%); 10-point pause rule per language.
7. **App**: `catalog-sources.ts` entries + seeder test (separate branch `feat/phase2-languages`).
8. **CI**: `ptcg-database.lock` (repo + pinned commit), `scripts/fetch_ptcg_database.sh` (sparse,
   shallow, only the needed `data_*` folders and `code/species_dex.json`), converter step and asset
   upload in the weekly extra workflow, previous release downloaded first for the gates.
