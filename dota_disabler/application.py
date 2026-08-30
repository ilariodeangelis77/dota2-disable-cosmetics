"""Application services that orchestrate planning, VPK work, and deployment."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from .constants import (
    HISTORY_FILENAME,
    NEUTRAL_PARTICLE,
    RESOURCE_MATERIAL,
    RESOURCE_MODEL,
    RESOURCE_PARTICLE,
    SUPPORTED_CATEGORIES,
    VPK_DEPLOYMENT_MODE,
)
from .deployment import (
    clean_cosmetics,
    clean_legacy_output_after_migration,
    clean_other_language_outputs_after_migration,
    deploy_overrides,
    get_status,
    read_marker,
    validate_category_transition,
    validate_language,
)
from .domain import (
    BuildOptions,
    BuildResult,
    Plan,
    ProgressCallback,
    ProgressUpdateCallback,
    WorkProgressCallback,
)
from .errors import GeneratorError
from .model_patcher import find_model_patcher, validate_model_patcher
from .paths import application_root
from .planning import (
    apply_missing_particle_fallbacks,
    apply_model_skin_material_fallbacks,
    build_plan,
)
from .progress import BUILD_PHASE_WEIGHTS, WeightedProgress
from .reporting import write_plan
from .resources import (
    compiled_material_path,
    compiled_override_path,
    compiled_particle_path,
)
from .schema import load_hero_models, load_items_game, load_unit_models
from .version import VERSION
from .versioning import (
    capture_dota_version,
    compare_dota_versions,
    dota_changed_during_build,
    dota_operation_lock,
    dota_version_label,
    find_dota_install,
    read_version_history,
    safely_append_version_history,
)
from .vpk import (
    extract_vpk,
    find_vpk_extractor,
    validate_vpk_extractor,
)


def load_or_extract_schema(
    dota: Path,
    extractor: Path,
    cache: Path,
    *,
    progress: ProgressCallback = print,
    progress_update: Optional[WorkProgressCallback] = None,
) -> tuple[Path, Path, Path]:
    pak = dota / "game/dota/pak01_dir.vpk"
    items = cache / "scripts/items/items_game.txt"
    heroes = cache / "scripts/npc/npc_heroes.txt"
    units = cache / "scripts/npc/npc_units.txt"
    extract_vpk(
        extractor,
        pak,
        (
            "scripts/items/items_game.txt",
            "scripts/npc/npc_heroes.txt",
            "scripts/npc/npc_units.txt",
        ),
        cache,
        progress=progress,
        progress_update=progress_update,
    )
    if not items.is_file() or not heroes.is_file() or not units.is_file():
        raise FileNotFoundError(
            "Schema extraction finished but items_game.txt, npc_heroes.txt, or npc_units.txt was not produced. "
            "The installed Dota VPK may have changed or be incomplete."
        )
    return items, heroes, units


def parse_schemas(
    items_path: Path,
    heroes_path: Path,
    units_path: Optional[Path] = None,
    *,
    enabled_categories: Optional[Iterable[str]] = None,
    progress: ProgressCallback = print,
    parse_progress: Optional[WorkProgressCallback] = None,
    planning_progress: Optional[WorkProgressCallback] = None,
) -> Plan:
    parse_total = 3 if units_path is not None else 2
    progress("Parsing items_game.txt...")
    prefabs, items, global_visuals = load_items_game(items_path)
    if parse_progress is not None:
        parse_progress("parse", 1, parse_total)
    progress("Parsing npc_heroes.txt...")
    hero_models = load_hero_models(heroes_path)
    if parse_progress is not None:
        parse_progress("parse", 2, parse_total)
    unit_models: dict[str, str] = {}
    if units_path is not None:
        progress("Parsing npc_units.txt...")
        unit_models = load_unit_models(units_path)
        if parse_progress is not None:
            parse_progress("parse", 3, parse_total)
    return build_plan(
        prefabs,
        items,
        hero_models,
        global_visuals,
        enabled_categories=enabled_categories,
        unit_models=unit_models,
        work_progress=planning_progress,
    )


def _build_cosmetics_unlocked(
    options: BuildOptions,
    *,
    progress: ProgressCallback = print,
    progress_update: Optional[ProgressUpdateCallback] = None,
    warning: Optional[ProgressCallback] = None,
    resolved_dota: Optional[Path] = None,
) -> BuildResult:
    build_progress = WeightedProgress(progress_update, BUILD_PHASE_WEIGHTS)
    build_progress.begin("validation", "Validating the Dota installation")
    warning = warning or progress
    enabled_categories = frozenset(options.enabled_categories)
    if not enabled_categories:
        raise ValueError("Select at least one replacement category to build.")
    unknown_categories = enabled_categories.difference(SUPPORTED_CATEGORIES)
    if unknown_categories:
        raise ValueError(
            f"Unknown replacement categories: {', '.join(sorted(unknown_categories))}"
        )
    build_progress.work("validation", 1, 5, "Build options validated")

    dota = resolved_dota or find_dota_install(options.dota)
    build_progress.work("validation", 2, 5, "Dota installation found")
    extractor = find_vpk_extractor(options.extractor)
    validate_vpk_extractor(extractor)
    build_progress.work("validation", 3, 5, "Bundled VPK extractor validated")
    language = validate_language(options.language)
    work = (
        Path(options.work).expanduser().resolve()
        if options.work
        else application_root() / ".work"
    )
    cache = work / "extracted"
    report = work / "model-plan.json"
    output_root = dota / "game" / f"dota_{language}"
    pak = dota / "game/dota/pak01_dir.vpk"
    history_path = work / HISTORY_FILENAME
    build_progress.work("validation", 4, 5, "Build paths and language validated")
    try:
        history: Optional[dict] = read_version_history(history_path)
    except GeneratorError as exc:
        history = None
        warning(
            f"WARNING: Existing version history is invalid and will not be overwritten: {exc}"
        )
    current_version = capture_dota_version(dota)
    previous_marker = read_marker(output_root, allow_shared_directory=True)
    validate_category_transition(
        previous_marker,
        enabled_categories,
        clean_first=options.clean_first,
    )
    build_progress.complete(
        "validation",
        "Dota installation and previous build validated",
    )

    progress(f"Dota: {dota}")
    progress(f"Current Dota version: {dota_version_label(current_version)}")
    if current_version.get("steam_manifest_error"):
        warning(
            "WARNING: Steam build metadata could not be read; using the VPK stamp instead: "
            f"{current_version['steam_manifest_error']}"
        )
    elif not current_version.get("steam_build_id"):
        progress("NOTE: Steam build metadata was not found; using the VPK stamp instead.")
    if previous_marker:
        comparison, basis = compare_dota_versions(
            previous_marker.get("dota_version"),
            current_version,
        )
        if comparison == "same":
            progress(f"Previous disabler build matches the current Dota version ({basis}).")
        elif comparison == "different":
            progress(f"Dota changed since the previous disabler build ({basis}); rebuilding now.")
        else:
            progress(
                "Previous disabler build has no comparable Dota version record; "
                "this build will add one."
            )
    progress(f"VPK extractor: {extractor}")
    progress(f"Enabled categories: {', '.join(sorted(enabled_categories))}")

    build_progress.begin("schema_extract", "Extracting the current Dota schemas")
    items_path, heroes_path, units_path = load_or_extract_schema(
        dota,
        extractor,
        cache,
        progress=progress,
        progress_update=build_progress.work_callback(
            "schema_extract",
            "Extracting Dota schemas",
        ),
    )
    build_progress.complete("schema_extract", "Current Dota schemas extracted")
    build_progress.begin("schema_parse", "Parsing the current Dota schemas")
    plan = parse_schemas(
        items_path,
        heroes_path,
        units_path,
        enabled_categories=enabled_categories,
        progress=progress,
        parse_progress=build_progress.work_callback(
            "schema_parse",
            "Parsing Dota schemas",
        ),
        planning_progress=build_progress.work_callback(
            "planning",
            "Planning cosmetic replacements",
        ),
    )
    # Persist the schema-only plan even when a later extraction or deployment
    # validation fails.
    write_plan(plan, report, enabled_categories=enabled_categories)
    build_progress.complete(
        "planning",
        f"Planned {len(plan.mappings):,} replacement mapping(s)",
    )

    source_resources = {
        compiled_override_path(mapping.source, mapping.resource_type)
        for mapping in plan.mappings
    }
    if any(mapping.resource_type == RESOURCE_PARTICLE for mapping in plan.mappings):
        source_resources.add(compiled_particle_path(NEUTRAL_PARTICLE))
    sorted_source_resources = sorted(source_resources)
    progress(
        f"Extracting {len(sorted_source_resources)} unique replacement resource(s)..."
    )
    build_progress.begin("source_extract", "Extracting replacement resources from Dota")
    extract_vpk(
        extractor,
        pak,
        sorted_source_resources,
        cache,
        progress=progress,
        progress_update=build_progress.work_callback(
            "source_extract",
            "Extracting replacement resources",
        ),
    )
    build_progress.complete("source_extract", "Replacement resources extracted")

    build_progress.begin("model_analysis", "Checking default-model material groups")
    plan = apply_model_skin_material_fallbacks(
        plan,
        cache,
        work_progress=build_progress.work_callback(
            "model_analysis",
            "Checking default-model material groups",
        ),
    )
    build_progress.complete("model_analysis", "Default-model material groups checked")
    group_patch_targets = sum(
        mapping.resource_type == RESOURCE_MODEL
        and mapping.required_material_groups > 1
        for mapping in plan.mappings
    )
    model_patcher: Optional[Path] = None
    if group_patch_targets:
        model_patcher = find_model_patcher()
        validate_model_patcher(model_patcher)
        progress(
            f"Preparing {group_patch_targets} skin-sensitive default-model replacement(s) "
            "with duplicated base material groups."
        )
    preserved_skin_models = plan.stats.get("alternate_skin_models_skipped", 0)
    if preserved_skin_models:
        progress(
            f"Preserved {preserved_skin_models} skin-sensitive cosmetic model target(s) "
            "whose default replacement cannot safely accept the equipped material group."
        )
    material_sources = sorted(
        {
            compiled_material_path(mapping.source)
            for mapping in plan.mappings
            if mapping.resource_type == RESOURCE_MATERIAL
        }
    )
    if material_sources:
        build_progress.begin("material_extract", "Extracting base material resources")
        progress(
            f"Extracting {len(material_sources)} base material resource(s) "
            "for alternate-skin cleanup..."
        )
        extract_vpk(
            extractor,
            pak,
            material_sources,
            cache,
            progress=progress,
            progress_update=build_progress.work_callback(
                "material_extract",
                "Extracting base material resources",
            ),
        )
    build_progress.complete("material_extract", "Model and material resources prepared")

    build_progress.begin("particle_validation", "Validating particle fallbacks")
    plan = apply_missing_particle_fallbacks(
        plan,
        cache,
        work_progress=build_progress.work_callback(
            "particle_validation",
            "Validating particle fallbacks",
        ),
    )
    unknown_particle_fallbacks = plan.stats.get(
        "particle_unknown_defaults_neutralized",
        0,
    )
    if unknown_particle_fallbacks:
        warning(
            f"NOTICE: {unknown_particle_fallbacks} unexpected schema default particle(s) "
            "were absent from this Dota build; their cosmetic targets will use Dota's "
            "neutral particle."
        )
    write_plan(plan, report, enabled_categories=enabled_categories)
    progress(json.dumps(plan.stats, indent=2))

    latest_version = capture_dota_version(dota)
    if dota_changed_during_build(current_version, latest_version):
        raise GeneratorError(
            "Dota changed while the disabler build was running. Run build again."
        )
    current_version = latest_version
    built_at_utc = datetime.now(timezone.utc).isoformat()
    build_progress.complete(
        "particle_validation",
        "Particle fallbacks and Dota version validated",
    )
    build_progress.begin("deployment", "Preparing the deployable override archive")
    copied, missing = deploy_overrides(
        plan,
        cache,
        output_root,
        work,
        extractor=extractor,
        model_patcher=model_patcher,
        game_pak=pak,
        items_schema=items_path,
        clean_first=options.clean_first,
        allow_missing=options.allow_missing,
        language=language,
        dota_version=current_version,
        generated_at_utc=built_at_utc,
        enabled_categories=enabled_categories,
        progress=progress,
        progress_update=build_progress.child_callback("deployment"),
    )
    build_progress.complete("deployment", "Override archive installed")
    build_progress.begin("finalize", "Finalizing the completed build")
    clean_legacy_output_after_migration(
        dota,
        progress=progress,
        warning=warning,
    )
    build_progress.work("finalize", 1, 3, "Checking legacy generated output")
    clean_other_language_outputs_after_migration(
        dota,
        language,
        progress=progress,
        warning=warning,
    )
    build_progress.work("finalize", 2, 3, "Checking previous language mounts")
    history_recorded = safely_append_version_history(
        history_path,
        history,
        {
            "built_at_utc": built_at_utc,
            "dota_path": str(dota),
            "dota_version": current_version,
            "generator_version": VERSION,
            "deployment_mode": VPK_DEPLOYMENT_MODE,
            "language": language,
            "resource_overrides": copied,
            "model_overrides": plan.stats["model_overrides"],
            "particle_overrides": plan.stats["particle_overrides"],
            "particle_snapshot_overrides": plan.stats["particle_snapshot_overrides"],
            "partial": bool(missing),
            "enabled_categories": sorted(enabled_categories),
            "mapping_stats": plan.stats,
        },
        warning=warning,
    )
    build_progress.work("finalize", 3, 3, "Recording Dota build history")
    build_progress.complete("finalize", "Build complete")

    progress("")
    progress(
        f"Built {copied} model/effect resource override(s) into a CRC-validated VPK under:"
    )
    progress(f"  {output_root}")
    progress("Mapping report:")
    progress(f"  {report}")
    if history_recorded:
        progress("Dota version history:")
        progress(f"  {history_path}")
    else:
        progress("Dota version history was not updated; review the warning above.")
    if missing:
        warning(f"WARNING: partial build; {len(missing)} source resource(s) were missing.")
    progress("")
    progress("Steam launch option to test:")
    progress(f"  -language {language}")
    progress("")
    progress(
        "This build changes models and supported particles; sounds, icons, and animations may remain."
    )
    return BuildResult(
        dota=dota,
        output_root=output_root,
        report=report,
        history=history_path,
        copied=copied,
        missing=tuple(missing),
        stats=plan.stats,
        dota_version=current_version,
        enabled_categories=tuple(sorted(enabled_categories)),
        history_recorded=history_recorded,
    )


def build_cosmetics(
    options: BuildOptions,
    *,
    progress: ProgressCallback = print,
    progress_update: Optional[ProgressUpdateCallback] = None,
    warning: Optional[ProgressCallback] = None,
) -> BuildResult:
    dota = find_dota_install(options.dota)
    with dota_operation_lock(dota):
        return _build_cosmetics_unlocked(
            options,
            progress=progress,
            progress_update=progress_update,
            warning=warning,
            resolved_dota=dota,
        )


__all__ = [
    "_build_cosmetics_unlocked",
    "build_cosmetics",
    "clean_cosmetics",
    "clean_legacy_output_after_migration",
    "clean_other_language_outputs_after_migration",
    "get_status",
    "load_or_extract_schema",
    "parse_schemas",
]
