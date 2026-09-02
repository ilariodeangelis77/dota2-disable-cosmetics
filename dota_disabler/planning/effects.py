"""Item and global particle-effect candidate rules."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Optional

from ..constants import (
    CATEGORY_PARTICLE_EFFECTS,
    NEUTRAL_PARTICLE,
    PARTICLE_REPLACEMENT_TYPES,
    RESOURCE_PARTICLE,
    RESOURCE_SNAPSHOT,
)
from ..resources import (
    canonical,
    is_cosmetic_additive_particle,
    looks_like_particle,
    looks_like_particle_snapshot,
)
from .context import ItemPlanningState, PlanningContext


def _best_suppressed_particle(
    target: str,
    suppressed_default_particles: list[str],
) -> Optional[str]:
    target_tokens = set(re.findall(r"[a-z0-9]+", PurePosixPath(target).stem.casefold()))
    ranked: list[tuple[int, int, str]] = []
    for source in suppressed_default_particles:
        source_tokens = set(
            re.findall(r"[a-z0-9]+", PurePosixPath(source).stem.casefold())
        )
        shared = target_tokens.intersection(source_tokens)
        ranked.append((len(shared), sum(len(token) for token in shared), source))
    ranked.sort(reverse=True)
    if not ranked or ranked[0][0] == 0:
        return None
    if len(ranked) > 1 and ranked[0][:2] == ranked[1][:2]:
        return None
    return ranked[0][2]


def _add_schema_replacement(
    context: PlanningContext,
    state: ItemPlanningState,
    modifier_type: str,
    asset: str,
    modifier: str,
) -> None:
    if looks_like_particle(asset) and looks_like_particle(modifier):
        context.add_candidate(
            asset,
            modifier,
            (
                "combined particle override replaced with schema default"
                if modifier_type == "particle_combined"
                else "particle override replaced with schema default"
            ),
            state.item,
            category=CATEGORY_PARTICLE_EFFECTS,
            resource_type=RESOURCE_PARTICLE,
            slot=state.slot,
        )
        context.increment("particle_default_replacements")
    elif not asset and looks_like_particle(modifier):
        context.add_candidate(
            NEUTRAL_PARTICLE,
            modifier,
            "cosmetic-only particle hidden",
            state.item,
            category=CATEGORY_PARTICLE_EFFECTS,
            resource_type=RESOURCE_PARTICLE,
            slot=state.slot,
        )
        context.increment("particle_additive_hidden")
    else:
        context.increment("particle_rules_unsupported")


def _add_created_particle(
    context: PlanningContext,
    state: ItemPlanningState,
    target: str,
    default_created_particle: Optional[str],
    suppressed_default_particles: list[str],
) -> None:
    if target and canonical(target) in context.protected_effect_resources:
        context.increment("particle_default_creates_preserved")
        return
    if target and is_cosmetic_additive_particle(target):
        suppressed_default = _best_suppressed_particle(
            target,
            suppressed_default_particles,
        )
        restored_default = suppressed_default or default_created_particle
        context.add_candidate(
            restored_default or NEUTRAL_PARTICLE,
            target,
            (
                "cosmetic particle restored from suppressed default"
                if suppressed_default
                else "cosmetic particle restored from slot default"
                if default_created_particle
                else "cosmetic-only particle hidden"
            ),
            state.item,
            category=CATEGORY_PARTICLE_EFFECTS,
            resource_type=RESOURCE_PARTICLE,
            slot=state.slot,
        )
        if suppressed_default:
            context.increment("particle_suppressed_defaults_restored")
        elif default_created_particle:
            context.increment("particle_slot_defaults_restored")
        context.increment("particle_additive_hidden")
    elif target:
        context.increment("particle_additive_shared_paths_skipped")
    else:
        context.increment("particle_rules_unsupported")


def process_item_effects(
    context: PlanningContext,
    state: ItemPlanningState,
) -> None:
    """Collect supported particle and snapshot rules for one item."""

    if CATEGORY_PARTICLE_EFFECTS not in context.enabled or (
        state.is_base
        and not context.has_reviewed_persona_base_particle_slot(
            state.hero,
            state.slot,
        )
    ):
        return

    defaults_for_created_particles = (
        context.default_created_particles.get((state.hero, state.slot), [])
        if state.hero and state.slot
        else []
    )
    suppressed_default_particles = [
        canonical(visual.get("asset", ""))
        for visual in state.item.visuals
        if visual.get("type") in PARTICLE_REPLACEMENT_TYPES
        and looks_like_particle(visual.get("asset", ""))
        and canonical(visual.get("modifier", "")) == canonical(NEUTRAL_PARTICLE)
    ]

    particle_create_index = 0
    for visual in state.item.visuals:
        modifier_type = visual.get("type", "")
        asset = visual.get("asset", "")
        modifier = visual.get("modifier", "")

        if modifier_type in PARTICLE_REPLACEMENT_TYPES:
            _add_schema_replacement(context, state, modifier_type, asset, modifier)
        elif modifier_type == "particle_create":
            target = (
                modifier
                if looks_like_particle(modifier)
                else asset
                if looks_like_particle(asset)
                else ""
            )
            default_created_particle = (
                defaults_for_created_particles[particle_create_index]
                if particle_create_index < len(defaults_for_created_particles)
                else None
            )
            particle_create_index += 1
            _add_created_particle(
                context,
                state,
                target,
                default_created_particle,
                suppressed_default_particles,
            )
        elif modifier_type == "particle_snapshot":
            if looks_like_particle_snapshot(asset) and looks_like_particle_snapshot(modifier):
                context.add_candidate(
                    asset,
                    modifier,
                    "particle snapshot replaced with schema default",
                    state.item,
                    category=CATEGORY_PARTICLE_EFFECTS,
                    resource_type=RESOURCE_SNAPSHOT,
                    slot=state.slot,
                )
                context.increment("particle_snapshot_replacements")
            else:
                context.increment("particle_rules_unsupported")
        elif "particle" in modifier_type:
            context.increment("particle_rules_unsupported")


def process_global_effects(context: PlanningContext) -> None:
    for visual in context.global_visuals:
        modifier_type = visual.get("type", "")
        asset = visual.get("asset", "")
        modifier = visual.get("modifier", "")
        if (
            modifier_type in PARTICLE_REPLACEMENT_TYPES
            and looks_like_particle(asset)
            and looks_like_particle(modifier)
        ):
            context.add_candidate(
                asset,
                modifier,
                "global particle override replaced with schema default",
                category=CATEGORY_PARTICLE_EFFECTS,
                resource_type=RESOURCE_PARTICLE,
            )
            if CATEGORY_PARTICLE_EFFECTS in context.enabled:
                context.increment("global_particle_mappings")


__all__ = ["process_global_effects", "process_item_effects"]
