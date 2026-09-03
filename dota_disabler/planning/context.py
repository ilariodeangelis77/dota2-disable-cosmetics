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
    PARTICLE_REPLACEMENT_TYPES,
    RESOURCE_MATERIAL,
    RESOURCE_MODEL,
    RESOURCE_PARTICLE,
    RESOURCE_SNAPSHOT,
    RETIRED_ITEM_NAME_MARKERS,
    SUPPORTED_CATEGORIES,
)
from ..domain import (
    ItemRecord,
    Mapping,
    ModelAttachmentOffset,
    ModelComposition,
    ModelCompositionPart,
)
from ..resources import (
    canonical,
    looks_like_material,
    looks_like_model,
    looks_like_particle,
    looks_like_particle_snapshot,
)
from ..schema import item_attr
from .personas import (
    PERSONA_PROFILES,
    PersonaAttachmentOffsetProfile,
    PersonaCompositionProfile,
    PersonaProfile,
    PersonaSlotCompositionProfile,
)


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
    "persona_slot_models_hidden",
    "persona_profiles_validated",
    "persona_profile_slots_resolved",
    "persona_profile_slots_unresolved",
    "persona_model_compositions_planned",
    "persona_model_compositions_unresolved",
    "persona_attachment_offsets_planned",
    "persona_attachment_offsets_unresolved",
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
    model_compositions: list[ModelComposition] = field(default_factory=list)
    model_attachment_offsets: list[ModelAttachmentOffset] = field(default_factory=list)
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
            slot = item_attr(item, prefabs, "item_slot")
            profile = PERSONA_PROFILES.get(item.hero or "")
            reviewed_persona_particles = bool(
                profile and slot in profile.base_particle_slots
            )
            for visual in item.visuals:
                for field in ("asset", "modifier"):
                    if (
                        reviewed_persona_particles
                        and field == "modifier"
                        and visual.get("type") in PARTICLE_REPLACEMENT_TYPES
                    ):
                        continue
                    resource = visual.get(field, "")
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

    def default_composition_model_for(
        self,
        hero: str,
        fallback_slot: str,
        additional_wearable_index: Optional[int] = None,
    ) -> Optional[str]:
        """Resolve a normal slot's main or indexed additional wearable model."""

        if additional_wearable_index is None:
            return self.default_model_for(self.defaults.get((hero, fallback_slot)))
        if additional_wearable_index < 0:
            return None
        additional = self.default_additional.get((hero, fallback_slot), [])
        return (
            additional[additional_wearable_index]
            if additional_wearable_index < len(additional)
            else None
        )

    def reviewed_persona_model_for(
        self,
        hero: Optional[str],
        slot: str,
        key: str = "model_player",
    ) -> Optional[str]:
        if not hero:
            return None
        profile = PERSONA_PROFILES.get(hero)
        if profile and profile.hides_slot(slot):
            return INVISIBLE_MODEL
        fallback_slot = profile.fallback_slot_for(slot) if profile else None
        if not fallback_slot:
            return None
        return self.default_model_for(self.defaults.get((hero, fallback_slot)), key)

    @staticmethod
    def has_reviewed_persona_slot(hero: Optional[str], slot: str) -> bool:
        if not hero:
            return False
        profile = PERSONA_PROFILES.get(hero)
        return bool(
            profile
            and (profile.fallback_slot_for(slot) or profile.hides_slot(slot))
        )

    @staticmethod
    def has_reviewed_persona_base_visual_slot(
        hero: Optional[str],
        slot: str,
    ) -> bool:
        if not hero:
            return False
        profile = PERSONA_PROFILES.get(hero)
        return bool(profile and slot in profile.base_visual_slots)

    @staticmethod
    def has_reviewed_persona_base_particle_slot(
        hero: Optional[str],
        slot: str,
    ) -> bool:
        if not hero:
            return False
        profile = PERSONA_PROFILES.get(hero)
        return bool(profile and slot in profile.base_particle_slots)

    def validate_persona_profiles(self) -> None:
        if CATEGORY_PERSONA_MODELS not in self.enabled:
            return

        modeled_slots = {
            (item.hero, item_attr(item, self.prefabs, "item_slot"))
            for item in self.items.values()
            if item.hero and (item.top_models or item.nested_models)
        }
        modeled_base_visual_slots = {
            (item.hero, item_attr(item, self.prefabs, "item_slot"))
            for item in self.items.values()
            if item.hero
            and item_attr(item, self.prefabs, "baseitem") == "1"
            and any(
                visual.get("type")
                in {
                    "entity_model",
                    "base_model",
                    "entity_clientside_model",
                    "hero_model_change",
                }
                and looks_like_model(visual.get("modifier", ""))
                for visual in item.visuals
            )
        }
        modeled_base_particle_slots = {
            (item.hero, item_attr(item, self.prefabs, "item_slot"))
            for item in self.items.values()
            if item.hero
            and item_attr(item, self.prefabs, "baseitem") == "1"
            and any(
                visual.get("type") in PARTICLE_REPLACEMENT_TYPES
                and looks_like_particle(visual.get("asset", ""))
                and looks_like_particle(visual.get("modifier", ""))
                for visual in item.visuals
            )
        }

        def selector_has_rule(
            profile: PersonaProfile,
            persona_slot: str,
            rule_types: set[str],
        ) -> bool:
            """Validate a reviewed non-base Persona selector by its item ID."""

            if profile.selector_item_id is None:
                return False
            item = self.items.get(profile.selector_item_id)
            return bool(
                item
                and item.hero == profile.hero
                and item_attr(item, self.prefabs, "item_slot") == persona_slot
                and any(
                    visual.get("type") in rule_types
                    and looks_like_model(visual.get("modifier", ""))
                    for visual in item.visuals
                )
            )

        def selector_has_particle_rule(
            profile: PersonaProfile,
            persona_slot: str,
        ) -> bool:
            if profile.selector_item_id is None:
                return False
            item = self.items.get(profile.selector_item_id)
            return bool(
                item
                and item.hero == profile.hero
                and item_attr(item, self.prefabs, "item_slot") == persona_slot
                and any(
                    visual.get("type") in PARTICLE_REPLACEMENT_TYPES
                    and looks_like_particle(visual.get("asset", ""))
                    and looks_like_particle(visual.get("modifier", ""))
                    for visual in item.visuals
                )
            )
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
            for persona_slot in profile.hidden_slots:
                if (profile.hero, persona_slot) in modeled_slots:
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
                        "fallback_slot": None,
                        "reason": (
                            "reviewed hidden Persona slot is absent from the "
                            "current schema"
                        ),
                    }
                )
            for persona_slot in profile.base_visual_slots:
                if (
                    (profile.hero, persona_slot) in modeled_base_visual_slots
                    or selector_has_rule(
                        profile,
                        persona_slot,
                        {
                            "entity_model",
                            "base_model",
                            "entity_clientside_model",
                            "hero_model_change",
                        },
                    )
                ):
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
                        "fallback_slot": None,
                        "reason": (
                            "reviewed Persona base visual slot has no current "
                            "model-changing rule"
                        ),
                    }
                )
            for persona_slot in profile.base_particle_slots:
                if (
                    (profile.hero, persona_slot) in modeled_base_particle_slots
                    or selector_has_particle_rule(profile, persona_slot)
                ):
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
                        "fallback_slot": None,
                        "reason": (
                            "reviewed Persona base particle slot has no current "
                            "particle-replacement rule"
                        ),
                    }
                )
            for composition_profile in profile.model_compositions:
                composition, reason = self._resolve_persona_composition(
                    profile,
                    composition_profile,
                )
                if composition is not None:
                    self.model_compositions.append(composition)
                    self.increment("persona_model_compositions_planned")
                    continue

                profile_valid = False
                self.increment("persona_model_compositions_unresolved")
                self.unresolved.append(
                    {
                        "item_id": composition_profile.item_id,
                        "hero": profile.hero,
                        "slot": composition_profile.slot,
                        "type": "persona_model_composition",
                        "target": canonical(composition_profile.target),
                        "primary_fallback_slot": (
                            composition_profile.primary_fallback_slot
                        ),
                        "secondary_fallback_slot": (
                            composition_profile.secondary_fallback_slot
                        ),
                        "reason": reason,
                    }
                )
            for composition_profile in profile.slot_compositions:
                compositions, reason = self._resolve_persona_slot_compositions(
                    profile,
                    composition_profile,
                )
                if compositions:
                    self.model_compositions.extend(compositions)
                    self.increment(
                        "persona_model_compositions_planned",
                        len(compositions),
                    )
                    continue

                profile_valid = False
                self.increment("persona_model_compositions_unresolved")
                self.unresolved.append(
                    {
                        "item_id": None,
                        "hero": profile.hero,
                        "slot": composition_profile.slot,
                        "type": "persona_slot_model_composition",
                        "primary_fallback_slot": (
                            composition_profile.primary_fallback_slot
                        ),
                        "secondary_fallback_slot": (
                            composition_profile.secondary_fallback_slot
                        ),
                        "primary_additional_wearable_index": (
                            composition_profile.primary_additional_wearable_index
                        ),
                        "secondary_additional_wearable_index": (
                            composition_profile.secondary_additional_wearable_index
                        ),
                        "additional_fallbacks": (
                            composition_profile.additional_fallbacks
                        ),
                        "mode": composition_profile.mode,
                        "reason": reason,
                    }
                )
            for offset_profile in profile.attachment_offsets:
                adjustment, reason = self._resolve_persona_attachment_offset(
                    profile,
                    offset_profile,
                )
                if adjustment is not None:
                    self.model_attachment_offsets.append(adjustment)
                    self.increment("persona_attachment_offsets_planned")
                    continue

                profile_valid = False
                self.increment("persona_attachment_offsets_unresolved")
                self.unresolved.append(
                    {
                        "item_id": None,
                        "hero": profile.hero,
                        "slot": offset_profile.slot,
                        "type": "persona_attachment_offset",
                        "trigger_particle": canonical(offset_profile.trigger_particle),
                        "target": canonical(offset_profile.model),
                        "reason": reason,
                    }
                )
            if profile_valid:
                self.increment("persona_profiles_validated")

    def _resolve_persona_attachment_offset(
        self,
        profile: PersonaProfile,
        offset_profile: PersonaAttachmentOffsetProfile,
    ) -> tuple[Optional[ModelAttachmentOffset], str]:
        item = self.defaults.get((profile.hero, offset_profile.slot))
        if item is None:
            return None, "reviewed Persona offset slot has no current default item"

        created_particles = {
            canonical(visual.get("modifier", "") or visual.get("asset", ""))
            for visual in item.visuals
            if visual.get("type") == "particle_create"
            and looks_like_particle(
                visual.get("modifier", "") or visual.get("asset", "")
            )
        }
        trigger = canonical(offset_profile.trigger_particle)
        if trigger not in created_particles:
            return None, "reviewed Persona loadout particle is absent from the current slot"
        if not looks_like_model(offset_profile.model):
            return None, "reviewed Persona offset target is not a safe model path"
        if not offset_profile.attachments or any(
            not attachment.strip() for attachment in offset_profile.attachments
        ):
            return None, "reviewed Persona offset has invalid attachment names"
        if not any(offset_profile.offset):
            return None, "reviewed Persona offset is empty"

        model = canonical(offset_profile.model)
        return (
            ModelAttachmentOffset(
                source=model,
                target=model,
                attachments=offset_profile.attachments,
                offset=offset_profile.offset,
                reason="reviewed Persona loadout attachment height restored",
                category=CATEGORY_PERSONA_MODELS,
                item_id=item.item_id,
                hero=profile.hero,
                slot=offset_profile.slot,
            ),
            "",
        )

    def _resolve_persona_composition(
        self,
        profile: PersonaProfile,
        composition_profile: PersonaCompositionProfile,
    ) -> tuple[Optional[ModelComposition], str]:
        item = self.items.get(composition_profile.item_id)
        if item is None:
            return (
                None,
                "reviewed Persona composition item is absent from the current schema",
            )
        if item.hero != profile.hero:
            return None, "reviewed Persona composition item belongs to a different hero"
        current_slot = item_attr(item, self.prefabs, "item_slot")
        if current_slot != composition_profile.slot:
            return None, "reviewed Persona composition item moved to a different slot"

        if not looks_like_model(composition_profile.target):
            return None, "reviewed Persona composition target is not a safe model path"
        target = canonical(composition_profile.target)
        current_targets = {
            canonical(model)
            for _key, model in (*item.top_models, *item.nested_models)
            if looks_like_model(model)
        }
        current_targets.update(
            canonical(modifier)
            for visual in item.visuals
            for modifier in (visual.get("modifier", ""),)
            if looks_like_model(modifier)
        )
        if target not in current_targets:
            return None, "reviewed Persona composition target is absent from the current item"

        primary_source = self.default_model_for(
            self.defaults.get(
                (profile.hero, composition_profile.primary_fallback_slot)
            )
        )
        secondary_source = self.default_model_for(
            self.defaults.get(
                (profile.hero, composition_profile.secondary_fallback_slot)
            )
        )
        if primary_source is None:
            return None, "reviewed primary fallback slot has no current default model"
        if secondary_source is None:
            return None, "reviewed secondary fallback slot has no current default model"
        if not looks_like_model(primary_source):
            return None, "reviewed primary fallback is not a safe model path"
        if not looks_like_model(secondary_source):
            return None, "reviewed secondary fallback is not a safe model path"
        primary_source = canonical(primary_source)
        secondary_source = canonical(secondary_source)
        if primary_source == secondary_source:
            return (
                None,
                "reviewed Persona composition resolved to the same source model twice",
            )

        return (
            ModelComposition(
                primary_source=primary_source,
                secondary_source=secondary_source,
                target=target,
                reason=(
                    "reviewed Persona wearable composed from compatible hero slot defaults"
                ),
                category=CATEGORY_PERSONA_MODELS,
                item_id=item.item_id,
                hero=profile.hero,
                slot=current_slot,
            ),
            "",
        )

    def _resolve_persona_slot_compositions(
        self,
        profile: PersonaProfile,
        composition_profile: PersonaSlotCompositionProfile,
    ) -> tuple[list[ModelComposition], str]:
        supported_modes = {"shared-root", "skeleton-overlay", "skeleton-union"}
        if composition_profile.mode not in supported_modes or any(
            mode not in supported_modes
            for _fallback_slot, mode in composition_profile.additional_fallbacks
        ):
            return [], "reviewed Persona slot composition has an unsupported mode"

        primary_source = self.default_composition_model_for(
            profile.hero,
            composition_profile.primary_fallback_slot,
            composition_profile.primary_additional_wearable_index,
        )
        secondary_source = self.default_composition_model_for(
            profile.hero,
            composition_profile.secondary_fallback_slot,
            composition_profile.secondary_additional_wearable_index,
        )
        if primary_source is None:
            return (
                [],
                "reviewed primary fallback has no current default composition model",
            )
        if secondary_source is None:
            return (
                [],
                "reviewed secondary fallback has no current default composition model",
            )
        if not looks_like_model(primary_source):
            return [], "reviewed primary fallback is not a safe model path"
        if not looks_like_model(secondary_source):
            return [], "reviewed secondary fallback is not a safe model path"
        primary_source = canonical(primary_source)
        secondary_source = canonical(secondary_source)
        additional_parts: list[ModelCompositionPart] = []
        for fallback_slot, mode in composition_profile.additional_fallbacks:
            source = self.default_model_for(
                self.defaults.get((profile.hero, fallback_slot))
            )
            if source is None:
                return (
                    [],
                    f"reviewed additional fallback slot {fallback_slot} has no current default model",
                )
            if not looks_like_model(source):
                return [], "reviewed additional fallback is not a safe model path"
            additional_parts.append(
                ModelCompositionPart(source=canonical(source), mode=mode)
            )

        sources = [
            primary_source,
            secondary_source,
            *(part.source for part in additional_parts),
        ]
        if len(set(sources)) != len(sources):
            return [], "reviewed Persona slot composition resolved to one source model"

        target_owners: dict[str, set[str]] = {}
        for item in self.items.values():
            if (
                item.hero != profile.hero
                or item_attr(item, self.prefabs, "item_slot")
                != composition_profile.slot
            ):
                continue
            targets = {
                canonical(model)
                for _key, model in (*item.top_models, *item.nested_models)
                if looks_like_model(model)
            }
            targets.update(
                canonical(visual.get("modifier", ""))
                for visual in item.visuals
                if visual.get("type") == "model"
                and looks_like_model(visual.get("modifier", ""))
            )
            for target in targets:
                target_owners.setdefault(target, set()).add(item.item_id)

        if not target_owners:
            return [], "reviewed Persona composition slot has no current model targets"

        return (
            [
                ModelComposition(
                    primary_source=primary_source,
                    secondary_source=secondary_source,
                    target=target,
                    reason=(
                        "reviewed Persona slot composed from compatible hero defaults"
                    ),
                    category=CATEGORY_PERSONA_MODELS,
                    item_id=min(
                        item_ids,
                        key=lambda item_id: (
                            not item_id.isdigit(),
                            int(item_id) if item_id.isdigit() else item_id,
                        ),
                    ),
                    hero=profile.hero,
                    slot=composition_profile.slot,
                    mode=composition_profile.mode,
                    additional_parts=tuple(additional_parts),
                )
                for target, item_ids in sorted(target_owners.items())
            ],
            "",
        )

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
