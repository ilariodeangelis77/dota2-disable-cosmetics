"""Restore default hero parts that cosmetic items hide with bodygroup rules.

A ``bodygroup_visibility`` rule selects a non-default bodygroup choice on a
hero model, which normally has no meshes.  Dota keeps applying that rule after
the cosmetic model is replaced, so the default hero part would stay hidden.

Instead of covering the hole with a second copy of the whole hero (which also
drops the slot's own default wearable, such as Oracle's armor or hood), the
generated archive carries a copy of the affected hero model whose hidden
choices render the default meshes.  The cosmetic slot can then use its normal
default wearable.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Optional

from ..constants import (
    CATEGORY_STANDARD_WEARABLES,
    RESOURCE_MODEL,
)
from ..domain import ItemRecord, Mapping
from ..resources import canonical, looks_like_model

if TYPE_CHECKING:
    from .context import PlanningContext


BODYGROUP_RESTORE_REASON = "hidden default hero bodygroups restored"
_ENTITY_MODEL_TYPES = frozenset(
    {"entity_model", "base_model", "entity_clientside_model"}
)


@dataclass(frozen=True)
class BodygroupRestoration:
    """One compiled model whose listed bodygroups must render their default choice."""

    target: str
    source: str
    bodygroups: tuple[str, ...]
    category: str
    hero: Optional[str]
    item_ids: tuple[str, ...]


@dataclass(frozen=True)
class BodygroupRestorationPlan:
    restorations: dict[str, BodygroupRestoration]
    restored_item_ids: frozenset[str]


def hidden_bodygroup_rules(item: ItemRecord) -> list[tuple[str, str]]:
    """Return ``(model, bodygroup)`` pairs an item switches away from the default."""

    rules: list[tuple[str, str]] = []
    for visual in item.visuals:
        if visual.get("type") != "bodygroup_visibility":
            continue
        asset = visual.get("asset", "")
        bodygroup = visual.get("modifier", "").strip()
        try:
            choice = int(visual.get("value", "").strip())
        except ValueError:
            continue
        if choice > 0 and bodygroup and looks_like_model(asset):
            rules.append((canonical(asset), bodygroup))
    return rules


def _alternate_hero_model_sources(
    context: "PlanningContext",
    hero_default_models: set[str],
) -> dict[str, str]:
    """Map alternate hero models (Arcana bases) to the default model replacing them."""

    candidates: dict[str, set[str]] = {}
    for item in context.items.values():
        if context.is_retired_item(item):
            continue
        state = context.state_for(item)
        if state.is_base or state.category not in context.enabled:
            continue
        for visual in item.visuals:
            modifier_type = visual.get("type", "")
            asset = visual.get("asset", "")
            modifier = visual.get("modifier", "")
            if not looks_like_model(modifier):
                continue
            if modifier_type in _ENTITY_MODEL_TYPES:
                default = context.default_entity_model_for(asset)
            elif modifier_type == "hero_model_change":
                default = context.hero_models.get(asset)
            else:
                continue
            if default and canonical(default) in hero_default_models:
                candidates.setdefault(canonical(modifier), set()).add(canonical(default))
    return {
        target: next(iter(sources))
        for target, sources in candidates.items()
        if len(sources) == 1 and target not in hero_default_models
    }


def plan_hidden_bodygroup_restorations(
    context: "PlanningContext",
) -> BodygroupRestorationPlan:
    """Decide which hidden bodygroups can be restored without exposing a cosmetic.

    A bodygroup is restored only when every item hiding it is replaced in this
    build. Otherwise the default part would render underneath a cosmetic that is
    still visible. A default (base) item that hides a part is respected.
    """

    hero_default_models = {canonical(model) for model in context.hero_models.values()}
    alternate_sources = _alternate_hero_model_sources(context, hero_default_models)

    hiders: dict[tuple[str, str], list[ItemRecord]] = {}
    item_rules: dict[str, list[tuple[str, str]]] = {}
    for item in context.items.values():
        if context.is_retired_item(item):
            continue
        rules = hidden_bodygroup_rules(item)
        if not rules:
            continue
        item_rules[item.item_id] = rules
        for rule in rules:
            hiders.setdefault(rule, []).append(item)

    restorable: dict[tuple[str, str], list[ItemRecord]] = {}
    for (model, bodygroup), owners in hiders.items():
        if model not in hero_default_models and model not in alternate_sources:
            continue
        # A default item hiding part of the default hero model is the default
        # look. An alternate model is already replaced by the default hero, so
        # hiding its parts is never part of that look.
        default_model = model in hero_default_models
        states = [context.state_for(owner) for owner in owners]
        if all(
            state.hero
            and (state.is_base or state.category in context.enabled)
            and not (default_model and state.is_base)
            for state in states
        ):
            restorable[(model, bodygroup)] = owners

    restored_item_ids = frozenset(
        item_id
        for item_id, rules in item_rules.items()
        if all(rule in restorable for rule in rules)
    )

    grouped: dict[str, list[tuple[str, list[ItemRecord]]]] = {}
    for (model, bodygroup), owners in restorable.items():
        grouped.setdefault(model, []).append((bodygroup, owners))
    restorations: dict[str, BodygroupRestoration] = {}
    for model, entries in sorted(grouped.items()):
        owners = sorted(
            {owner.item_id: owner for _bodygroup, items in entries for owner in items}.values(),
            key=lambda owner: int(owner.item_id) if owner.item_id.isdigit() else 0,
        )
        categories = {context.state_for(owner).category for owner in owners}
        heroes = sorted({owner.hero for owner in owners if owner.hero})
        restorations[model] = BodygroupRestoration(
            target=model,
            source=alternate_sources.get(model, model),
            bodygroups=tuple(
                sorted({bodygroup for bodygroup, _items in entries}, key=str.casefold)
            ),
            category=(
                CATEGORY_STANDARD_WEARABLES
                if CATEGORY_STANDARD_WEARABLES in categories
                else sorted(categories)[0]
            ),
            hero=heroes[0] if len(heroes) == 1 else None,
            item_ids=tuple(owner.item_id for owner in owners),
        )
    return BodygroupRestorationPlan(
        restorations=restorations,
        restored_item_ids=restored_item_ids,
    )


def apply_hidden_bodygroup_restorations(
    context: "PlanningContext",
    mappings: list[Mapping],
) -> list[Mapping]:
    """Attach planned bodygroup restorations to the resolved model mappings."""

    by_target = {mapping.target: mapping for mapping in mappings}
    for target, restoration in context.bodygroup_restorations.restorations.items():
        existing = by_target.get(target)
        if existing is not None:
            if (
                existing.resource_type != RESOURCE_MODEL
                or canonical(existing.source) != restoration.source
            ):
                context.unresolved.append(
                    {
                        "type": "bodygroup_restoration",
                        "target": target,
                        "kept_source": existing.source,
                        "expected_source": restoration.source,
                        "bodygroups": list(restoration.bodygroups),
                        "item_ids": list(restoration.item_ids),
                        "reason": "another replacement owns the model whose bodygroups were hidden",
                    }
                )
                continue
            merged = tuple(
                sorted(
                    {*existing.restore_bodygroups, *restoration.bodygroups},
                    key=str.casefold,
                )
            )
            by_target[target] = replace(existing, restore_bodygroups=merged)
        elif restoration.source == target:
            by_target[target] = Mapping(
                source=restoration.source,
                target=target,
                reason=BODYGROUP_RESTORE_REASON,
                category=restoration.category,
                resource_type=RESOURCE_MODEL,
                hero=restoration.hero,
                restore_bodygroups=restoration.bodygroups,
            )
        else:
            context.unresolved.append(
                {
                    "type": "bodygroup_restoration",
                    "target": target,
                    "expected_source": restoration.source,
                    "bodygroups": list(restoration.bodygroups),
                    "item_ids": list(restoration.item_ids),
                    "reason": "the alternate hero model was not replaced by its default",
                }
            )
            continue
        context.increment("hidden_bodygroup_models_restored")
    return sorted(
        by_target.values(),
        key=lambda mapping: (mapping.resource_type, mapping.target, mapping.source),
    )


__all__ = [
    "BODYGROUP_RESTORE_REASON",
    "BodygroupRestoration",
    "BodygroupRestorationPlan",
    "apply_hidden_bodygroup_restorations",
    "hidden_bodygroup_rules",
    "plan_hidden_bodygroup_restorations",
]
