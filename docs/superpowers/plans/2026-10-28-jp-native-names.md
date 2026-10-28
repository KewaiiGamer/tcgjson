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

## Link rule

1. Candidate codes for a TCGplayer set, in order: abbreviation exact; abbreviation ignoring case; the
   set-name prefix before `:` (exact, then ignoring case). The first candidate whose set is trusted
   wins. If none is trusted, a content search over all PTCG codes; accept only if exactly one code is
   trusted.
2. Trusted: at least 5 rows whose PTCG card (unique name at that number) is a Pokemon with a known
   Pokedex number, and at least 95% of them have that species' English token in the TCGplayer name.
3. Inside a trusted set, a row gets the PTCG name only if: its collector number is numeric; all PTCG
   cards at that number share one NFC name; a Pokemon card's species token is in the row name; a
   non-Pokemon card's row name does not end in a species name.
4. Variants with the same number get the same name. Names are NFC, stripped, never empty. Rows never
   linked keep no `nameNative`. Nothing else in the file changes.

## Tasks

1. `src/tcgjson/jp_native.py` + `tests/test_jp_native.py` (TDD): loaders, number key, species tokens,
   index, trust, set resolution, row linking, report.
2. `python -m tcgjson.jp_native` entry point: read catalog + PTCG root, write the catalog, print and
   optionally write a JSON report, fail under a minimum native-name share or with an empty PTCG folder.
3. Lock file `locks/ptcg-database.commit` (pinned SHA) and a sparse-fetch script
   `scripts/fetch-ptcg-database.sh` (data_jp + data_en only).
4. Workflow step in `.github/workflows/weekly-extra.yml` (may be refused; then the YAML goes in the
   report).
5. README attribution line for PTCG-database (MIT).
6. Offline end-to-end: real release + scratch clone, coverage report, then load through the app's
   `replaceCatalogRows` on in-memory SQLite and search `name_native LIKE`.
7. Fresh-subagent review, fixes, push the feature branch.
