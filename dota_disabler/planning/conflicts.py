"""Pending refit inference and deterministic candidate conflict resolution."""

from __future__ import annotations

from dataclasses import replace

from ..constants import MODEL_ASSET_DEFAULT_EXCEPTIONS, RESOURCE_PARTICLE
from ..domain import Mapping
from .context import PlanningContext


PRIORITY = {
    "entity_model override replaced with entity default": 100,
    "base_model override replaced with entity default": 100,
    "entity_clientside_model override replaced with entity default": 100,
    "model asset override replaced with inferred default": 95,
    "model asset override replaced with original": 95,
    "alternate hero model replaced with default": 90,
    "persona wearable hidden": 85,
    "persona style hidden": 85,
    "persona default wearable replaced with reviewed hero slot default": 87,
    "persona wearable replaced with reviewed hero slot default": 87,
    "persona style replaced with reviewed hero slot default": 87,
    "wearable replaced with slot default": 80,
    "wearable style replaced with slot default": 80,
    "integrated hero-slot cosmetic hidden": 80,
    "additional wearable replaced or hidden": 70,
    "cosmetic pet model hidden": 88,
    "bodygroup wearable replaced with full hero fallback": 86,
    "bodygroup style replaced with full hero fallback": 86,
    "whole-hero wearable replaced with hero default": 86,
    "whole-hero wearable style replaced with hero default": 86,
    "global particle override replaced with schema default": 110,
    "particle override replaced with schema default": 100,
    "particle snapshot replaced with schema default": 100,
    "combined particle override replaced with schema default": 75,
    "cosmetic-only particle hidden": 60,
    "cosmetic particle restored from suppressed default": 100,
    "cosmetic particle restored from slot default": 100,
}


def resolve_pending_model_overrides(context: PlanningContext) -> None:
    """Resolve model-to-model refits after wearable defaults are known."""

    for pending in context.pending_model_overrides:
        inferred_sources = context.default_sources_by_target.get(pending.asset, set())
        if len(inferred_sources) == 1:
            source = next(iter(inferred_sources))
            context.increment("model_asset_defaults_inferred")
        else:
            source = MODEL_ASSET_DEFAULT_EXCEPTIONS.get(
                (pending.item.item_id, pending.asset),
                pending.asset,
            )
            if source != pending.asset:
                context.increment("model_asset_reviewed_exceptions")
            else:
                context.increment("model_asset_original_fallbacks")
        context.add_candidate(
            source,
            pending.modifier,
            (
                "model asset override replaced with inferred default"
                if source != pending.asset
                else "model asset override replaced with original"
            ),
            pending.item,
            category=pending.category,
            slot=pending.slot,
        )


def _ordered_candidates(candidates: list[Mapping]) -> list[Mapping]:
    return sorted(
        candidates,
        key=lambda mapping: (
            mapping.resource_type,
            mapping.target,
            -PRIORITY.get(mapping.reason, 10),
            mapping.source,
            mapping.item_id or "",
            mapping.category,
            mapping.reason,
        ),
    )


def _merge_candidate(
    context: PlanningContext,
    chosen: dict[str, Mapping],
    mapping: Mapping,
) -> None:
    existing = chosen.get(mapping.target)
    if existing is None:
        chosen[mapping.target] = mapping
        return
    if existing.source == mapping.source:
        if (
            mapping.neutralize_model_skin and not existing.neutralize_model_skin
        ) or (
            mapping.required_material_groups > existing.required_material_groups
        ) or (
            mapping.neutralize_bodygroup and not existing.neutralize_bodygroup
        ):
            chosen[mapping.target] = replace(
                existing,
                neutralize_model_skin=(
                    existing.neutralize_model_skin or mapping.neutralize_model_skin
                ),
                required_material_groups=max(
                    existing.required_material_groups,
                    mapping.required_material_groups,
                ),
                neutralize_bodygroup=(
                    existing.neutralize_bodygroup or mapping.neutralize_bodygroup
                ),
            )
        return

    context.increment("mapping_conflicts")
    if PRIORITY.get(mapping.reason, 10) > PRIORITY.get(existing.reason, 10):
        chosen[mapping.target] = mapping
    if existing.neutralize_model_skin or mapping.neutralize_model_skin:
        chosen[mapping.target] = replace(
            chosen[mapping.target],
            neutralize_model_skin=True,
            required_material_groups=max(
                existing.required_material_groups,
                mapping.required_material_groups,
            ),
        )
    if existing.neutralize_bodygroup or mapping.neutralize_bodygroup:
        chosen[mapping.target] = replace(
            chosen[mapping.target],
            neutralize_bodygroup=True,
        )
    kept = chosen[mapping.target]
    context.unresolved.append(
        {
            "type": "mapping_conflict",
            "target": mapping.target,
            "kept_source": kept.source,
            "candidate_sources": sorted({existing.source, mapping.source}),
            "reason": "multiple schema rules map the same target differently",
        }
    )


def _resolve_particle_routes(
    context: PlanningContext,
    chosen: dict[str, Mapping],
) -> None:
    cyclic_particle_targets: set[str] = set()
    particle_routes = dict(chosen)
    for target, mapping in particle_routes.items():
        if mapping.resource_type != RESOURCE_PARTICLE:
            continue
        source = mapping.source
        seen = {target}
        while (
            source in particle_routes
            and particle_routes[source].resource_type == RESOURCE_PARTICLE
        ):
            if source in seen:
                context.increment("particle_resolution_cycles")
                cyclic_particle_targets.add(target)
                context.unresolved.append(
                    {
                        "type": "particle_mapping_cycle",
                        "target": target,
                        "reason": "particle replacement chain contains a cycle and was skipped",
                    }
                )
                break
            seen.add(source)
            source = particle_routes[source].source
        else:
            if source != mapping.source:
                chosen[target] = replace(mapping, source=source)
                context.increment("particle_defaults_resolved_transitively")
    for target in cyclic_particle_targets:
        chosen.pop(target, None)


def resolve_candidates(context: PlanningContext) -> list[Mapping]:
    """Choose one mapping per target and resolve transitive particle routes."""

    chosen: dict[str, Mapping] = {}
    for mapping in _ordered_candidates(context.candidates):
        _merge_candidate(context, chosen, mapping)
    _resolve_particle_routes(context, chosen)
    return sorted(
        chosen.values(),
        key=lambda mapping: (mapping.resource_type, mapping.target, mapping.source),
    )


__all__ = ["PRIORITY", "resolve_candidates", "resolve_pending_model_overrides"]
