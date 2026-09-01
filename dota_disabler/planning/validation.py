"""Final mapping invariants and compatibility statistics."""

from __future__ import annotations

from ..constants import (
    RESOURCE_MODEL,
    RESOURCE_PARTICLE,
    RESOURCE_SNAPSHOT,
    SUPPORTED_CATEGORIES,
)
from ..domain import Mapping, Plan
from .context import PlanningContext


def _assert_resolved_mapping_invariants(mappings: list[Mapping]) -> None:
    targets: set[str] = set()
    for mapping in mappings:
        if mapping.target in targets:
            raise ValueError(f"Planner emitted duplicate target: {mapping.target}")
        targets.add(mapping.target)


def finalize_plan(context: PlanningContext, mappings: list[Mapping]) -> Plan:
    """Validate resolved mappings and reproduce the stable report counters."""

    _assert_resolved_mapping_invariants(mappings)
    model_mappings = [
        mapping for mapping in mappings if mapping.resource_type == RESOURCE_MODEL
    ]
    particle_mappings = [
        mapping for mapping in mappings if mapping.resource_type == RESOURCE_PARTICLE
    ]
    snapshot_mappings = [
        mapping for mapping in mappings if mapping.resource_type == RESOURCE_SNAPSHOT
    ]
    counters = context.counters
    stats = {
        "items": len(context.items),
        "heroes_with_base_models": len(context.hero_models),
        "hero_slot_defaults": len(context.defaults),
        "resource_overrides": len(mappings),
        "model_overrides": len(model_mappings),
        "particle_overrides": len(particle_mappings),
        "particle_snapshot_overrides": len(snapshot_mappings),
        "unique_source_models": len({mapping.source for mapping in model_mappings}),
        "unique_source_particles": len(
            {mapping.source for mapping in particle_mappings}
        ),
        "unique_source_particle_snapshots": len(
            {mapping.source for mapping in snapshot_mappings}
        ),
        "global_visual_modifiers_skipped": (
            len(context.global_visuals) - counters["global_particle_mappings"]
        ),
        "global_particle_mappings": counters["global_particle_mappings"],
        "particle_default_replacements": counters["particle_default_replacements"],
        "particle_additive_hidden": counters["particle_additive_hidden"],
        "particle_default_creates_preserved": counters[
            "particle_default_creates_preserved"
        ],
        "particle_additive_shared_paths_skipped": counters[
            "particle_additive_shared_paths_skipped"
        ],
        "particle_snapshot_replacements": counters["particle_snapshot_replacements"],
        "particle_protected_targets_skipped": counters[
            "particle_protected_targets_skipped"
        ],
        "particle_rules_unsupported": counters["particle_rules_unsupported"],
        "particle_suppressed_defaults_restored": counters[
            "particle_suppressed_defaults_restored"
        ],
        "particle_slot_defaults_restored": counters[
            "particle_slot_defaults_restored"
        ],
        "particle_reviewed_default_fallbacks": counters[
            "particle_reviewed_default_fallbacks"
        ],
        "particle_defaults_resolved_transitively": counters[
            "particle_defaults_resolved_transitively"
        ],
        "particle_resolution_cycles": counters["particle_resolution_cycles"],
        "particle_missing_defaults_hidden": 0,
        "particle_virtual_defaults_neutralized": 0,
        "particle_unknown_defaults_neutralized": 0,
        "unresolved": len(context.unresolved),
        "mapping_conflicts": counters["mapping_conflicts"],
        "model_asset_defaults_inferred": counters["model_asset_defaults_inferred"],
        "model_asset_reviewed_exceptions": counters[
            "model_asset_reviewed_exceptions"
        ],
        "model_asset_original_fallbacks": counters[
            "model_asset_original_fallbacks"
        ],
        "integrated_slot_cosmetics_hidden": counters[
            "integrated_slot_cosmetics_hidden"
        ],
        "bodygroup_sensitive_models_skipped": counters[
            "bodygroup_sensitive_models_skipped"
        ],
        "bodygroup_schema_items_reset": 0,
        "bodygroup_hero_fallbacks": counters["bodygroup_hero_fallbacks"],
        "full_hero_wearable_fallbacks": counters[
            "full_hero_wearable_fallbacks"
        ],
        "persona_slot_defaults_restored": counters[
            "persona_slot_defaults_restored"
        ],
        "persona_profiles_validated": counters["persona_profiles_validated"],
        "persona_profile_slots_resolved": counters[
            "persona_profile_slots_resolved"
        ],
        "persona_profile_slots_unresolved": counters[
            "persona_profile_slots_unresolved"
        ],
        "alternate_skin_models_skipped": counters["alternate_skin_models_skipped"],
        "entity_default_replacements": counters["entity_default_replacements"],
        "pet_models_hidden": counters["pet_models_hidden"],
        "retired_items_skipped": counters["retired_items_skipped"],
    }
    for category in SUPPORTED_CATEGORIES:
        stats[f"category_{category}"] = sum(
            mapping.category == category for mapping in mappings
        )
    return Plan(
        mappings=mappings,
        unresolved=context.unresolved,
        stats=stats,
    )


__all__ = ["finalize_plan"]
