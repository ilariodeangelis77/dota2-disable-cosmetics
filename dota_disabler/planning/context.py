"""Shared state and candidate collection for the planning pipeline."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Optional

from ..constants import (
    CATEGORY_PARTICLE_EFFECTS,
    CATEGORY_PERSONA_MODELS,
    CATEGORY_SPECIAL_MODELS,
    CATEGORY_STANDARD_WEARABLES,
    DEFAULT_CATEGORIES,
    INVISIBLE_MODEL,
    NEUTRAL_PARTICLE,
    PARTICLE_DEFAULT_PATH_EXCEPTIONS,
    RESOURCE_MATERIAL,
    RESOURCE_MODEL,
    RESOURCE_PARTICLE,
    RESOURCE_SNAPSHOT,
    RETIRED_ITEM_NAME_MARKERS,
    SUPPORTED_CATEGORIES,
)
from ..domain import ItemRecord, Mapping
from ..resources import (
    canonical,
    looks_like_material,
    looks_like_model,
    looks_like_particle,
    looks_like_particle_snapshot,
)
from ..schema import item_attr
from .personas import PERSONA_PROFILES


COUNTER_NAMES = (
    "particle_default_replacements",
    "particle_additive_hidden",
    "particle_default_creates_preserved",
    "particle_additive_shared_paths_skipped",
    "particle_snapshot_replacements",
    "particle_protected_targets_skipped",
    "particle_rules_unsupported",
    "global_particle_mappings",
    "particle_suppressed_defaults_restored",
    "particle_slot_defaults_restored",
    "integrated_slot_cosmetics_hidden",
    "bodygroup_sensitive_models_skipped",
    "alternate_skin_models_skipped",
    "entity_default_replacements",
    "particle_reviewed_default_fallbacks",
    "bodygroup_hero_fallbacks",
    "full_hero_wearable_fallbacks",
    "persona_slot_defaults_restored",
    "persona_profiles_validated",
    "persona_profile_slots_resolved",
    "persona_profile_slots_unresolved",
    "pet_models_hidden",
    "retired_items_skipped",
    "model_asset_defaults_inferred",
    "model_asset_reviewed_exceptions",
    "model_asset_original_fallbacks",
    "mapping_conflicts",
    "particle_defaults_resolved_transitively",
    "particle_resolution_cycles",
)


@dataclass(frozen=True)
class PendingModelOverride:
    item: ItemRecord
    asset: str
    modifier: str
    category: str
    slot: str


@dataclass(frozen=True)
class ItemPlanningState:
    item: ItemRecord
    hero: Optional[str]
    slot: str
    is_base: bool
    default_item: Optional[ItemRecord]
    persona_slot: bool
    category: str


@dataclass
class PlanningContext:
    prefabs: dict[str, dict[str, str]]
    items: dict[str, ItemRecord]
    hero_models: dict[str, str]
    global_visuals: list[dict[str, str]]
    enabled: set[str]
    defaults: dict[tuple[str, str], ItemRecord]
    skin_sensitive_item_ids: set[str]
    entity_defaults: dict[str, str]
    default_additional: dict[tuple[str, str], list[str]]
    default_created_particles: dict[tuple[str, str], list[str]]
    protected_effect_resources: set[str]
    candidates: list[Mapping] = field(default_factory=list)
    unresolved: list[dict] = field(default_factory=list)
    default_sources_by_target: dict[str, set[str]] = field(default_factory=dict)
    pending_model_overrides: list[PendingModelOverride] = field(default_factory=list)
    counters: dict[str, int] = field(
        default_factory=lambda: {name: 0 for name in COUNTER_NAMES}
    )

    @classmethod
    def create(
        cls,
        prefabs: dict[str, dict[str, str]],
        items: dict[str, ItemRecord],
        hero_models: dict[str, str],
        global_visuals: list[dict[str, str]],
        enabled_categories: Optional[Iterable[str]] = None,
        unit_models: Optional[dict[str, str]] = None,
    ) -> "PlanningContext":
        enabled = set(
            DEFAULT_CATEGORIES if enabled_categories is None else enabled_categories
        )
        unknown_categories = enabled.difference(SUPPORTED_CATEGORIES)
        if unknown_categories:
            raise ValueError(
                f"Unknown replacement categories: {', '.join(sorted(unknown_categories))}"
            )

        defaults: dict[tuple[str, str], ItemRecord] = {}
        for item in items.values():
            slot = item_attr(item, prefabs, "item_slot")
            if item_attr(item, prefabs, "baseitem") == "1" and item.hero and slot:
                defaults.setdefault((item.hero, slot), item)

        skin_sensitive_item_ids = {
            item.item_id for item in items.values() if item.has_nondefault_skin
        }

        entity_defaults = dict(unit_models or {})
        entity_defaults.update(hero_models)
        for item in items.values():
            slot = item_attr(item, prefabs, "item_slot")
            if item_attr(item, prefabs, "baseitem") != "1" or "persona" in slot.casefold():
                continue
            for visual in item.visuals:
                if visual.get("type") not in {
                    "entity_model",
                    "base_model",
                    "entity_clientside_model",
                }:
                    continue
                asset = visual.get("asset", "")
                modifier = visual.get("modifier", "")
                if asset and looks_like_model(modifier):
                    entity_defaults[asset] = canonical(modifier)

        default_additional: dict[tuple[str, str], list[str]] = {}
        default_created_particles: dict[tuple[str, str], list[str]] = {}
        for hero_slot, default_item in defaults.items():
            default_additional[hero_slot] = [
                canonical(visual["asset"])
                for visual in default_item.visuals
                if visual.get("type") == "additional_wearable"
                and looks_like_model(visual.get("asset", ""))
            ]
            default_created_particles[hero_slot] = [
                canonical(target)
                for visual in default_item.visuals
                if visual.get("type") == "particle_create"
                for target in (visual.get("modifier", "") or visual.get("asset", ""),)
                if looks_like_particle(target)
            ]

        protected_effect_resources = {canonical(NEUTRAL_PARTICLE)}
        for item in items.values():
            if item_attr(item, prefabs, "baseitem") != "1":
                continue
            for visual in item.visuals:
                for resource in (visual.get("asset", ""), visual.get("modifier", "")):
                    if looks_like_particle(resource) or looks_like_particle_snapshot(resource):
                        protected_effect_resources.add(canonical(resource))

        context = cls(
            prefabs=prefabs,
            items=items,
            hero_models=hero_models,
            global_visuals=global_visuals,
            enabled=enabled,
            defaults=defaults,
            skin_sensitive_item_ids=skin_sensitive_item_ids,
            entity_defaults=entity_defaults,
            default_additional=default_additional,
            default_created_particles=default_created_particles,
            protected_effect_resources=protected_effect_resources,
        )
        context.validate_persona_profiles()
        return context

    def increment(self, name: str, amount: int = 1) -> None:
        self.counters[name] += amount

    def is_retired_item(self, item: ItemRecord) -> bool:
        folded = item.name.casefold()
        return any(marker in folded for marker in RETIRED_ITEM_NAME_MARKERS)

    def state_for(self, item: ItemRecord) -> ItemPlanningState:
        hero = item.hero
        slot = item_attr(item, self.prefabs, "item_slot")
        is_base = item_attr(item, self.prefabs, "baseitem") == "1"
        default_item = self.defaults.get((hero, slot)) if hero and slot else None
        persona_slot = "persona" in slot.casefold()
        special_item = any(
            visual.get("type", "")
            in {
                "entity_model",
                "base_model",
                "entity_clientside_model",
                "hero_model_change",
                "model",
                "pet",
            }
            for visual in item.visuals
        )
        category = (
            CATEGORY_PERSONA_MODELS
            if persona_slot
            else CATEGORY_SPECIAL_MODELS
            if special_item
            else CATEGORY_STANDARD_WEARABLES
        )
        return ItemPlanningState(
            item=item,
            hero=hero,
            slot=slot,
            is_base=is_base,
            default_item=default_item,
            persona_slot=persona_slot,
            category=category,
        )

    def default_model_for(
        self,
        default_item: Optional[ItemRecord],
        key: str = "model_player",
    ) -> Optional[str]:
        if default_item is None:
            return None
        by_key = dict(default_item.top_models)
        return by_key.get(key) or by_key.get("model_player")

    def reviewed_persona_model_for(
        self,
        hero: Optional[str],
        slot: str,
        key: str = "model_player",
    ) -> Optional[str]:
        if not hero:
            return None
        profile = PERSONA_PROFILES.get(hero)
        fallback_slot = profile.fallback_slot_for(slot) if profile else None
        if not fallback_slot:
            return None
        return self.default_model_for(self.defaults.get((hero, fallback_slot)), key)

    @staticmethod
    def has_reviewed_persona_slot(hero: Optional[str], slot: str) -> bool:
        if not hero:
            return False
        profile = PERSONA_PROFILES.get(hero)
        return bool(profile and profile.fallback_slot_for(slot))

    def validate_persona_profiles(self) -> None:
        if CATEGORY_PERSONA_MODELS not in self.enabled:
            return

        modeled_slots = {
            (item.hero, item_attr(item, self.prefabs, "item_slot"))
            for item in self.items.values()
            if item.hero and (item.top_models or item.nested_models)
        }
        for profile in PERSONA_PROFILES.values():
            if profile.hero not in self.hero_models:
                continue
            profile_valid = True
            for persona_slot, fallback_slot in profile.slot_fallbacks:
                reason = ""
                if (profile.hero, persona_slot) not in modeled_slots:
                    reason = "reviewed Persona slot is absent from the current schema"
                elif self.default_model_for(
                    self.defaults.get((profile.hero, fallback_slot))
                ) is None:
                    reason = "reviewed fallback slot has no current default model"

                if not reason:
                    self.increment("persona_profile_slots_resolved")
                    continue

                profile_valid = False
                self.increment("persona_profile_slots_unresolved")
                self.unresolved.append(
                    {
                        "item_id": None,
                        "hero": profile.hero,
                        "slot": persona_slot,
                        "type": "persona_profile",
                        "fallback_slot": fallback_slot,
                        "reason": reason,
                    }
                )
            if profile_valid:
                self.increment("persona_profiles_validated")

    def default_entity_model_for(self, asset: str) -> Optional[str]:
        direct = self.entity_defaults.get(asset)
        if direct:
            return direct
        variant_models = {
            model
            for entity, model in self.entity_defaults.items()
            if re.fullmatch(re.escape(asset) + r"_?\d+", entity)
        }
        return next(iter(variant_models)) if len(variant_models) == 1 else None

    def remember_default_source(self, target: str, source: str) -> None:
        self.default_sources_by_target.setdefault(canonical(target), set()).add(
            canonical(source)
        )

    def add_candidate(
        self,
        source: str,
        target: str,
        reason: str,
        item: Optional[ItemRecord] = None,
        *,
        category: str = CATEGORY_STANDARD_WEARABLES,
        resource_type: str = RESOURCE_MODEL,
        hero: Optional[str] = None,
        slot: Optional[str] = None,
        neutralize_model_skin: Optional[bool] = None,
    ) -> None:
        if category not in self.enabled:
            return
        validators = {
            RESOURCE_MODEL: looks_like_model,
            RESOURCE_MATERIAL: looks_like_material,
            RESOURCE_PARTICLE: looks_like_particle,
            RESOURCE_SNAPSHOT: looks_like_particle_snapshot,
        }
        validator = validators.get(resource_type)
        if validator is None or not (validator(source) and validator(target)):
            return
        normalized_source = canonical(source)
        normalized_target = canonical(target)
        if resource_type == RESOURCE_PARTICLE:
            reviewed_source = PARTICLE_DEFAULT_PATH_EXCEPTIONS.get(normalized_source)
            if reviewed_source:
                normalized_source = canonical(reviewed_source)
                self.increment("particle_reviewed_default_fallbacks")
        if resource_type == RESOURCE_MODEL and normalized_target == canonical(INVISIBLE_MODEL):
            return
        if (
            resource_type in {RESOURCE_PARTICLE, RESOURCE_SNAPSHOT}
            and normalized_target in self.protected_effect_resources
        ):
            self.increment("particle_protected_targets_skipped")
            return
        if normalized_source == normalized_target and not (
            resource_type == RESOURCE_MODEL
            and item
            and item.item_id in self.skin_sensitive_item_ids
        ):
            return
        self.candidates.append(
            Mapping(
                source=normalized_source,
                target=normalized_target,
                reason=reason,
                category=category,
                resource_type=resource_type,
                item_id=item.item_id if item else None,
                hero=hero or (item.hero if item else None),
                slot=slot,
                neutralize_model_skin=(
                    bool(item and item.item_id in self.skin_sensitive_item_ids)
                    if neutralize_model_skin is None
                    else neutralize_model_skin
                ),
                required_material_groups=(
                    item.required_material_groups
                    if (
                        item
                        and item.item_id in self.skin_sensitive_item_ids
                        and normalized_source != canonical(INVISIBLE_MODEL)
                    )
                    else 1
                ),
            )
        )


__all__ = [
    "COUNTER_NAMES",
    "ItemPlanningState",
    "PendingModelOverride",
    "PlanningContext",
]
