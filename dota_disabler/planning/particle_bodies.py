"""Reviewed bridges for heroes whose readable body is a particle effect."""

from __future__ import annotations

from dataclasses import dataclass

from ..constants import (
    CATEGORY_PARTICLE_EFFECTS,
    CATEGORY_STANDARD_WEARABLES,
    NEUTRAL_PARTICLE,
    PARTICLE_REPLACEMENT_TYPES,
    RESOURCE_PARTICLE,
)
from ..domain import ModelParticleBridge
from ..resources import canonical, looks_like_model, looks_like_particle
from .context import ItemPlanningState, PlanningContext


@dataclass(frozen=True)
class ParticleBodyProfile:
    item_id: str
    hero: str
    slot: str
    model_target: str
    default_particle_slot: str
    source_particle: str
    template_particle: str
    private_particle: str
    suppressed_particles: tuple[str, ...]


MADAME_SCRIO = ParticleBodyProfile(
    item_id="34398",
    hero="npc_dota_hero_wisp",
    slot="head",
    model_target="models/items/io/dark_carnival_io/dark_carnival_io.vmdl",
    default_particle_slot="ambient_effects",
    source_particle="particles/units/heroes/hero_wisp/wisp_ambient.vpcf",
    template_particle=(
        "particles/econ/items/wisp/io_carnival/io_carnival_ambient.vpcf"
    ),
    private_particle=(
        "particles/dota2_cosmetic_disabler/heroes/wisp/"
        "wisp_ambient_34398.vpcf"
    ),
    suppressed_particles=(
        "particles/units/heroes/hero_wisp/wisp_ambient.vpcf",
        "particles/units/heroes/hero_wisp/wisp_ambient_entity_tentacles.vpcf",
    ),
)


PARTICLE_BODY_PROFILES = {MADAME_SCRIO.item_id: MADAME_SCRIO}


def _profile_error(
    context: PlanningContext,
    state: ItemPlanningState,
    profile: ParticleBodyProfile,
) -> str:
    if state.hero != profile.hero:
        return "reviewed particle-body item belongs to a different hero"
    if state.slot != profile.slot:
        return "reviewed particle-body item moved to a different slot"

    current_targets = {
        canonical(model)
        for _key, model in (*state.item.top_models, *state.item.nested_models)
        if looks_like_model(model)
    }
    if canonical(profile.model_target) not in current_targets:
        return "reviewed particle-body model is absent from the current item"

    source_model = context.hero_models.get(profile.hero)
    if not source_model or not looks_like_model(source_model):
        return "reviewed particle-body hero has no current base model"

    default_particles = context.default_created_particles.get(
        (profile.hero, profile.default_particle_slot),
        [],
    )
    if canonical(profile.source_particle) not in default_particles:
        return "reviewed particle-body ambient is absent from the current default item"

    suppressed = {
        canonical(visual.get("asset", ""))
        for visual in state.item.visuals
        if visual.get("type") in PARTICLE_REPLACEMENT_TYPES
        and canonical(visual.get("modifier", "")) == canonical(NEUTRAL_PARTICLE)
        and looks_like_particle(visual.get("asset", ""))
    }
    if not all(canonical(particle) in suppressed for particle in profile.suppressed_particles):
        return "reviewed particle-body suppression rules changed in the current schema"
    return ""


def process_particle_body(
    context: PlanningContext,
    state: ItemPlanningState,
) -> bool:
    """Plan an atomic model/particle bridge, or conservatively preserve the item."""

    profile = PARTICLE_BODY_PROFILES.get(state.item.item_id)
    if profile is None:
        return False

    if CATEGORY_STANDARD_WEARABLES not in context.enabled:
        return True
    if CATEGORY_PARTICLE_EFFECTS not in context.enabled:
        context.increment("particle_body_bridges_preserved")
        context.unresolved.append(
            {
                "item_id": state.item.item_id,
                "hero": state.hero,
                "slot": state.slot,
                "type": "particle_body_bridge",
                "target": canonical(profile.model_target),
                "reason": (
                    "particle-bodied cosmetic preserved because particle effects are disabled"
                ),
            }
        )
        return True

    error = _profile_error(context, state, profile)
    if error:
        context.increment("particle_body_bridges_preserved")
        context.unresolved.append(
            {
                "item_id": state.item.item_id,
                "hero": state.hero,
                "slot": state.slot,
                "type": "particle_body_bridge",
                "target": canonical(profile.model_target),
                "reason": error,
            }
        )
        return True

    source_model = canonical(context.hero_models[profile.hero])
    target_model = canonical(profile.model_target)
    source_particle = canonical(profile.source_particle)
    private_particle = canonical(profile.private_particle)
    template_particle = canonical(profile.template_particle)
    reason = "particle-bodied hero wearable restored with reviewed ambient bridge"

    context.add_candidate(
        source_model,
        target_model,
        reason,
        state.item,
        category=CATEGORY_STANDARD_WEARABLES,
        slot=state.slot,
    )
    context.add_candidate(
        source_particle,
        private_particle,
        "reviewed particle-body ambient copied to a private model-owned path",
        state.item,
        category=CATEGORY_PARTICLE_EFFECTS,
        resource_type=RESOURCE_PARTICLE,
        slot=state.slot,
    )
    context.model_particle_bridges.append(
        ModelParticleBridge(
            source_model=source_model,
            template_model=target_model,
            target=target_model,
            source_particle=source_particle,
            private_particle=private_particle,
            template_particle=template_particle,
            reason=reason,
            category=CATEGORY_STANDARD_WEARABLES,
            item_id=state.item.item_id,
            hero=profile.hero,
            slot=profile.slot,
        )
    )
    context.increment("particle_body_bridges_planned")
    context.increment("full_hero_wearable_fallbacks")
    return True


__all__ = [
    "MADAME_SCRIO",
    "PARTICLE_BODY_PROFILES",
    "ParticleBodyProfile",
    "process_particle_body",
]
