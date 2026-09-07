import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import disable_cosmetics as generator


EMBER = "npc_dota_hero_ember_spirit"
AMBIENT = (
    "particles/units/heroes/hero_ember_spirit/"
    "ember_spirit_ambient_sword_primary.vpcf"
)
BLADE = (
    "particles/units/heroes/hero_ember_spirit/"
    "ember_spirit_ambient_sword_primary_blade.vpcf"
)
COSMETIC_PARTICLE = (
    "particles/econ/items/ember_spirit/ember_ti10_cache/"
    "ember_ti10_cache_weapon.vpcf"
)
DEFAULT_MODEL = "models/heroes/ember_spirit/weapon1.vmdl"
COSMETIC_MODEL = (
    "models/items/ember_spirit/kungfu_master_weapon/kungfu_master_weapon.vmdl"
)


def ember_item(item_id, *, base=False, model=DEFAULT_MODEL, visuals=()):
    return generator.ItemRecord(
        item_id=str(item_id),
        name=f"item_{item_id}",
        prefab="",
        item_slot="weapon",
        baseitem="1" if base else "0",
        hero=EMBER,
        top_models=[("model_player", model)],
        nested_models=[],
        visuals=list(visuals),
        has_nondefault_skin=False,
        required_material_groups=1,
        bundle_members=(),
    )


def ember_plan():
    default_weapon = ember_item(
        472,
        base=True,
        visuals=(
            {"type": "particle_create", "modifier": AMBIENT},
            {"type": "particle_create", "modifier": BLADE},
        ),
    )
    cosmetic = ember_item(
        13300,
        model=COSMETIC_MODEL,
        visuals=({"type": "particle_create", "modifier": COSMETIC_PARTICLE},),
    )
    return generator.build_plan(
        {},
        {record.item_id: record for record in (default_weapon, cosmetic)},
        {EMBER: "models/heroes/ember_spirit/ember_spirit.vmdl"},
        [],
    )


class ParticleSupplementPlanningTests(unittest.TestCase):
    def test_reviewed_ember_weapon_supplements_its_missing_blade_particle(self):
        plan = ember_plan()
        by_target = {mapping.target: mapping for mapping in plan.mappings}
        private_particle = (
            "particles/dota2_cosmetic_disabler/heroes/ember_spirit/"
            "13300_ember_spirit_ambient_sword_primary_blade.vpcf"
        )

        self.assertEqual(by_target[COSMETIC_MODEL].source, DEFAULT_MODEL)
        self.assertEqual(by_target[COSMETIC_PARTICLE].source, AMBIENT)
        self.assertEqual(by_target[private_particle].source, BLADE)
        self.assertEqual(len(plan.model_particle_bridges), 1)
        bridge = plan.model_particle_bridges[0]
        self.assertEqual(bridge.target, COSMETIC_MODEL)
        self.assertEqual(bridge.private_particle, private_particle)
        self.assertFalse(bridge.required_for_model)
        self.assertEqual(plan.stats["model_particle_supplements_planned"], 1)
        self.assertEqual(plan.stats["model_particle_supplements_skipped"], 0)

    def test_unavailable_supplement_keeps_the_direct_model_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            adjusted = generator.apply_missing_particle_fallbacks(
                ember_plan(),
                Path(temporary),
            )

        by_target = {mapping.target: mapping for mapping in adjusted.mappings}
        self.assertEqual(by_target[COSMETIC_MODEL].source, DEFAULT_MODEL)
        self.assertEqual(adjusted.model_particle_bridges, [])
        self.assertEqual(adjusted.stats["model_particle_supplements_planned"], 0)
        self.assertEqual(adjusted.stats["model_particle_supplements_skipped"], 1)
        self.assertEqual(adjusted.unresolved[-1]["type"], "model_particle_supplement")


class ParticleSupplementDeploymentTests(unittest.TestCase):
    @staticmethod
    def extractor_path():
        published = os.environ.get("DOTA2_COSMETIC_DISABLER_TEST_EXTRACTOR")
        if published:
            return Path(published).resolve()
        executable = (
            "Dota2VpkExtractor.exe" if generator.os.name == "nt" else "Dota2VpkExtractor"
        )
        return (
            Path(__file__).resolve().parents[1]
            / "tools"
            / "VpkExtractor"
            / "bin"
            / "Release"
            / "net10.0"
            / executable
        )

    def test_missing_optional_particle_uses_the_direct_model(self):
        extractor = self.extractor_path()
        if not extractor.is_file():
            self.skipTest("Build tools/VpkExtractor in Release mode to run this test")
        bridge = generator.ModelParticleBridge(
            source_model=DEFAULT_MODEL,
            template_model="models/items/io/madame.vmdl",
            target=COSMETIC_MODEL,
            source_particle=BLADE,
            private_particle=(
                "particles/dota2_cosmetic_disabler/heroes/ember_spirit/"
                "13300_ember_spirit_ambient_sword_primary_blade.vpcf"
            ),
            template_particle="particles/econ/items/wisp/io_carnival_ambient.vpcf",
            reason="reviewed particle supplement",
            category=generator.CATEGORY_STANDARD_WEARABLES,
            item_id="13300",
            hero=EMBER,
            slot="weapon",
            required_for_model=False,
        )
        plan = generator.Plan(
            mappings=[
                generator.Mapping(
                    source=bridge.source_model,
                    target=bridge.target,
                    reason=bridge.reason,
                    item_id=bridge.item_id,
                    hero=bridge.hero,
                    slot=bridge.slot,
                ),
                generator.Mapping(
                    source=bridge.source_particle,
                    target=bridge.private_particle,
                    reason="private blade",
                    category=generator.CATEGORY_PARTICLE_EFFECTS,
                    resource_type=generator.RESOURCE_PARTICLE,
                    item_id=bridge.item_id,
                    hero=bridge.hero,
                    slot=bridge.slot,
                ),
            ],
            unresolved=[],
            stats={},
            model_particle_bridges=[bridge],
        )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            for resource, payload in (
                (bridge.source_model, b"base weapon"),
                (bridge.template_model, b"particle template"),
            ):
                path = cache / generator.compiled_model_path(resource)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
            output = root / "dota_dutch"

            with patch("dota_disabler.deployment.bridge_model_particle") as bridge_model:
                copied, missing = generator.deploy_overrides(
                    plan,
                    cache,
                    output,
                    root / "work",
                    extractor=extractor,
                    model_patcher=root / "patcher.exe",
                    clean_first=True,
                    allow_missing=True,
                    language="dutch",
                    progress=lambda _message: None,
                )

            self.assertEqual(copied, 1)
            self.assertTrue(
                any(entry["source"] == bridge.source_particle for entry in missing)
            )
            bridge_model.assert_not_called()
            marker = generator.read_marker(output, allow_shared_directory=True)
            unpacked = root / "unpacked"
            generator.extract_vpk(
                extractor,
                output / marker["files"][0],
                marker["resources"],
                unpacked,
            )
            self.assertEqual(
                (unpacked / generator.compiled_model_path(bridge.target)).read_bytes(),
                b"base weapon",
            )


if __name__ == "__main__":
    unittest.main()
