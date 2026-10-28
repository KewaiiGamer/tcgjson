"""CLI commands for the PTCG-database languages: ptcg-build, tcgdex-snapshot, translate."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .ptcg_import import LANGUAGES

PTCG_COMMANDS = ("ptcg-build", "tcgdex-snapshot", "translate", "import-chs")
ALL_CODES = [lang.code for lang in LANGUAGES]


def _codes(value: str | None) -> list[str]:
    if not value:
        return list(ALL_CODES)
    codes = [code.strip() for code in value.split(",") if code.strip()]
    unknown = [code for code in codes if code not in ALL_CODES]
    return _check(codes, unknown)


def _translate_codes(value: str | None) -> list[str]:
    """Like _codes, plus zh-cn (Simplified Chinese, from PTCG-CHS-Datasets)."""
    if not value:
        return list(ALL_CODES)
    codes = [code.strip() for code in value.split(",") if code.strip()]
    return _check(codes, [code for code in codes if code not in ALL_CODES and code != "zh-cn"])


def _check(codes: list[str], unknown: list[str]) -> list[str]:
    if unknown:
        raise argparse.ArgumentTypeError(f"unknown language(s): {', '.join(unknown)}")
    return codes


def register_ptcg_commands(subparsers) -> None:
    build = subparsers.add_parser("ptcg-build", help="Convert PTCG-database languages to tcgjson")
    build.add_argument("--ptcg-root", type=Path, required=True)
    build.add_argument("--output", type=Path, default=Path("release"))
    build.add_argument("--languages", type=_codes, default=None,
                       help=f"Comma-separated codes (default: {','.join(ALL_CODES)})")
    build.add_argument("--japan-catalog", type=Path, default=None,
                       help="pokemon-japan.json.gz, for linked_ja names and set names")
    build.add_argument("--translations", type=Path, default=Path("translations"))
    build.add_argument("--tcgdex-snapshot", type=Path, default=Path("data/tcgdex-en.json"))
    build.add_argument("--previous-dir", type=Path, default=None,
                       help="Directory holding the previous release's language files (gates)")
    build.add_argument("--source-commit", default="")
    build.add_argument("--carry-forward", action="store_true",
                       help="Copy the previous release's file for a language that fails a gate")
    build.add_argument("--report", type=Path, default=None, help="Write the coverage report JSON")

    snap = subparsers.add_parser("tcgdex-snapshot", help="Fetch missing TCGdex English sets")
    snap.add_argument("--ptcg-root", type=Path, required=True)
    snap.add_argument("--snapshot", type=Path, default=Path("data/tcgdex-en.json"))
    snap.add_argument("--refresh", action="store_true")

    tr = subparsers.add_parser("translate", help="Fill the translation cache for pending names")
    tr.add_argument("--ptcg-root", type=Path, required=True)
    tr.add_argument("--languages", type=_translate_codes, default=None,
                    help="Comma-separated codes; zh-cn needs --chs-json")
    tr.add_argument("--chs-json", type=Path, default=None, help="ptcg_chs_infos.json (zh-cn)")
    tr.add_argument("--japan-catalog", type=Path, default=None)
    tr.add_argument("--translations", type=Path, default=Path("translations"))
    tr.add_argument("--tcgdex-snapshot", type=Path, default=Path("data/tcgdex-en.json"))
    tr.add_argument("--limit", type=int, default=None, help="Translate at most N names per language")
    tr.add_argument("--batch-size", type=int, default=20)
    tr.add_argument("--glossary-only", action="store_true", help="Do not call the translator")

    chs = subparsers.add_parser("import-chs", help="Convert PTCG-CHS-Datasets to tcgjson (zh-cn)")
    chs.add_argument("--chs-json", type=Path, required=True, help="ptcg_chs_infos.json")
    chs.add_argument("--ptcg-root", type=Path, required=True,
                     help="PTCG-database checkout (English cards and species table)")
    chs.add_argument("--output", type=Path, default=Path("release"))
    chs.add_argument("--translations", type=Path, default=Path("translations"))
    chs.add_argument("--previous-dir", type=Path, default=None)
    chs.add_argument("--source-commit", default="",
                     help="Dataset commit (pins the image URLs)")
    chs.add_argument("--image-repo", default="KewaiiGamer/PTCG-CHS-Datasets")
    chs.add_argument("--carry-forward", action="store_true")


def run_ptcg_command(args) -> int:
    if args.command == "ptcg-build":
        from .ptcg_build import build_languages, coverage_line

        report = build_languages(
            args.ptcg_root, args.output, args.languages or list(ALL_CODES),
            japan_catalog=args.japan_catalog, translations_dir=args.translations,
            tcgdex_snapshot=args.tcgdex_snapshot, previous_dir=args.previous_dir,
            source_commit=args.source_commit, carry_forward=args.carry_forward,
        )
        failed = False
        for code, entry in report.items():
            print(coverage_line(code, entry["coverage"]))
            if entry.get("renamed"):
                print(f"{code}: renamed since the previous release: {entry['renamed']}")
            if "error" in entry:
                failed = True
                carried = " (previous file carried forward)" if entry.get("carriedForward") else ""
                print(f"GATE FAILED {entry['error']}{carried}")
        if args.report:
            args.report.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
        return 1 if failed else 0
    if args.command == "tcgdex-snapshot":
        from .tcgdex_snapshot import needed_set_ids, update_snapshot

        result = update_snapshot(args.snapshot, needed_set_ids(args.ptcg_root), refresh=args.refresh)
        print(f"TCGdex English snapshot: {result['fetched']} fetched, {result['kept']} kept, "
              f"{len(result['failed'])} failed {result['failed'][:20]}")
        return 0
    if args.command == "import-chs":
        from .chs_import import build_chs
        from .ptcg_build import coverage_line

        entry = build_chs(args.chs_json, args.ptcg_root, args.output,
                          translations_dir=args.translations, previous_dir=args.previous_dir,
                          source_commit=args.source_commit, image_repo=args.image_repo,
                          carry_forward=args.carry_forward)
        print(coverage_line("zh-cn", entry["coverage"]))
        if entry.get("pokemonFingerprint"):
            fp = entry["pokemonFingerprint"]
            share = 100.0 * fp["linked"] / fp["pokemon"] if fp["pokemon"] else 0.0
            print(f"zh-cn: Pokemon linked by fingerprint {fp['linked']}/{fp['pokemon']} "
                  f"({share:.1f}%)")
        if entry.get("renamed"):
            print(f"zh-cn: renamed since the previous release: {entry['renamed']}")
        if "error" in entry:
            print(f"GATE FAILED {entry['error']}")
            return 1
        return 0
    if args.command == "translate":
        from .translate_run import run_translate

        return run_translate(args)
    raise ValueError(f"not a PTCG command: {args.command}")
