"""Stable configuration and schema policy constants.

This module deliberately contains no application or infrastructure imports so
schema parsing, planning, deployment, the CLI, and the UI can depend on the
same vocabulary without creating circular dependencies.
"""

from __future__ import annotations


# Dota stopped mounting arbitrary language names in 2026. A recognized but
# otherwise unused locale keeps the normal English base resources while giving
# this tool a dedicated, higher-priority VPK mount.
DEFAULT_LANGUAGE = "dutch"
LEGACY_LANGUAGE = "defaultmodels"
RECOGNIZED_LANGUAGES = frozenset(
    {
        "brazilian",
        "bulgarian",
        "czech",
        "danish",
        "dutch",
        "finnish",
        "french",
        "german",
        "greek",
        "hungarian",
        "italian",
        "japanese",
        "koreana",
        "latam",
        "norwegian",
        "polish",
        "portuguese",
        "romanian",
        "russian",
        "schinese",
        "spanish",
        "swedish",
        "tchinese",
        "thai",
        "turkish",
        "ukrainian",
        "vietnamese",
    }
)

VPK_DEPLOYMENT_MODE = "recognized-language-vpk"
# pak99 is used by Dota2 Minify's English-localization compatibility archive;
# leave it available so both tools can coexist in the same recognized locale.
VPK_ARCHIVE_CANDIDATES = tuple(f"pak{number:02d}_dir.vpk" for number in range(98, 89, -1))
MARKER_FILENAME = ".dota-default-models.json"
MARKER_KIND = "dota2-default-models"
HISTORY_FILENAME = "dota-version-history.json"
HISTORY_KIND = "dota2-cosmetic-disabler-version-history"
HISTORY_FORMAT_VERSION = 1

ITEMS_SCHEMA_RESOURCE = "scripts/items/items_game.txt"
INVISIBLE_MODEL = "models/development/invisiblebox.vmdl"
NEUTRAL_PARTICLE = "particles/error/null.vpcf"
MODEL_KEYS = ("model_player", "model_player1", "model_player2", "model_player3", "model_player4")

# Reviewed exceptions for economy records that describe an entire hero as an
# ordinary wearable or need a skeleton-compatible full-hero replacement.
FULL_HERO_WEARABLE_ITEMS = frozenset({"34398"})  # Madame Scrio (Io)
FULL_HERO_INTEGRATED_SLOTS = frozenset({("npc_dota_hero_witch_doctor", "back")})
RETIRED_ITEM_NAME_MARKERS = (" expired",)

CATEGORY_STANDARD_WEARABLES = "standard_wearables"
CATEGORY_PERSONA_MODELS = "persona_models"
CATEGORY_SPECIAL_MODELS = "special_models"
CATEGORY_ADDITIONAL_WEARABLES = "additional_wearables"
CATEGORY_PARTICLE_EFFECTS = "particle_effects"
MODEL_CATEGORIES = (
    CATEGORY_STANDARD_WEARABLES,
    CATEGORY_PERSONA_MODELS,
    CATEGORY_SPECIAL_MODELS,
    CATEGORY_ADDITIONAL_WEARABLES,
)
SUPPORTED_CATEGORIES = MODEL_CATEGORIES + (CATEGORY_PARTICLE_EFFECTS,)
DEFAULT_CATEGORIES = frozenset(SUPPORTED_CATEGORIES)
DEFAULT_MODEL_CATEGORIES = frozenset(MODEL_CATEGORIES)

RESOURCE_MODEL = "model"
RESOURCE_MATERIAL = "material"
RESOURCE_PARTICLE = "particle"
RESOURCE_SNAPSHOT = "particle_snapshot"

PARTICLE_REPLACEMENT_TYPES = frozenset(
    {"particle", "particle_clientside", "particle_combined"}
)
PARTICLE_DEFAULT_PATH_EXCEPTIONS = {
    (
        "particles/econ/items/juggernaut/bladekeeper_omnislash/"
        "_dc_juggernaut_omni_slash_tgt.vpcf.vpcf"
    ): (
        "particles/econ/items/juggernaut/bladekeeper_omnislash/"
        "_dc_juggernaut_omni_slash_tgt.vpcf"
    ),
    (
        "particles/units/heroes/hero_ogre_magi/"
        "ogre_magi_arcana_multicast_counter.vpcf"
    ): (
        "particles/units/heroes/hero_ogre_magi/"
        "ogre_magi_multicast_counter.vpcf"
    ),
    (
        "particles/econ/items/faceless_void/faceless_void_arcana/"
        "faceless_void_arcana_time_walk_preimage_v2.vpcf"
    ): (
        "particles/econ/items/faceless_void/faceless_void_arcana/"
        "faceless_void_arcana_time_walk_v2_preimage.vpcf"
    ),
    (
        "particles/units/heroes/hero_pangolier/armadillo_swashbuckler_dash.vpcf"
    ): "particles/units/heroes/hero_pangolier/pangolier_swashbuckler_dash.vpcf",
    (
        "particles/units/heroes/hero_warlock/warlock_upheval_cast_econ.vpcf"
    ): "particles/units/heroes/hero_warlock/warlock_upheaval.vpcf",
}
INTENTIONALLY_NEUTRAL_PARTICLE_PREFIXES = (
    "particles/ability_modifier_placeholder/",
    "particles/reftononexistentsystem/cannotfindremap/",
)
INTENTIONALLY_NEUTRAL_PARTICLE_DEFAULTS = frozenset(
    {
        "particles/units/heroes/hero_dragon_knight/dragon_knight_transform_black_ambient.vpcf",
        "particles/units/heroes/hero_dragon_knight/dragon_knight_transform_blue_ambient.vpcf",
        "particles/units/heroes/hero_dragon_knight/dragon_knight_transform_green_ambient.vpcf",
        "particles/units/heroes/hero_dragon_knight/dragon_knight_transform_red_ambient.vpcf",
    }
)
COSMETIC_ADDITIVE_PARTICLE_PREFIXES = (
    "particles/econ/",
    "particles/models/items/",
    "particles/world_tower/tower_upgrade/",
)

# Current schemas contain a few model-refit assets that are stale or no longer
# packaged. These remain deliberately item- and path-specific.
MODEL_ASSET_DEFAULT_EXCEPTIONS = {
    (
        "12930",
        "models/items/queenofpain/qop_crimson_matriarch_shoulder/qop_crimson_matriarch_shoulder.vmdl",
    ): "models/heroes/queenofpain/shoulders.vmdl",
    (
        "12930",
        "models/items/queenofpain/qop_scourge_of_dungeon_shoulder/qop_scourge_of_dungeon_shoulder.vmdl",
    ): "models/heroes/queenofpain/shoulders.vmdl",
    (
        "12930",
        "models/items/queenofpain/tpl_shoulder_lvl1/tpl_shoulder_lvl2.vmdl",
    ): "models/heroes/queenofpain/shoulders.vmdl",
    (
        "12930",
        "models/items/queenofpain/tpl_shoulder_lvl1/tpl_shoulder_lvl3.vmdl",
    ): "models/heroes/queenofpain/shoulders.vmdl",
    (
        "13806",
        "models/items/windrunner/wr_spriggan_shoulder_of_spriggan/wr_spriggan_shoulder_of_spriggan.vmdl",
    ): "models/heroes/windrunner/windrunner_shoulderpads.vmdl",
}


__all__ = [name for name in globals() if name.isupper() and not name.startswith("_")]
