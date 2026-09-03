"""Reviewed Persona profiles that bridge alternate slots to hero defaults."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class PersonaCompositionProfile:
    """A reviewed two-model composition owned by one Persona economy item."""

    item_id: str
    slot: str
    target: str
    primary_fallback_slot: str
    secondary_fallback_slot: str


@dataclass(frozen=True)
class PersonaSlotCompositionProfile:
    """A reviewed composition applied to every model in one Persona slot."""

    slot: str
    primary_fallback_slot: str
    secondary_fallback_slot: str
    mode: str
    primary_additional_wearable_index: Optional[int] = None
    secondary_additional_wearable_index: Optional[int] = None
    additional_fallbacks: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class PersonaAttachmentOffsetProfile:
    """A reviewed adjustment for a hidden Persona loadout model."""

    slot: str
    trigger_particle: str
    model: str
    attachments: tuple[str, ...]
    offset: tuple[float, float, float]


@dataclass(frozen=True)
class PersonaProfile:
    """A reviewed mapping from one hero's Persona slots to normal hero slots."""

    hero: str
    slot_fallbacks: tuple[tuple[str, str], ...]
    hidden_slots: tuple[str, ...] = ()
    base_visual_slots: tuple[str, ...] = ()
    base_particle_slots: tuple[str, ...] = ()
    model_compositions: tuple[PersonaCompositionProfile, ...] = ()
    slot_compositions: tuple[PersonaSlotCompositionProfile, ...] = ()
    attachment_offsets: tuple[PersonaAttachmentOffsetProfile, ...] = ()

    def fallback_slot_for(self, persona_slot: str) -> Optional[str]:
        return next(
            (
                fallback_slot
                for reviewed_slot, fallback_slot in self.slot_fallbacks
                if reviewed_slot == persona_slot
            ),
            None,
        )

    def hides_slot(self, persona_slot: str) -> bool:
        """Return whether a reviewed Persona-only slot has no normal counterpart."""

        return persona_slot in self.hidden_slots


PERSONA_PROFILES = {
    profile.hero: profile
    for profile in (
        PersonaProfile(
            hero="npc_dota_hero_crystal_maiden",
            # The wolf Persona exposes four wearable slots while the human hero
            # has five. Preserve the four pieces that restore her silhouette and
            # make the staff-holding animations read correctly; omit the cuffs.
            slot_fallbacks=(
                ("head_persona_1", "head"),
                ("armor_persona_1", "back"),
                ("misc_persona_1", "shoulder"),
                ("tail_persona_1", "weapon"),
            ),
        ),
        PersonaProfile(
            hero="npc_dota_hero_phantom_assassin",
            # PA's Persona has four wearable slots while the normal hero has
            # five. Restore the helmet, shoulders, cape, and weapon that define
            # her silhouette; omit only the small belt-mounted daggers.
            slot_fallbacks=(
                ("head_persona_1", "head"),
                ("armor_persona_1", "shoulder"),
                ("legs_persona_1", "back"),
                ("weapon_persona_1", "weapon"),
            ),
        ),
        PersonaProfile(
            hero="npc_dota_hero_antimage",
            # Wei exposes four wearable slots while normal Anti-Mage has six.
            # The weapons, head, and chest have exact semantic counterparts;
            # omit the smaller normal-only arms and belt pieces.
            slot_fallbacks=(
                ("weapon_persona_1", "weapon"),
                ("offhand_weapon_persona_1", "offhand_weapon"),
                ("head_persona_1", "head"),
                ("armor_persona_1", "armor"),
            ),
        ),
        PersonaProfile(
            hero="npc_dota_hero_morphling",
            # Normal Morphling's back is integrated into the base hero model,
            # so the Automaton neck/back hook has no normal wearable to restore.
            # Arms are deliberately omitted: that Persona slot is currently
            # model-less and should be reviewed if Valve gives it a model.
            slot_fallbacks=(),
            hidden_slots=("neck_persona_1",),
        ),
        PersonaProfile(
            hero="npc_dota_hero_axe",
            # The Automaton exposes two wearable hooks for Axe's normal weapon,
            # shoulder guard, hair, belt, and default additional underwear.
            # Keep the weapon direct. Preserve the underwear's animation and
            # morph payload as the primary of the remaining multi-stage union,
            # then add the armor, hair, and belt meshes to the back hook.
            slot_fallbacks=(
                ("weapon_persona_1", "weapon"),
                ("back_persona_1", "belt"),
            ),
            base_particle_slots=("weapon_persona_1",),
            slot_compositions=(
                PersonaSlotCompositionProfile(
                    slot="back_persona_1",
                    primary_fallback_slot="belt",
                    primary_additional_wearable_index=0,
                    secondary_fallback_slot="armor",
                    mode="skeleton-union",
                    additional_fallbacks=(
                        ("head", "skeleton-union"),
                        ("belt", "skeleton-union"),
                    ),
                ),
            ),
        ),
        PersonaProfile(
            hero="npc_dota_hero_oracle",
            # The Automaton exposes two wearable hooks for Oracle's four
            # normal pieces. Keep the weapon direct, then assemble the back,
            # armor, and head on the remaining hook with reviewed skeleton
            # unions that preserve the animated back model as the primary.
            slot_fallbacks=(
                ("weapon_persona_1", "weapon"),
                ("back_persona_1", "back"),
            ),
            base_particle_slots=("weapon_persona_1",),
            slot_compositions=(
                PersonaSlotCompositionProfile(
                    slot="back_persona_1",
                    primary_fallback_slot="back",
                    secondary_fallback_slot="armor",
                    mode="skeleton-union",
                    additional_fallbacks=(("head", "skeleton-union"),),
                ),
            ),
        ),
        PersonaProfile(
            hero="npc_dota_hero_invoker",
            # Kid Invoker has four modeled default wearable hooks plus a
            # model-less armor slot, while adult Invoker has six pieces. The
            # reviewed head composition combines adult hair and face on one
            # always-loaded hook, freeing the shoulder hook for its semantic
            # counterpart. Persona armor cosmetics can carry the small bracer.
            slot_fallbacks=(
                ("head_persona_1", "head"),
                ("shoulder_persona_1", "shoulder"),
                ("back_persona_1", "back"),
                ("arms_persona_1", "belt"),
                ("armor_persona_1", "arms"),
            ),
            base_visual_slots=("summon_persona_1",),
            slot_compositions=(
                PersonaSlotCompositionProfile(
                    slot="head_persona_1",
                    primary_fallback_slot="head",
                    secondary_fallback_slot="body_head",
                    mode="skeleton-overlay",
                ),
            ),
            attachment_offsets=(
                PersonaAttachmentOffsetProfile(
                    slot="head_persona_1",
                    trigger_particle=(
                        "particles/units/heroes/hero_invoker_kid/"
                        "invoker_kid_orbs_loadout.vpcf"
                    ),
                    model=(
                        "models/heroes/invoker_kid/"
                        "invoker_kid_orbs_loadout.vmdl"
                    ),
                    attachments=("attach_orb1", "attach_orb2", "attach_orb3"),
                    offset=(0.0, 0.0, 40.0),
                ),
            ),
        ),
        PersonaProfile(
            hero="npc_dota_hero_pudge",
            # The Toy Butcher has five wearable slots while normal Pudge has
            # seven. Keep both weapons, his hair, the structural left arm, and
            # the large back/apron piece; omit the smaller bracer and belt.
            slot_fallbacks=(
                ("weapon_persona_1", "weapon"),
                ("offhand_weapon_persona_1", "offhand_weapon"),
                ("head_persona_1", "head"),
                ("arms_persona_1", "shoulder"),
                ("armor_persona_1", "back"),
            ),
        ),
        PersonaProfile(
            hero="npc_dota_hero_dragon_knight",
            # Davion exposes only three wearable slots. Preserve the helmet,
            # sword, and shield needed for his silhouette and combat poses;
            # omit the pauldrons, bracers, and skirt. The reviewed shapeshift
            # visual also restores the normal Elder Dragon model.
            slot_fallbacks=(
                ("head_persona_1", "head"),
                ("weapon_persona_1", "weapon"),
                ("armor_persona_1", "offhand_weapon"),
            ),
            base_visual_slots=("shapeshift_persona_1",),
        ),
        PersonaProfile(
            hero="npc_dota_hero_mirana",
            # Mirana's Persona has five wearable slots for seven normal pieces.
            # Preserve her mount, bow, headdress, cape, and pauldrons; omit the
            # smaller bracers and quiver.
            slot_fallbacks=(
                ("mount_persona_1", "mount"),
                ("weapon_persona_1", "weapon"),
                ("head_persona_1", "head"),
                ("back_persona_1", "back"),
                ("armor_persona_1", "shoulder"),
            ),
            model_compositions=(
                PersonaCompositionProfile(
                    item_id="18603",
                    slot="back_persona_1",
                    target=(
                        "models/items/mirana_persona/dark_moon_armor/"
                        "mirana_persona_head_dark_moon_refit.vmdl"
                    ),
                    primary_fallback_slot="head",
                    secondary_fallback_slot="back",
                ),
            ),
        ),
    )
}


__all__ = [
    "PERSONA_PROFILES",
    "PersonaAttachmentOffsetProfile",
    "PersonaCompositionProfile",
    "PersonaProfile",
    "PersonaSlotCompositionProfile",
]
