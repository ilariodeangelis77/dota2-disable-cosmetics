"""Resource-aware fallbacks for schema particle defaults."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Optional

from ..constants import (
    INTENTIONALLY_NEUTRAL_PARTICLE_DEFAULTS,
    INTENTIONALLY_NEUTRAL_PARTICLE_PREFIXES,
    NEUTRAL_PARTICLE,
    RESOURCE_MODEL,
    RESOURCE_PARTICLE,
    RESOURCE_SNAPSHOT,
    SUPPORTED_CATEGORIES,
)
from ..domain import Mapping, Plan, WorkProgressCallback
from ..paths import path_under
from ..resources import (
    canonical,
    compiled_model_path,
    compiled_override_path,
    compiled_particle_path,
)


def _discard_unavailable_model_particle_bridges(plan: Plan, cache: Path) -> Plan:
    """Drop unavailable bridges while retaining safe direct-model fallbacks."""

    unavailable = []
    retained = []
    for bridge in plan.model_particle_bridges:
        required = (
            (bridge.source_model, compiled_model_path(bridge.source_model)),
            (bridge.template_model, compiled_model_path(bridge.template_model)),
            (bridge.source_particle, compiled_particle_path(bridge.source_particle)),
        )
        missing = [
            resource
            for resource, relative in required
            if not path_under(cache, relative).is_file()
        ]
        if missing:
            unavailable.append((bridge, missing))
        else:
            retained.append(bridge)

    if not unavailable:
        return plan

    blocked_model_targets = {
        canonical(bridge.target)
        for bridge, _missing in unavailable
        if bridge.required_for_model
    }
    blocked_particle_targets = {
        canonical(bridge.private_particle) for bridge, _missing in unavailable
    }
    mappings = [
        mapping
        for mapping in plan.mappings
        if not (
            mapping.resource_type == RESOURCE_MODEL
            and mapping.target in blocked_model_targets
        )
        and not (
            mapping.resource_type == RESOURCE_PARTICLE
            and mapping.target in blocked_particle_targets
        )
    ]
    unresolved = list(plan.unresolved)
    for bridge, missing in unavailable:
        bridge_kind = (
            "particle_body_bridge"
            if bridge.required_for_model
            else "model_particle_supplement"
        )
        unresolved.append(
            {
                "item_id": bridge.item_id,
                "hero": bridge.hero,
                "slot": bridge.slot,
                "type": bridge_kind,
                "target": bridge.target,
                "missing_sources": missing,
                "reason": (
                    "particle-bodied cosmetic preserved because a reviewed bridge "
                    "source is unavailable"
                    if bridge.required_for_model
                    else "model particle supplement skipped because a source is unavailable"
                ),
            }
        )

    stats = dict(plan.stats)
    model_mappings = [
        mapping for mapping in mappings if mapping.resource_type == RESOURCE_MODEL
    ]
    particle_mappings = [
        mapping for mapping in mappings if mapping.resource_type == RESOURCE_PARTICLE
    ]
    snapshot_mappings = [
        mapping for mapping in mappings if mapping.resource_type == RESOURCE_SNAPSHOT
    ]
    stats.update(
        {
            "resource_overrides": len(mappings),
            "model_overrides": len(model_mappings),
            "particle_overrides": len(particle_mappings),
            "particle_snapshot_overrides": len(snapshot_mappings),
            "unique_source_models": len(
                {mapping.source for mapping in model_mappings}
            ),
            "unique_source_particles": len(
                {mapping.source for mapping in particle_mappings}
            ),
            "unique_source_particle_snapshots": len(
                {mapping.source for mapping in snapshot_mappings}
            ),
            "unresolved": len(unresolved),
            "particle_body_bridges_planned": sum(
                bridge.required_for_model for bridge in retained
            ),
            "particle_body_bridges_preserved": (
                stats.get("particle_body_bridges_preserved", 0)
                + sum(bridge.required_for_model for bridge, _missing in unavailable)
            ),
            "model_particle_supplements_planned": sum(
                not bridge.required_for_model for bridge in retained
            ),
            "model_particle_supplements_skipped": (
                stats.get("model_particle_supplements_skipped", 0)
                + sum(
                    not bridge.required_for_model
                    for bridge, _missing in unavailable
                )
            ),
            "full_hero_wearable_fallbacks": max(
                0,
                stats.get("full_hero_wearable_fallbacks", 0)
                - sum(bridge.required_for_model for bridge, _missing in unavailable),
            ),
        }
    )
    for category in SUPPORTED_CATEGORIES:
        stats[f"category_{category}"] = sum(
            mapping.category == category for mapping in mappings
        )
    return replace(
        plan,
        mappings=mappings,
        unresolved=unresolved,
        stats=stats,
        model_particle_bridges=retained,
    )


def apply_missing_particle_fallbacks(
    plan: Plan,
    cache: Path,
    *,
    work_progress: Optional[WorkProgressCallback] = None,
) -> Plan:
    """Use Dota's null particle when a schema-referenced default no longer exists."""

    plan = _discard_unavailable_model_particle_bridges(plan, cache)
    neutral_compiled = compiled_particle_path(NEUTRAL_PARTICLE)
    neutral_source = path_under(cache, neutral_compiled)
    if not neutral_source.is_file():
        return plan

    adjusted: list[Mapping] = []
    fallback_count = 0
    intentional_count = 0
    unknown_count = 0
    mapping_count = len(plan.mappings)
    for index, mapping in enumerate(plan.mappings, start=1):
        source_relative = compiled_override_path(mapping.source, mapping.resource_type)
        if (
            mapping.resource_type == RESOURCE_PARTICLE
            and not path_under(cache, source_relative).is_file()
        ):
            intentional = (
                mapping.source in INTENTIONALLY_NEUTRAL_PARTICLE_DEFAULTS
                or mapping.source.startswith(INTENTIONALLY_NEUTRAL_PARTICLE_PREFIXES)
            )
            adjusted.append(
                replace(
                    mapping,
                    source=canonical(NEUTRAL_PARTICLE),
                    reason=(
                        "virtual schema particle neutralized by design"
                        if intentional
                        else "missing default particle hidden with neutral fallback"
                    ),
                )
            )
            fallback_count += 1
            if intentional:
                intentional_count += 1
            else:
                unknown_count += 1
        else:
            adjusted.append(mapping)
        if work_progress is not None:
            work_progress("validate", index, mapping_count)

    if not fallback_count:
        return plan
    stats = dict(plan.stats)
    stats["particle_missing_defaults_hidden"] = fallback_count
    stats["particle_virtual_defaults_neutralized"] = intentional_count
    stats["particle_unknown_defaults_neutralized"] = unknown_count
    return replace(
        plan,
        mappings=adjusted,
        unresolved=list(plan.unresolved),
        stats=stats,
    )


__all__ = ["apply_missing_particle_fallbacks"]
