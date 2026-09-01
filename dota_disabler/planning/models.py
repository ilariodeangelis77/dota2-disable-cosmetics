"""Model, wearable, Persona, entity, attachment, and pet candidate rules."""

from __future__ import annotations

from ..constants import (
    CATEGORY_ADDITIONAL_WEARABLES,
    CATEGORY_PERSONA_MODELS,
    CATEGORY_SPECIAL_MODELS,
    FULL_HERO_INTEGRATED_SLOTS,
    FULL_HERO_WEARABLE_ITEMS,
    INVISIBLE_MODEL,
)
from ..domain import ItemRecord
from ..resources import canonical, looks_like_model
from .context import ItemPlanningState, PendingModelOverride, PlanningContext


def _wearable_source(
    context: PlanningContext,
    state: ItemPlanningState,
    *,
    model_key: str = "model_player",
    has_bodygroup_rules: bool,
) -> tuple[str | None, bool, bool]:
    full_hero_fallback = (
        has_bodygroup_rules
        or state.item.item_id in FULL_HERO_WEARABLE_ITEMS
        or (state.hero, state.slot) in FULL_HERO_INTEGRATED_SLOTS
    ) and not state.persona_slot
    reviewed_persona_source = context.reviewed_persona_model_for(
        state.hero,
        state.slot,
        model_key,
    )
    source = (
        reviewed_persona_source or INVISIBLE_MODEL
        if state.persona_slot
        else context.hero_models.get(state.hero)
        if full_hero_fallback
        else context.default_model_for(state.default_item, model_key)
        or (INVISIBLE_MODEL if state.default_item is not None else None)
    )
    return source, full_hero_fallback, reviewed_persona_source is not None


def _record_wearable_fallback(
    context: PlanningContext,
    state: ItemPlanningState,
    source: str,
    *,
    has_bodygroup_rules: bool,
    full_hero_fallback: bool,
    reviewed_persona_fallback: bool,
) -> None:
    if reviewed_persona_fallback:
        context.increment("persona_slot_defaults_restored")
    elif has_bodygroup_rules and full_hero_fallback:
        context.increment("bodygroup_hero_fallbacks")
    elif full_hero_fallback:
        context.increment("full_hero_wearable_fallbacks")
    if source == INVISIBLE_MODEL and not state.persona_slot:
        context.increment("integrated_slot_cosmetics_hidden")


def _wearable_reason(
    state: ItemPlanningState,
    source: str,
    *,
    has_bodygroup_rules: bool,
    full_hero_fallback: bool,
    reviewed_persona_fallback: bool,
    style: bool,
) -> str:
    if reviewed_persona_fallback:
        if state.is_base:
            return "persona default wearable replaced with reviewed hero slot default"
        return (
            "persona style replaced with reviewed hero slot default"
            if style
            else "persona wearable replaced with reviewed hero slot default"
        )
    if state.persona_slot:
        return "persona style hidden" if style else "persona wearable hidden"
    if has_bodygroup_rules and full_hero_fallback:
        return (
            "bodygroup style replaced with full hero fallback"
            if style
            else "bodygroup wearable replaced with full hero fallback"
        )
    if full_hero_fallback:
        return (
            "whole-hero wearable style replaced with hero default"
            if style
            else "whole-hero wearable replaced with hero default"
        )
    if source == INVISIBLE_MODEL:
        return "integrated hero-slot cosmetic hidden"
    return "wearable style replaced with slot default" if style else "wearable replaced with slot default"


def _add_wearable_models(
    context: PlanningContext,
    state: ItemPlanningState,
) -> None:
    if not state.hero:
        return
    if state.is_base and not context.has_reviewed_persona_slot(state.hero, state.slot):
        return
    has_bodygroup_rules = any(
        visual.get("type") == "bodygroup_visibility" for visual in state.item.visuals
    )

    for key, cosmetic_model in state.item.top_models:
        source, full_hero_fallback, reviewed_persona_fallback = _wearable_source(
            context,
            state,
            model_key=key,
            has_bodygroup_rules=has_bodygroup_rules,
        )
        if source:
            _record_wearable_fallback(
                context,
                state,
                source,
                has_bodygroup_rules=has_bodygroup_rules,
                full_hero_fallback=full_hero_fallback,
                reviewed_persona_fallback=reviewed_persona_fallback,
            )
            context.remember_default_source(cosmetic_model, source)
            context.add_candidate(
                source,
                cosmetic_model,
                _wearable_reason(
                    state,
                    source,
                    has_bodygroup_rules=has_bodygroup_rules,
                    full_hero_fallback=full_hero_fallback,
                    reviewed_persona_fallback=reviewed_persona_fallback,
                    style=False,
                ),
                state.item,
                category=state.category,
                slot=state.slot,
            )
        elif state.category in context.enabled:
            context.unresolved.append(
                {
                    "item_id": state.item.item_id,
                    "hero": state.hero,
                    "slot": state.slot,
                    "type": "wearable",
                    "target": cosmetic_model,
                    "reason": "no default model could be inferred for this hero slot",
                }
            )

    for _key, cosmetic_model in state.item.nested_models:
        source, full_hero_fallback, reviewed_persona_fallback = _wearable_source(
            context,
            state,
            has_bodygroup_rules=has_bodygroup_rules,
        )
        if source:
            _record_wearable_fallback(
                context,
                state,
                source,
                has_bodygroup_rules=has_bodygroup_rules,
                full_hero_fallback=full_hero_fallback,
                reviewed_persona_fallback=reviewed_persona_fallback,
            )
            context.remember_default_source(cosmetic_model, source)
            context.add_candidate(
                source,
                cosmetic_model,
                _wearable_reason(
                    state,
                    source,
                    has_bodygroup_rules=has_bodygroup_rules,
                    full_hero_fallback=full_hero_fallback,
                    reviewed_persona_fallback=reviewed_persona_fallback,
                    style=True,
                ),
                state.item,
                category=state.category,
                slot=state.slot,
            )
        elif state.category in context.enabled:
            context.unresolved.append(
                {
                    "item_id": state.item.item_id,
                    "hero": state.hero,
                    "slot": state.slot,
                    "type": "wearable_style",
                    "target": cosmetic_model,
                    "reason": "no default model could be inferred for this hero slot",
                }
            )


def _add_entity_or_refit(
    context: PlanningContext,
    state: ItemPlanningState,
    modifier_type: str,
    asset: str,
    modifier: str,
) -> bool:
    if modifier_type in ("entity_model", "base_model", "entity_clientside_model"):
        default_entity_model = context.default_entity_model_for(asset)
        if default_entity_model and looks_like_model(modifier):
            context.add_candidate(
                default_entity_model,
                modifier,
                f"{modifier_type} override replaced with entity default",
                state.item,
                category=state.category,
                hero=asset,
                slot=state.slot,
            )
            if state.category in context.enabled:
                context.increment("entity_default_replacements")
        return True
    if modifier_type == "hero_model_change":
        context.add_candidate(
            context.hero_models.get(asset, asset),
            modifier,
            "alternate hero model replaced with default",
            state.item,
            category=state.category,
            slot=state.slot,
        )
        return True
    if modifier_type != "model" or not (
        looks_like_model(asset) and looks_like_model(modifier)
    ):
        return False
    if canonical(modifier) == canonical(INVISIBLE_MODEL) and state.category in context.enabled:
        context.unresolved.append(
            {
                "item_id": state.item.item_id,
                "hero": state.hero,
                "slot": state.slot,
                "type": modifier_type,
                "asset": asset,
                "modifier": modifier,
                "reason": "the shared invisible target is never overwritten",
            }
        )
    elif state.category in context.enabled:
        context.pending_model_overrides.append(
            PendingModelOverride(
                item=state.item,
                asset=canonical(asset),
                modifier=canonical(modifier),
                category=state.category,
                slot=state.slot,
            )
        )
    return True


def _add_attachment_or_pet(
    context: PlanningContext,
    state: ItemPlanningState,
    modifier_type: str,
    visual: dict[str, str],
    additional_index: int,
) -> tuple[bool, int]:
    asset = visual.get("asset", "")
    if modifier_type == "additional_wearable" and looks_like_model(asset):
        if not state.hero:
            return True, additional_index
        defaults_for_slot = context.default_additional.get((state.hero, state.slot), [])
        source = (
            defaults_for_slot[additional_index]
            if additional_index < len(defaults_for_slot)
            else INVISIBLE_MODEL
        )
        context.add_candidate(
            source,
            asset,
            "additional wearable replaced or hidden",
            state.item,
            category=(
                state.category
                if state.category in {CATEGORY_PERSONA_MODELS, CATEGORY_SPECIAL_MODELS}
                else CATEGORY_ADDITIONAL_WEARABLES
            ),
            slot=state.slot,
        )
        return True, additional_index + 1
    if modifier_type != "pet":
        return False, additional_index
    for field in ("asset", "pickup_item"):
        pet_model = visual.get(field, "")
        if not looks_like_model(pet_model):
            continue
        context.add_candidate(
            INVISIBLE_MODEL,
            pet_model,
            "cosmetic pet model hidden",
            state.item,
            category=state.category,
            slot=state.slot,
            neutralize_model_skin=False,
        )
        if state.category in context.enabled:
            context.increment("pet_models_hidden")
    return True, additional_index


def process_item_models(
    context: PlanningContext,
    item: ItemRecord,
) -> ItemPlanningState:
    """Collect every non-particle candidate owned by one economy item."""

    state = context.state_for(item)
    _add_wearable_models(context, state)
    if state.is_base:
        return state

    additional_index = 0
    for visual in item.visuals:
        modifier_type = visual.get("type", "")
        asset = visual.get("asset", "")
        modifier = visual.get("modifier", "")
        if _add_entity_or_refit(context, state, modifier_type, asset, modifier):
            continue
        _handled, additional_index = _add_attachment_or_pet(
            context,
            state,
            modifier_type,
            visual,
            additional_index,
        )
    return state


__all__ = ["process_item_models"]
