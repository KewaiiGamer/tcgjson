#!/usr/bin/env bash
# Fetch ptcg_chs_infos.json (only that file, never the ~10 GB image repo) at the commit pinned in
# chs-dataset.lock and check its git blob id. When the source is unreachable or changed, fall
# back to the pinned copy kept in the previous release (spec: "the build survives the source
# disappearing").
#
# Usage: scripts/fetch_chs_dataset.sh <dest json> [previous copy .json.gz] [lock file]
set -euo pipefail

dest="${1:?usage: fetch_chs_dataset.sh <dest json> [previous copy .json.gz] [lock file]}"
previous="${2:-}"
lock="${3:-chs-dataset.lock}"
repo="$(sed -n 's/^repo=//p' "$lock")"
commit="$(sed -n 's/^commit=//p' "$lock")"
file="$(sed -n 's/^path=//p' "$lock")"
blob="$(sed -n 's/^blob=//p' "$lock")"

url="https://raw.githubusercontent.com/$repo/$commit/$file"
if curl -fsSL --retry 3 --retry-delay 10 -o "$dest" "$url" \
    && [ "$(git hash-object --no-filters "$dest")" = "$blob" ]; then
  echo "fetched $file at $commit"
  exit 0
fi
echo "::warning::could not fetch $url (or its content changed); using the previous pinned copy"
if [ -n "$previous" ] && [ -f "$previous" ]; then
  gunzip -c "$previous" > "$dest"
  if [ "$(git hash-object --no-filters "$dest")" = "$blob" ]; then
    echo "using the pinned copy from the previous release"
    exit 0
  fi
  echo "::error::the previous pinned copy does not match the lock's blob id"
fi
rm -f "$dest"
exit 1
