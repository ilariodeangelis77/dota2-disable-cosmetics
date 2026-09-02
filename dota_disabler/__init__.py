"""Modular core for the Dota 2 Cosmetic Disabler.

The legacy top-level modules remain compatibility entry points while the
implementation migrates into this package.
"""

from .constants import *
from .constants import __all__ as _constant_exports
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


__all__ = [
    *_constant_exports,
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
    "application_root",
    "as_str",
    "canonical",
    "compiled_material_path",
    "compiled_model_path",
    "compiled_override_path",
    "compiled_particle_path",
    "compiled_particle_snapshot_path",
    "find_model_fields",
    "hero_from_item",
    "is_cosmetic_additive_particle",
    "is_safe_resource_path",
    "item_attr",
    "load_hero_models",
    "load_items_game",
    "load_unit_models",
    "looks_like_material",
    "looks_like_model",
    "looks_like_particle",
    "looks_like_particle_snapshot",
    "obj_to_simple_dict",
    "parse_value",
    "path_under",
    "prefab_attr",
    "runtime_asset_root",
    "skip_value",
    "source_root",
    "visual_modifiers",
]
