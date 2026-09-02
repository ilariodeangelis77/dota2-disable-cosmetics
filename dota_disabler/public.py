"""Explicit compatibility surface for the legacy launcher and desktop UI.

The root ``disable_cosmetics`` module re-exports this module during the staged
refactor. Domain records and exceptions are imported directly from their owner
modules so every adapter observes the same class identity.
"""

from __future__ import annotations

import os as os
import sys
from pathlib import Path
from typing import Optional

from .application import (
    _build_cosmetics_unlocked,
    build_cosmetics,
    clean_cosmetics,
    clean_legacy_output_after_migration,
    clean_other_language_outputs_after_migration,
    get_status,
    load_or_extract_schema,
    parse_schemas,
)
from .cli import (
    do_analyze,
    do_build,
    do_clean,
    do_gui,
    do_history,
    do_status,
    main,
    make_parser,
    positive_int,
)
from .constants import (
    CATEGORY_ADDITIONAL_WEARABLES,
    CATEGORY_PARTICLE_EFFECTS,
    CATEGORY_PERSONA_MODELS,
    CATEGORY_SPECIAL_MODELS,
    CATEGORY_STANDARD_WEARABLES,
    COSMETIC_ADDITIVE_PARTICLE_PREFIXES,
    DEFAULT_CATEGORIES,
    DEFAULT_LANGUAGE,
    DEFAULT_MODEL_CATEGORIES,
    FULL_HERO_INTEGRATED_SLOTS,
    FULL_HERO_WEARABLE_ITEMS,
    HISTORY_FILENAME,
    HISTORY_FORMAT_VERSION,
    HISTORY_KIND,
    INTENTIONALLY_NEUTRAL_PARTICLE_DEFAULTS,
    INTENTIONALLY_NEUTRAL_PARTICLE_PREFIXES,
    INVISIBLE_MODEL,
    ITEMS_SCHEMA_RESOURCE,
    LEGACY_LANGUAGE,
    MARKER_FILENAME,
    MARKER_KIND,
    MODEL_ASSET_DEFAULT_EXCEPTIONS,
    MODEL_CATEGORIES,
    MODEL_KEYS,
    NEUTRAL_PARTICLE,
    PARTICLE_DEFAULT_PATH_EXCEPTIONS,
    PARTICLE_REPLACEMENT_TYPES,
    RECOGNIZED_LANGUAGES,
    RESOURCE_MATERIAL,
    RESOURCE_MODEL,
    RESOURCE_PARTICLE,
    RESOURCE_SNAPSHOT,
    RETIRED_ITEM_NAME_MARKERS,
    SUPPORTED_CATEGORIES,
    VPK_ARCHIVE_CANDIDATES,
    VPK_DEPLOYMENT_MODE,
)
from .deployment import (
    choose_vpk_archive_name,
    clean_output,
    deploy_overrides,
    marker_enabled_categories,
    read_marker,
    remove_tracked_files,
    sha256_file,
    validate_category_transition,
    validate_language,
)
from .domain import (
    BuildOptions,
    BuildResult,
    CleanResult,
    ItemRecord,
    Mapping,
    ModelAttachmentOffset,
    ModelComposition,
    ModelCompositionPart,
    Plan,
    ProgressCallback,
    ProgressUpdateCallback,
    WorkProgressCallback,
)
from .errors import GeneratorError, UnsafeOutputError
from .keyvalues import (
    KVObject,
    KVValue,
    TokenStream,
    as_str,
    obj_to_simple_dict,
    parse_value,
    skip_value,
)
from .paths import application_root, path_under, runtime_asset_root, source_root
from .planning import (
    apply_missing_particle_fallbacks,
    apply_model_skin_material_fallbacks,
    build_plan,
    neutralize_item_bodygroups,
    stage_bodygroup_schema_overlay,
)
from .reporting import write_json, write_plan
from .resources import (
    canonical,
    compiled_material_path,
    compiled_model_path,
    compiled_override_path,
    compiled_particle_path,
    compiled_particle_snapshot_path,
    is_cosmetic_additive_particle,
    is_safe_resource_path,
    looks_like_material,
    looks_like_model,
    looks_like_particle,
    looks_like_particle_snapshot,
)
from .schema import (
    find_model_fields,
    hero_from_item,
    item_attr,
    load_hero_models,
    load_items_game,
    load_unit_models,
    prefab_attr,
    visual_modifiers,
)
from .version import VERSION
from .versioning import (
    append_version_history,
    capture_dota_version,
    compare_dota_versions,
    dota_changed_during_build,
    dota_operation_lock,
    dota_version_label,
    find_dota_appmanifest,
    find_dota_install,
    parse_dota_appmanifest,
    read_version_history,
)
from .vpk import (
    extract_vpk,
    find_vpk_extractor,
    list_vpk_resources,
    pack_vpk,
    run,
    stage_english_language_support,
    validate_vpk_extractor,
)


def safely_append_version_history(
    path: Path,
    payload: Optional[dict],
    entry: dict,
    *,
    warning: ProgressCallback,
) -> bool:
    """Compatibility wrapper that retains the legacy facade patch point.

    Existing integrations patch ``disable_cosmetics.append_version_history``
    to simulate a post-deployment persistence failure. Resolve that hook from
    the facade at call time while keeping the canonical writer as the default.
    """

    if payload is None:
        return False
    facade = sys.modules.get("disable_cosmetics")
    writer = getattr(facade, "append_version_history", append_version_history)
    try:
        writer(path, payload, entry)
    except Exception as exc:
        warning(
            "WARNING: Overrides were built, but version history could not be updated: "
            f"{exc}"
        )
        return False
    return True


__all__ = [
    "CATEGORY_ADDITIONAL_WEARABLES",
    "CATEGORY_PARTICLE_EFFECTS",
    "CATEGORY_PERSONA_MODELS",
    "CATEGORY_SPECIAL_MODELS",
    "CATEGORY_STANDARD_WEARABLES",
    "COSMETIC_ADDITIVE_PARTICLE_PREFIXES",
    "DEFAULT_CATEGORIES",
    "DEFAULT_LANGUAGE",
    "DEFAULT_MODEL_CATEGORIES",
    "FULL_HERO_INTEGRATED_SLOTS",
    "FULL_HERO_WEARABLE_ITEMS",
    "HISTORY_FILENAME",
    "HISTORY_FORMAT_VERSION",
    "HISTORY_KIND",
    "INTENTIONALLY_NEUTRAL_PARTICLE_DEFAULTS",
    "INTENTIONALLY_NEUTRAL_PARTICLE_PREFIXES",
    "INVISIBLE_MODEL",
    "ITEMS_SCHEMA_RESOURCE",
    "LEGACY_LANGUAGE",
    "MARKER_FILENAME",
    "MARKER_KIND",
    "MODEL_ASSET_DEFAULT_EXCEPTIONS",
    "MODEL_CATEGORIES",
    "MODEL_KEYS",
    "NEUTRAL_PARTICLE",
    "PARTICLE_DEFAULT_PATH_EXCEPTIONS",
    "PARTICLE_REPLACEMENT_TYPES",
    "RECOGNIZED_LANGUAGES",
    "RESOURCE_MATERIAL",
    "RESOURCE_MODEL",
    "RESOURCE_PARTICLE",
    "RESOURCE_SNAPSHOT",
    "RETIRED_ITEM_NAME_MARKERS",
    "SUPPORTED_CATEGORIES",
    "VPK_ARCHIVE_CANDIDATES",
    "VPK_DEPLOYMENT_MODE",
    "VERSION",
    "BuildOptions",
    "BuildResult",
    "CleanResult",
    "GeneratorError",
    "ItemRecord",
    "KVObject",
    "KVValue",
    "Mapping",
    "ModelAttachmentOffset",
    "ModelComposition",
    "ModelCompositionPart",
    "Plan",
    "ProgressCallback",
    "ProgressUpdateCallback",
    "WorkProgressCallback",
    "TokenStream",
    "UnsafeOutputError",
    "_build_cosmetics_unlocked",
    "append_version_history",
    "application_root",
    "apply_missing_particle_fallbacks",
    "apply_model_skin_material_fallbacks",
    "as_str",
    "build_cosmetics",
    "build_plan",
    "canonical",
    "capture_dota_version",
    "choose_vpk_archive_name",
    "clean_cosmetics",
    "clean_legacy_output_after_migration",
    "clean_other_language_outputs_after_migration",
    "clean_output",
    "compare_dota_versions",
    "compiled_material_path",
    "compiled_model_path",
    "compiled_override_path",
    "compiled_particle_path",
    "compiled_particle_snapshot_path",
    "deploy_overrides",
    "do_analyze",
    "do_build",
    "do_clean",
    "do_gui",
    "do_history",
    "do_status",
    "dota_changed_during_build",
    "dota_operation_lock",
    "dota_version_label",
    "extract_vpk",
    "find_dota_appmanifest",
    "find_dota_install",
    "find_model_fields",
    "find_vpk_extractor",
    "get_status",
    "hero_from_item",
    "is_cosmetic_additive_particle",
    "is_safe_resource_path",
    "item_attr",
    "list_vpk_resources",
    "load_hero_models",
    "load_items_game",
    "load_or_extract_schema",
    "load_unit_models",
    "looks_like_material",
    "looks_like_model",
    "looks_like_particle",
    "looks_like_particle_snapshot",
    "main",
    "make_parser",
    "marker_enabled_categories",
    "neutralize_item_bodygroups",
    "obj_to_simple_dict",
    "os",
    "pack_vpk",
    "parse_dota_appmanifest",
    "parse_schemas",
    "parse_value",
    "path_under",
    "positive_int",
    "prefab_attr",
    "read_marker",
    "read_version_history",
    "remove_tracked_files",
    "run",
    "runtime_asset_root",
    "safely_append_version_history",
    "sha256_file",
    "skip_value",
    "source_root",
    "stage_bodygroup_schema_overlay",
    "stage_english_language_support",
    "validate_category_transition",
    "validate_language",
    "validate_vpk_extractor",
    "visual_modifiers",
    "write_json",
    "write_plan",
]
