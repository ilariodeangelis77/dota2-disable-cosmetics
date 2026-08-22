#!/usr/bin/env python3
"""Validate the current installed Dota schema/resources without deploying.

This development gate uses a temporary extraction/staging directory, checks
every final mapping source, and can pack/reopen the complete override VPK for
CRC validation. It never writes under the Dota installation.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dota_disabler.application import load_or_extract_schema, parse_schemas
from dota_disabler.constants import (
    DEFAULT_CATEGORIES,
    INTENTIONALLY_NEUTRAL_PARTICLE_DEFAULTS,
    INTENTIONALLY_NEUTRAL_PARTICLE_PREFIXES,
    NEUTRAL_PARTICLE,
    RESOURCE_MATERIAL,
    RESOURCE_PARTICLE,
)
from dota_disabler.paths import path_under
from dota_disabler.model_patcher import (
    find_model_patcher,
    patch_model_material_groups_batch,
    validate_model_patcher,
)
from dota_disabler.planning import apply_missing_particle_fallbacks, apply_model_skin_material_fallbacks
from dota_disabler.reporting import write_plan
from dota_disabler.resources import (
    compiled_material_path,
    compiled_override_path,
    compiled_particle_path,
    looks_like_model,
)
from dota_disabler.version import VERSION
from dota_disabler.versioning import capture_dota_version, dota_version_label, find_dota_install
from dota_disabler.vpk import extract_vpk, find_vpk_extractor, pack_vpk, validate_vpk_extractor
from dota_disabler.schema import load_items_game


REGRESSION_ITEM_GROUPS = {
    "whitewind_battlemage": "whitewind battlemage",
    "flame_of_origin": "flame of origin",
    "spirit_of_dark_wood": "spirit of the dark wood",
    "abominable_snowbeast": "abominable snowbeast",
    "roost_of_winter_raven": "roost of the winter raven",
}


def item_declares_model_replacement(item) -> bool:
    """Return whether a schema item owns a model target the planner should map."""

    if item.top_models or item.nested_models:
        return True
    for visual in item.visuals:
        modifier_type = visual.get("type", "")
        asset = visual.get("asset", "")
        modifier = visual.get("modifier", "")
        if modifier_type in {
            "entity_model",
            "base_model",
            "entity_clientside_model",
            "hero_model_change",
        } and looks_like_model(modifier):
            return True
        if (
            modifier_type == "model"
            and looks_like_model(asset)
            and looks_like_model(modifier)
        ):
            return True
        if modifier_type == "additional_wearable" and looks_like_model(asset):
            return True
        if modifier_type == "pet" and any(
            looks_like_model(visual.get(field, ""))
            for field in ("asset", "pickup_item")
        ):
            return True
    return False


def audit_live_install(
    dota_path: str | None,
    extractor_path: str | None,
    model_patcher_path: str | None,
    *,
    pack: bool,
    report: Path | None,
    temporary_root: Path | None,
) -> dict:
    dota = find_dota_install(dota_path)
    extractor = find_vpk_extractor(extractor_path)
    validate_vpk_extractor(extractor)
    game_pak = dota / "game/dota/pak01_dir.vpk"

    with tempfile.TemporaryDirectory(
        prefix="dota-disabler-audit-",
        dir=temporary_root,
    ) as temporary:
        audit_root = Path(temporary)
        cache = audit_root / "extracted"
        items, heroes, units = load_or_extract_schema(dota, extractor, cache)
        plan = parse_schemas(items, heroes, units)

        source_resources = {
            compiled_override_path(mapping.source, mapping.resource_type)
            for mapping in plan.mappings
        }
        if any(mapping.resource_type == RESOURCE_PARTICLE for mapping in plan.mappings):
            source_resources.add(compiled_particle_path(NEUTRAL_PARTICLE))
        extract_vpk(extractor, game_pak, sorted(source_resources), cache)

        plan = apply_model_skin_material_fallbacks(plan, cache)
        model_patcher = None
        if plan.stats.get("alternate_skin_group_patch_targets", 0):
            model_patcher = find_model_patcher(model_patcher_path)
            validate_model_patcher(model_patcher)
        material_sources = sorted(
            {
                compiled_material_path(mapping.source)
                for mapping in plan.mappings
                if mapping.resource_type == RESOURCE_MATERIAL
            }
        )
        if material_sources:
            extract_vpk(extractor, game_pak, material_sources, cache)
        absent_particle_defaults = [
            {
                "source": mapping.source,
                "target": mapping.target,
                "item_id": mapping.item_id,
                "intentional": (
                    mapping.source in INTENTIONALLY_NEUTRAL_PARTICLE_DEFAULTS
                    or mapping.source.startswith(
                        INTENTIONALLY_NEUTRAL_PARTICLE_PREFIXES
                    )
                ),
            }
            for mapping in plan.mappings
            if mapping.resource_type == RESOURCE_PARTICLE
            and not path_under(
                cache,
                compiled_override_path(mapping.source, mapping.resource_type),
            ).is_file()
        ]
        plan = apply_missing_particle_fallbacks(plan, cache)

        missing_sources = sorted(
            {
                compiled_override_path(mapping.source, mapping.resource_type)
                for mapping in plan.mappings
                if not path_under(
                    cache,
                    compiled_override_path(mapping.source, mapping.resource_type),
                ).is_file()
            }
        )
        if missing_sources:
            raise RuntimeError(
                f"{len(missing_sources)} final mapping source(s) are absent; "
                f"first: {missing_sources[0]}"
            )

        packed_resources = 0
        if pack:
            staging = audit_root / "staging"
            patch_jobs = []
            for mapping in plan.mappings:
                source = path_under(
                    cache,
                    compiled_override_path(mapping.source, mapping.resource_type),
                )
                target = path_under(
                    staging,
                    compiled_override_path(mapping.target, mapping.resource_type),
                )
                target.parent.mkdir(parents=True, exist_ok=True)
                if mapping.resource_type == "model" and mapping.required_material_groups > 1:
                    patch_jobs.append((source, target, mapping.required_material_groups))
                else:
                    shutil.copy2(source, target)
            patch_model_material_groups_batch(
                model_patcher,
                patch_jobs,
                staging,
                progress=lambda _message: None,
            )
            packed_resources = pack_vpk(
                extractor,
                staging,
                audit_root / "pak98_dir.vpk",
            )
            if packed_resources != len(plan.mappings):
                raise RuntimeError(
                    "Packed resource count does not match the final mapping count: "
                    f"{packed_resources} != {len(plan.mappings)}"
                )

        if report is not None:
            write_plan(plan, report, enabled_categories=DEFAULT_CATEGORIES)

        _, item_records, _ = load_items_game(items)
        named_regressions = {}
        for label, marker in REGRESSION_ITEM_GROUPS.items():
            matching_items = {
                item.item_id: item.name
                for item in item_records.values()
                if marker in item.name.casefold()
            }
            matching_ids = set(matching_items)
            expected_model_ids = {
                item.item_id
                for item in item_records.values()
                if item.item_id in matching_ids
                and item_declares_model_replacement(item)
            }
            mapped_model_ids = {
                mapping.item_id
                for mapping in plan.mappings
                if mapping.resource_type == "model"
                and mapping.item_id in expected_model_ids
            }
            named_regressions[label] = {
                "items": matching_items,
                "expected_model_item_count": len(expected_model_ids),
                "mapped_model_item_count": len(mapped_model_ids),
                "missing_model_items": {
                    item_id: matching_items[item_id]
                    for item_id in sorted(expected_model_ids - mapped_model_ids)
                },
                "material_mapping_count": sum(
                    mapping.resource_type == "material"
                    and mapping.item_id in matching_ids
                    for mapping in plan.mappings
                ),
                "material_unresolved_count": sum(
                    diagnostic.get("type") == "alternate_skin_material_unresolved"
                    and diagnostic.get("item_id") in matching_ids
                    for diagnostic in plan.unresolved
                ),
                "model_noop_skipped_count": sum(
                    diagnostic.get("type") == "alternate_skin_model_preserved"
                    and diagnostic.get("item_id") in matching_ids
                    for diagnostic in plan.unresolved
                ),
            }

        regression_failures = {
            label: summary
            for label, summary in named_regressions.items()
            if not summary["items"]
            or summary["missing_model_items"]
            or summary["model_noop_skipped_count"]
        }
        if regression_failures:
            raise RuntimeError(
                "Named live-schema regression gate failed: "
                + ", ".join(sorted(regression_failures))
            )

        version = capture_dota_version(dota)
        return {
            "generator_version": VERSION,
            "dota_path": str(dota),
            "dota_version": version,
            "dota_version_label": dota_version_label(version),
            "mapping_stats": plan.stats,
            "named_regressions": named_regressions,
            "absent_particle_defaults": absent_particle_defaults,
            "missing_source_count": 0,
            "packed_resource_count": packed_resources,
            "report": str(report) if report is not None else None,
        }


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dota", help="Path to 'dota 2 beta' (auto-detected by default)")
    parser.add_argument("--extractor", help="Path to Dota2VpkExtractor")
    parser.add_argument("--model-patcher", help="Path to Dota2ModelSkinPatcher")
    parser.add_argument(
        "--pack",
        action="store_true",
        help="Also pack and CRC-validate the complete temporary override VPK",
    )
    parser.add_argument("--report", type=Path, help="Optional final plan JSON path")
    parser.add_argument(
        "--temp-root",
        type=Path,
        help="Parent for temporary audit files (use a drive with several GB free)",
    )
    return parser


def main() -> int:
    args = make_parser().parse_args()
    report = args.report.expanduser().resolve() if args.report else None
    temporary_root = args.temp_root.expanduser().resolve() if args.temp_root else None
    if temporary_root is not None:
        temporary_root.mkdir(parents=True, exist_ok=True)
    result = audit_live_install(
        args.dota,
        args.extractor,
        args.model_patcher,
        pack=args.pack,
        report=report,
        temporary_root=temporary_root,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
