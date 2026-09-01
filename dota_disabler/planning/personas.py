"""Reviewed Persona profiles that bridge alternate slots to hero defaults."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class PersonaProfile:
    """A reviewed mapping from one hero's Persona slots to normal hero slots."""

    hero: str
    slot_fallbacks: tuple[tuple[str, str], ...]
    base_visual_slots: tuple[str, ...] = ()

    def fallback_slot_for(self, persona_slot: str) -> Optional[str]:
        return next(
            (
                fallback_slot
                for reviewed_slot, fallback_slot in self.slot_fallbacks
                if reviewed_slot == persona_slot
            ),
            None,
        )


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
    )
}


__all__ = ["PERSONA_PROFILES", "PersonaProfile"]
