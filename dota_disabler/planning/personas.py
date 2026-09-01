"""Reviewed Persona profiles that bridge alternate slots to hero defaults."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class PersonaProfile:
    """A reviewed mapping from one hero's Persona slots to normal hero slots."""

    hero: str
    slot_fallbacks: tuple[tuple[str, str], ...]

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
    )
}


__all__ = ["PERSONA_PROFILES", "PersonaProfile"]
