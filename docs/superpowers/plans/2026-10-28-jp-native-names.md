# Japanese native names (Phase 2, Japanese part) — plan

Goal: add the official Japanese card name (`nameNative`) to rows of `pokemon-japan.json.gz` by linking
each TCGplayer "Pokemon Japan" row to the PTCG-database (`type-null/PTCG-database`, MIT) `data_jp` card
at the same set code and collector number, only inside sets where the link is trusted.

Design spec: `docs/superpowers/specs/2026-10-22-multilingual-catalog-design.md` in the app repo
(KewaiiGamer/tcg-tracker, branch `feat/multilingual-catalog`), "Calls I made" #7.

## Facts measured before building (PTCG commit 18e0de2, release extra-20261023)

- TCGplayer rows carry no HP or Pokedex number, so the Asian trust test (Pokedex + HP) cannot be used
  as written. Replacement: compare the PTCG card's Pokedex number, turned into an English species
  token from `data_en`, with the TCGplayer English name.
- 166 TCGplayer abbreviations equal a PTCG code, 78 equal one ignoring case, 79 match none; 138 sets
  have no abbreviation. Several TCGplayer abbreviations point at the wrong PTCG set (for example
  TCGplayer `svAW`/`svAM` are PTCG `SVAL`/`SVAW`), which the trust test catches.
- A content search (the one PTCG code whose cards agree with the set) finds unique codes for 83 more
  sets (old BW/DP/Pt/L/XY sets that PTCG splits by suffix, promos, decks).

## Link rule (as shipped; the module docstring in `src/tcgjson/jp_native.py` is the reference)

1. Row words: the TCGplayer name without the " - 001/073" tail and without parentheses, folded to
   ASCII. A row agrees with a PTCG Pokemon card when one word is the species word of its Pokedex
   number (from `data_en`), optionally followed by a card suffix (`gx`, `ex`...) or `f`/`m`.
2. Rows whose collector number names another set (`028/DPt-P` outside DPt-P) are ignored.
3. Trusted: at least 5 distinct collector numbers on PTCG Pokemon cards with a species word, and at
   least 95% of them with every row agreeing.
4. Every PTCG code is scored. A trusted named candidate (abbreviation or set-name prefix, exact or
   ignoring case) with the best agreeing count wins; otherwise the best trusted code wins only with at
   least twice the agreeing numbers of the runner-up ("content"); anything else links nothing.
5. Inside a trusted set a row is refused when PTCG has two names at its number, a Pokemon card
   disagrees, a non-Pokemon card sits on a row ending in a species word, an Energy card sits on a row
   without the word "energy", or a row ending in "energy" sits on a non-Energy card.
6. Variants with the same number get the same name. Names are NFC, stripped, never empty. Rows never
   linked keep no `nameNative`. Nothing else in the file changes.

## CI behaviour

The weekly workflow fails closed: if the PTCG checkout looks incomplete (data_jp < 20,000 cards,
data_en < 18,000, < 1,000 species words) or fewer than 60% of rows get a name, the step fails and no
release is published that week, so the app keeps the previous release. The pin does not move by
itself, so the share drifts down slowly as TCGplayer adds Japanese sets that the pinned PTCG commit
lacks (75.9% on the first release); bump `ptcg-database.lock` deliberately when it does.
The per-set link report is published as the release asset `jp-native-report.json`.

## Tasks

1. `src/tcgjson/jp_native.py` + `tests/test_jp_native.py` (TDD): loaders, number key, species tokens,
   index, trust, set resolution, row linking, report.
2. `python -m tcgjson.jp_native` entry point: read catalog + PTCG root, write the catalog, print and
   optionally write a JSON report, fail under a minimum native-name share or with an empty PTCG folder.
3. Lock file `ptcg-database.lock` (pinned SHA) and a sparse-fetch script
   `scripts/fetch_ptcg_database.sh`.
4. Workflow step in `.github/workflows/weekly-extra.yml` (may be refused; then the YAML goes in the
   report).
5. README attribution line for PTCG-database (MIT).
6. Offline end-to-end: real release + scratch clone, coverage report, then load through the app's
   `replaceCatalogRows` on in-memory SQLite and search `name_native LIKE`.
7. Fresh-subagent review, fixes, push the feature branch.
