"""Command-line adapter for the cosmetic-disabler application services."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

from .application import build_cosmetics, clean_cosmetics, get_status, parse_schemas
from .constants import DEFAULT_CATEGORIES, DEFAULT_LANGUAGE, HISTORY_FILENAME, SUPPORTED_CATEGORIES
from .domain import BuildOptions
from .model_patcher import find_model_patcher, validate_model_patcher
from .paths import application_root
from .reporting import write_plan
from .version import VERSION
from .versioning import dota_version_label, read_version_history
from .vpk import find_vpk_extractor, validate_vpk_extractor


def do_analyze(args: argparse.Namespace) -> int:
    units_path = getattr(args, "npc_units", None)
    plan = parse_schemas(
        Path(args.items_game).expanduser().resolve(),
        Path(args.npc_heroes).expanduser().resolve(),
        Path(units_path).expanduser().resolve() if units_path else None,
    )
    report = Path(args.report).expanduser().resolve()
    write_plan(plan, report)
    print(json.dumps(plan.stats, indent=2))
    print(f"Wrote: {report}")
    return 0


def do_build(args: argparse.Namespace) -> int:
    selected_categories = getattr(args, "category", None)
    options = BuildOptions(
        dota=args.dota,
        extractor=args.extractor,
        language=args.language,
        work=args.work,
        clean_first=args.clean_first,
        allow_missing=args.allow_missing,
        enabled_categories=frozenset(selected_categories or DEFAULT_CATEGORIES),
    )
    build_cosmetics(
        options,
        warning=lambda message: print(message, file=sys.stderr),
    )
    return 0


def do_clean(args: argparse.Namespace) -> int:
    clean_cosmetics(args.dota, args.language)
    return 0


def do_status(args: argparse.Namespace) -> int:
    result = get_status(args.dota, args.language)
    dota = Path(result["dota_path"])
    output_root = Path(result["output_path"])
    current_version = result["current_dota_version"]

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    print(f"Dota: {dota}")
    print(f"Current Dota version: {dota_version_label(current_version)}")
    if result["status"] == "not_built":
        print(f"Status: NOT BUILT - no disabler marker was found under {output_root}")
    elif result["status"] == "legacy":
        print(
            "Status: LEGACY - loose custom-language overrides are no longer loaded by "
            "current Dota. Run build again."
        )
    else:
        print(
            f"Disabler built for: {dota_version_label(result['recorded_dota_version'])}"
        )
        print(f"Generated at: {result['generated_at_utc'] or 'unknown'}")
        if result["status"] == "current":
            print(
                f"Status: CURRENT - the versions match ({result['comparison_basis']})."
            )
        elif result["status"] == "stale":
            print(
                f"Status: STALE - Dota changed ({result['comparison_basis']}). "
                "Run build again."
            )
        elif result["status"] == "broken":
            print(
                "Status: BROKEN - the owned VPK is missing or does not match its recorded "
                "checksum. Run build again."
            )
        else:
            print(
                "Status: UNKNOWN - the existing disabler build has no comparable Dota "
                "version record. Run build once to record it."
            )
    if current_version.get("steam_manifest_error"):
        print(f"Steam metadata warning: {current_version['steam_manifest_error']}")
    elif not current_version.get("steam_build_id"):
        print("Steam build ID was unavailable; comparisons use the VPK file stamp fallback.")
    return 0


def do_history(args: argparse.Namespace) -> int:
    work = (
        Path(args.work).expanduser().resolve()
        if args.work
        else application_root() / ".work"
    )
    history_path = work / HISTORY_FILENAME
    history = read_version_history(history_path)
    if args.json:
        print(json.dumps(history, indent=2, sort_keys=True))
        return 0
    entries = history["entries"]
    print(f"Dota version history: {history_path}")
    if not entries:
        print("No successful disabler builds have been recorded yet.")
        return 0
    for entry in reversed(entries[-args.limit :]):
        partial = "partial" if entry.get("partial") else "complete"
        print(
            f"{entry.get('built_at_utc', 'unknown')} | "
            f"{dota_version_label(entry.get('dota_version'))} | "
            f"disabler {entry.get('generator_version', 'unknown')} | "
            f"{entry.get('resource_overrides', entry.get('model_overrides', '?'))} "
            f"overrides | {partial}"
        )
    return 0


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be at least 1")
    return parsed


def do_gui(args: argparse.Namespace) -> int:
    # Keep Tk lazy so CLI/status/history use no GUI runtime and remain safe in
    # consoles and headless test processes.
    if args.smoke_test and getattr(sys, "frozen", False):
        do_helper_smoke(args)
    from .gui import run_gui

    return run_gui(smoke_test=args.smoke_test)


def do_helper_smoke(_args: argparse.Namespace) -> int:
    validate_vpk_extractor(find_vpk_extractor(None))
    validate_model_patcher(find_model_patcher(None))
    return 0


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate client-side Dota 2 overrides that restore default models and effects"
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    commands = parser.add_subparsers(dest="command", required=True)

    analyze = commands.add_parser("analyze", help="Analyze already-extracted schema files")
    analyze.add_argument(
        "--items-game",
        required=True,
        help="Path to scripts/items/items_game.txt",
    )
    analyze.add_argument(
        "--npc-heroes",
        required=True,
        help="Path to scripts/npc/npc_heroes.txt",
    )
    analyze.add_argument("--npc-units", help="Optional path to scripts/npc/npc_units.txt")
    analyze.add_argument("--report", default="model-plan.json", help="Output JSON report")
    analyze.set_defaults(func=do_analyze)

    build = commands.add_parser(
        "build",
        help="Extract Dota assets and build a cosmetic-override VPK",
    )
    build.add_argument("--dota", help="Path to 'dota 2 beta' (auto-detected when possible)")
    build.add_argument(
        "--extractor",
        help="Path to Dota2VpkExtractor (normally bundled or auto-detected for source builds)",
    )
    build.add_argument(
        "--language",
        default=DEFAULT_LANGUAGE,
        help=(
            f"Recognized compatibility mount (default: {DEFAULT_LANGUAGE}; "
            "english aliases to it)"
        ),
    )
    build.add_argument("--work", help="Cache/report directory (default: .work beside this script)")
    build.add_argument(
        "--clean-first",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Compatibility option; the current VPK deployment replaces its owned archive "
            "as one unit"
        ),
    )
    build.add_argument(
        "--allow-missing",
        action="store_true",
        help="Deploy a partial build even when some replacement source resources are missing",
    )
    build.add_argument(
        "--category",
        action="append",
        choices=SUPPORTED_CATEGORIES,
        help=(
            "Replacement category to include; repeat for multiple categories. "
            "The default includes every supported category."
        ),
    )
    build.set_defaults(func=do_build)

    clean = commands.add_parser(
        "clean",
        help="Remove only files tracked by the generator manifest",
    )
    clean.add_argument("--dota", help="Path to 'dota 2 beta' (auto-detected when possible)")
    clean.add_argument("--language", default=DEFAULT_LANGUAGE, help="Recognized mount to clean")
    clean.set_defaults(func=do_clean)

    status = commands.add_parser(
        "status",
        help="Compare the current Dota version with the generated overrides",
    )
    status.add_argument("--dota", help="Path to 'dota 2 beta' (auto-detected when possible)")
    status.add_argument("--language", default=DEFAULT_LANGUAGE, help="Recognized mount to inspect")
    status.add_argument("--json", action="store_true", help="Write the comparison as JSON")
    status.set_defaults(func=do_status)

    history = commands.add_parser(
        "history",
        help="Show successful disabler builds and their Dota versions",
    )
    history.add_argument(
        "--work",
        help="History directory (default: .work beside this application)",
    )
    history.add_argument(
        "--limit",
        type=positive_int,
        default=10,
        help="Maximum entries to show (default: 10)",
    )
    history.add_argument("--json", action="store_true", help="Write the complete history as JSON")
    history.set_defaults(func=do_history)

    gui = commands.add_parser("gui", help="Open the desktop dashboard")
    gui.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    gui.set_defaults(func=do_gui)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = make_parser()
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        arguments = ["gui"]
    args = parser.parse_args(arguments)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        if os.environ.get("DOTA_DISABLE_COSMETICS_DEBUG"):
            raise
        return 1


__all__ = [
    "do_analyze",
    "do_build",
    "do_clean",
    "do_gui",
    "do_history",
    "do_helper_smoke",
    "do_status",
    "main",
    "make_parser",
    "positive_int",
]
