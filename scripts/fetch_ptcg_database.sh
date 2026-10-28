#!/usr/bin/env bash
# Sparse, shallow checkout of PTCG-database at the commit pinned in ptcg-database.lock.
# Only the data_* folders the converter reads and code/species_dex.json are checked out
# (never data_sc or data_pocket). Nothing from the repository is executed.
#
# Usage: scripts/fetch_ptcg_database.sh <dest> [lock file]
# PTCG_PATHS (space-separated) overrides the checked-out paths, for tests.
set -euo pipefail

dest="${1:?usage: fetch_ptcg_database.sh <dest> [lock file]}"
lock="${2:-ptcg-database.lock}"
repo="$(sed -n 's/^repo=//p' "$lock")"
commit="$(sed -n 's/^commit=//p' "$lock")"
if [ -z "$repo" ] || [ -z "$commit" ]; then
  echo "lock file $lock needs repo= and commit= lines" >&2
  exit 1
fi
paths="${PTCG_PATHS:-data_ko data_tc data_jp data_en data_de data_fr data_es data_it data_pt code/species_dex.json}"

mkdir -p "$dest"
git -C "$dest" init -q
git -C "$dest" remote add origin "$repo" 2>/dev/null || git -C "$dest" remote set-url origin "$repo"
git -C "$dest" config core.sparseCheckout true
git -C "$dest" sparse-checkout set --no-cone $paths
git -C "$dest" fetch -q --depth 1 --filter=blob:none origin "$commit"
git -C "$dest" checkout -q FETCH_HEAD
actual="$(git -C "$dest" rev-parse HEAD)"
if [ "$actual" != "$commit" ]; then
  echo "checked out $actual, expected $commit" >&2
  exit 1
fi
echo "PTCG-database $commit checked out in $dest"
