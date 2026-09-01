import json
import io
import os
import struct
import subprocess
import tempfile
import unittest
import zlib
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import disable_cosmetics as generator
import disabler_gui
from dota_disabler import model_patcher


HERO = "npc_dota_hero_test"


def item(
    item_id,
    *,
    slot,
    name=None,
    prefab="",
    hero=HERO,
    baseitem="0",
    model=None,
    nested_models=None,
    visuals=None,
    has_nondefault_skin=False,
    required_material_groups=None,
    bundle_members=(),
):
    return generator.ItemRecord(
        item_id=str(item_id),
        name=name or f"item_{item_id}",
        prefab=prefab,
        item_slot=slot,
        baseitem=baseitem,
        hero=hero,
        top_models=[("model_player", model)] if model else [],
        nested_models=nested_models or [],
        visuals=visuals or [],
        has_nondefault_skin=has_nondefault_skin,
        required_material_groups=(
            required_material_groups
            if required_material_groups is not None
            else 2 if has_nondefault_skin else 1
        ),
        bundle_members=tuple(bundle_members),
    )


class KeyValuesTests(unittest.TestCase):
    def test_parser_preserves_duplicate_keys_and_skips_comments_and_conditions(self):
        tokens = generator.TokenStream(
            '''
            // comment
            "root" {
                "duplicate" "one"
                "duplicate" "two" [$WIN32]
                "path" "C:\\Games\\Dota"
            }
            '''
        )
        self.assertEqual(tokens.next(), "root")
        value = generator.parse_value(tokens)
        self.assertIsInstance(value, generator.KVObject)
        self.assertEqual(value.get_last("duplicate"), "two")
        self.assertEqual(value.get_last("path"), "C:\\Games\\Dota")


class DotaVersionTests(unittest.TestCase):
    @staticmethod
    def make_dota(root, *, build_id="12345678"):
        dota = root / "steamapps/common/dota 2 beta"
        pak = dota / "game/dota/pak01_dir.vpk"
        pak.parent.mkdir(parents=True)
        pak.write_bytes(b"versioned Dota VPK fixture")
        manifest = root / "steamapps/appmanifest_570.acf"
        manifest.write_text(
            f'''"AppState"
{{
    "appid" "570"
    "name" "Dota 2"
    "buildid" "{build_id}"
    "LastUpdated" "1770000000"
}}
''',
            encoding="utf-8",
        )
        return dota, manifest

    def test_capture_uses_steam_build_id_and_vpk_fallback_stamp(self):
        with tempfile.TemporaryDirectory() as temporary:
            dota, manifest = self.make_dota(Path(temporary))
            version = generator.capture_dota_version(dota)
            self.assertEqual(version["steam_build_id"], "12345678")
            self.assertEqual(version["steam_last_updated_unix"], "1770000000")
            self.assertEqual(version["steam_manifest_path"], str(manifest))
            self.assertGreater(version["pak01_dir"]["size_bytes"], 0)
            self.assertIsInstance(version["pak01_dir"]["mtime_ns"], int)

    def test_version_comparison_prefers_build_id_and_falls_back_to_vpk_stamp(self):
        first = {
            "steam_build_id": "100",
            "pak01_dir": {"size_bytes": 10, "mtime_ns": 20},
        }
        same = {
            "steam_build_id": "100",
            "pak01_dir": {"size_bytes": 11, "mtime_ns": 21},
        }
        changed = {
            "steam_build_id": "101",
            "pak01_dir": {"size_bytes": 10, "mtime_ns": 20},
        }
        self.assertEqual(generator.compare_dota_versions(first, same), ("same", "Steam build ID"))
        self.assertEqual(generator.compare_dota_versions(first, changed), ("different", "Steam build ID"))
        self.assertTrue(generator.dota_changed_during_build(first, changed))

        first["steam_build_id"] = None
        changed["steam_build_id"] = None
        self.assertEqual(
            generator.compare_dota_versions(first, changed),
            ("same", "pak01_dir.vpk size and modification time"),
        )
        self.assertFalse(generator.dota_changed_during_build(first, changed))

    def test_history_is_append_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / generator.HISTORY_FILENAME
            history = generator.read_version_history(path)
            generator.append_version_history(path, history, {"built_at_utc": "first"})
            history = generator.read_version_history(path)
            generator.append_version_history(path, history, {"built_at_utc": "second"})
            recorded = generator.read_version_history(path)
            self.assertEqual(
                [entry["built_at_utc"] for entry in recorded["entries"]],
                ["first", "second"],
            )

    def test_history_write_failure_is_reported_as_a_nonfatal_warning(self):
        warnings = []
        with patch.object(generator, "append_version_history", side_effect=OSError("disk is read-only")):
            recorded = generator.safely_append_version_history(
                Path("history.json"),
                {"entries": []},
                {"built_at_utc": "now"},
                warning=warnings.append,
            )
        self.assertFalse(recorded)
        self.assertIn("Overrides were built", warnings[0])

    def test_status_reports_current_then_stale_after_build_id_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dota, manifest = self.make_dota(root)
            output = dota / f"game/dota_{generator.DEFAULT_LANGUAGE}"
            output.mkdir()
            archive = output / generator.VPK_ARCHIVE_CANDIDATES[0]
            archive.write_bytes(b"owned VPK fixture")
            generator.write_json(
                output / generator.MARKER_FILENAME,
                {
                    "kind": generator.MARKER_KIND,
                    "generator_version": generator.VERSION,
                    "deployment_mode": generator.VPK_DEPLOYMENT_MODE,
                    "language": generator.DEFAULT_LANGUAGE,
                    "generated_at_utc": "2026-08-20T00:00:00+00:00",
                    "dota_version": generator.capture_dota_version(dota),
                    "files": [archive.name],
                    "resources": [],
                    "archive_sha256": generator.sha256_file(archive),
                },
            )
            args = Namespace(dota=str(dota), language=generator.DEFAULT_LANGUAGE, json=False)
            output_text = io.StringIO()
            with redirect_stdout(output_text):
                self.assertEqual(generator.do_status(args), 0)
            self.assertIn("Status: CURRENT", output_text.getvalue())

            manifest.write_text(
                '"AppState" { "appid" "570" "buildid" "12345679" }\n',
                encoding="utf-8",
            )
            output_text = io.StringIO()
            with redirect_stdout(output_text):
                self.assertEqual(generator.do_status(args), 0)
            self.assertIn("Status: STALE", output_text.getvalue())

    def test_status_reports_broken_when_owned_vpk_is_modified(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dota, _manifest = self.make_dota(root)
            output = dota / f"game/dota_{generator.DEFAULT_LANGUAGE}"
            output.mkdir()
            archive = output / generator.VPK_ARCHIVE_CANDIDATES[0]
            archive.write_bytes(b"original archive")
            generator.write_json(
                output / generator.MARKER_FILENAME,
                {
                    "kind": generator.MARKER_KIND,
                    "generator_version": generator.VERSION,
                    "deployment_mode": generator.VPK_DEPLOYMENT_MODE,
                    "language": generator.DEFAULT_LANGUAGE,
                    "generated_at_utc": "2026-08-20T00:00:00+00:00",
                    "dota_version": generator.capture_dota_version(dota),
                    "files": [archive.name],
                    "resources": [],
                    "archive_sha256": generator.sha256_file(archive),
                },
            )
            archive.write_bytes(b"modified archive")

            result = generator.get_status(str(dota))
            self.assertEqual(result["status"], "broken")
            self.assertFalse(result["archive_valid"])

    def test_default_language_is_recognized_and_arbitrary_names_are_rejected(self):
        self.assertEqual(generator.validate_language("english"), generator.DEFAULT_LANGUAGE)
        self.assertEqual(generator.validate_language("Dutch"), generator.DEFAULT_LANGUAGE)
        with self.assertRaisesRegex(ValueError, "Dota-recognized"):
            generator.validate_language("defaultmodels")
        self.assertEqual(
            generator.validate_language("defaultmodels", allow_legacy=True),
            generator.LEGACY_LANGUAGE,
        )


class MappingTests(unittest.TestCase):
    def test_wearables_persona_and_entity_override_map_to_safe_defaults(self):
        default = item(
            1,
            slot="head",
            baseitem="1",
            model="models/heroes/test/test_head.vmdl",
        )
        cosmetic = item(
            2,
            slot="head",
            model="models/items/test/fancy_head.vmdl",
            nested_models=[("model_player", "models/items/test/fancy_head_style.vmdl")],
            visuals=[
                {
                    "type": "entity_model",
                    "asset": HERO,
                    "modifier": "models/items/test/arcana_hero.vmdl",
                }
            ],
        )
        persona = item(
            3,
            slot="persona_head",
            model="models/items/test/persona_head.vmdl",
        )
        plan = generator.build_plan(
            {},
            {record.item_id: record for record in (default, cosmetic, persona)},
            {HERO: "models/heroes/test/test.vmdl"},
            [],
        )
        by_target = {mapping.target: mapping for mapping in plan.mappings}
        self.assertEqual(
            by_target["models/items/test/fancy_head.vmdl"].source,
            "models/heroes/test/test_head.vmdl",
        )
        self.assertEqual(
            by_target["models/items/test/fancy_head_style.vmdl"].source,
            "models/heroes/test/test_head.vmdl",
        )
        self.assertEqual(
            by_target["models/items/test/persona_head.vmdl"].source,
            generator.INVISIBLE_MODEL,
        )
        self.assertEqual(
            by_target["models/items/test/arcana_hero.vmdl"].source,
            "models/heroes/test/test.vmdl",
        )
        self.assertEqual(
            by_target["models/items/test/fancy_head.vmdl"].category,
            generator.CATEGORY_SPECIAL_MODELS,
        )
        self.assertEqual(
            by_target["models/items/test/persona_head.vmdl"].category,
            generator.CATEGORY_PERSONA_MODELS,
        )

    def test_missing_slot_default_is_reported_instead_of_hidden(self):
        cosmetic = item(5, slot="weapon", model="models/items/test/weapon.vmdl")
        plan = generator.build_plan({}, {cosmetic.item_id: cosmetic}, {HERO: "models/heroes/test/test.vmdl"}, [])
        self.assertEqual(plan.mappings, [])
        self.assertEqual(plan.unresolved[0]["type"], "wearable")

    def test_integrated_default_slot_hides_cosmetic_model(self):
        default = item(1, slot="head", baseitem="1")
        cosmetic = item(2, slot="head", model="models/items/test/integrated_head.vmdl")

        plan = generator.build_plan(
            {},
            {record.item_id: record for record in (default, cosmetic)},
            {HERO: "models/heroes/test/test.vmdl"},
            [],
        )

        self.assertEqual(plan.mappings[0].source, generator.INVISIBLE_MODEL)
        self.assertEqual(plan.mappings[0].reason, "integrated hero-slot cosmetic hidden")
        self.assertEqual(plan.stats["integrated_slot_cosmetics_hidden"], 1)

    def test_bodygroup_wearable_uses_full_hero_fallback(self):
        default = item(1, slot="head", baseitem="1", model="models/heroes/test/head.vmdl")
        bodygroup = item(
            2,
            slot="head",
            model="models/items/test/bodygroup_head.vmdl",
            visuals=[
                {
                    "type": "bodygroup_visibility",
                    "asset": "models/heroes/test/test.vmdl",
                    "modifier": "head",
                    "value": "1",
                }
            ],
        )

        plan = generator.build_plan(
            {},
            {record.item_id: record for record in (default, bodygroup)},
            {HERO: "models/heroes/test/test.vmdl"},
            [],
        )

        self.assertEqual(len(plan.mappings), 1)
        self.assertEqual(plan.mappings[0].source, "models/heroes/test/test.vmdl")
        self.assertEqual(plan.mappings[0].target, "models/items/test/bodygroup_head.vmdl")
        self.assertFalse(plan.mappings[0].neutralize_bodygroup)
        self.assertEqual(
            plan.mappings[0].reason,
            "bodygroup wearable replaced with full hero fallback",
        )
        self.assertEqual(plan.stats["bodygroup_sensitive_models_skipped"], 0)
        self.assertEqual(plan.stats["bodygroup_hero_fallbacks"], 1)

    def test_integrated_bodygroup_slot_uses_full_hero_without_schema_reset(self):
        default = item(1, slot="head", baseitem="1")
        bodygroup = item(
            2,
            slot="head",
            model="models/items/test/integrated_bodygroup_head.vmdl",
            visuals=[
                {
                    "type": "bodygroup_visibility",
                    "asset": "models/heroes/test/test.vmdl",
                    "modifier": "head",
                    "value": "1",
                }
            ],
        )

        plan = generator.build_plan(
            {},
            {record.item_id: record for record in (default, bodygroup)},
            {HERO: "models/heroes/test/test.vmdl"},
            [],
        )

        self.assertEqual(plan.mappings[0].source, "models/heroes/test/test.vmdl")
        self.assertFalse(plan.mappings[0].neutralize_bodygroup)
        self.assertEqual(plan.stats["bodygroup_schema_items_reset"], 0)
        self.assertEqual(plan.stats["bodygroup_sensitive_models_skipped"], 0)

    def test_reviewed_whole_hero_wearable_uses_hero_default(self):
        default = item(1, slot="head", baseitem="1")
        whole_hero = item(
            34398,
            slot="head",
            model="models/items/test/whole_hero.vmdl",
        )

        plan = generator.build_plan(
            {},
            {record.item_id: record for record in (default, whole_hero)},
            {HERO: "models/heroes/test/test.vmdl"},
            [],
        )

        self.assertEqual(plan.mappings[0].source, "models/heroes/test/test.vmdl")
        self.assertEqual(
            plan.mappings[0].reason,
            "whole-hero wearable replaced with hero default",
        )

    def test_witch_doctor_integrated_back_uses_skeleton_compatible_hero(self):
        hero = "npc_dota_hero_witch_doctor"
        default = item(1, slot="back", hero=hero, baseitem="1")
        cosmetic = item(
            2,
            slot="back",
            hero=hero,
            model="models/items/witchdoctor/test_back.vmdl",
        )

        plan = generator.build_plan(
            {},
            {record.item_id: record for record in (default, cosmetic)},
            {hero: "models/heroes/witchdoctor/witchdoctor.vmdl"},
            [],
        )

        self.assertEqual(
            plan.mappings[0].source,
            "models/heroes/witchdoctor/witchdoctor.vmdl",
        )
        self.assertEqual(plan.stats["full_hero_wearable_fallbacks"], 1)

    def test_retired_duplicate_does_not_override_current_slot_default(self):
        default_weapon = item(
            1,
            slot="weapon",
            baseitem="1",
            model="models/heroes/test/staff.vmdl",
        )
        default_mount = item(
            2,
            slot="mount",
            baseitem="1",
            model="models/heroes/test/mount.vmdl",
        )
        expired = item(
            3,
            name="Test Mount Expired",
            slot="weapon",
            model="models/items/test/shared_mount.vmdl",
        )
        current = item(
            4,
            name="Test Mount",
            slot="mount",
            model="models/items/test/shared_mount.vmdl",
        )

        plan = generator.build_plan(
            {},
            {
                record.item_id: record
                for record in (default_weapon, default_mount, expired, current)
            },
            {HERO: "models/heroes/test/test.vmdl"},
            [],
        )

        self.assertEqual(len(plan.mappings), 1)
        self.assertEqual(plan.mappings[0].source, "models/heroes/test/mount.vmdl")
        self.assertEqual(plan.stats["retired_items_skipped"], 1)

    def test_pet_models_and_pickup_items_are_hidden_with_parent_category(self):
        cosmetic = item(
            2,
            slot="back",
            model="models/items/test/arcana_back.vmdl",
            visuals=[
                {
                    "type": "entity_model",
                    "asset": HERO,
                    "modifier": "models/items/test/arcana_hero.vmdl",
                },
                {
                    "type": "pet",
                    "asset": "models/pets/test/wolf.vmdl",
                    "pickup_item": "models/pets/items/bone.vmdl",
                },
            ],
        )

        plan = generator.build_plan(
            {},
            {cosmetic.item_id: cosmetic},
            {HERO: "models/heroes/test/test.vmdl"},
            [],
        )
        by_target = {mapping.target: mapping for mapping in plan.mappings}

        self.assertEqual(by_target["models/pets/test/wolf.vmdl"].source, generator.INVISIBLE_MODEL)
        self.assertEqual(by_target["models/pets/items/bone.vmdl"].source, generator.INVISIBLE_MODEL)
        self.assertEqual(
            by_target["models/pets/test/wolf.vmdl"].category,
            generator.CATEGORY_SPECIAL_MODELS,
        )
        self.assertEqual(plan.stats["pet_models_hidden"], 2)

    def test_bodygroup_schema_patch_is_scoped_to_selected_items(self):
        schema = '''"items_game"
{
    "items"
    {
        "2"
        {
            "visuals"
            {
                "asset_modifier"
                {
                    "type" "bodygroup_visibility"
                    "asset" "models/heroes/test/test.vmdl"
                    "modifier" "head"
                    "value" "1"
                }
            }
        }
        "3"
        {
            "visuals"
            {
                "asset_modifier"
                {
                    "type" "bodygroup_visibility"
                    "value" "1"
                }
            }
        }
    }
    "attribute_controlled_attached_particles" { }
}
'''

        patched, counts = generator.neutralize_item_bodygroups(schema, {"2"})

        self.assertEqual(counts, {"2": 1})
        selected = patched[patched.index('"2"'):patched.index('"3"')]
        unselected = patched[patched.index('"3"'):]
        self.assertIn('"value" "0"', selected)
        self.assertIn('"value" "1"', unselected)

    def test_alternate_skin_material_with_same_filename_is_redirected_to_base(self):
        mapping = generator.Mapping(
            source="models/heroes/test/default_head.vmdl",
            target="models/items/test/skinned_head.vmdl",
            reason="wearable replaced with slot default",
            category=generator.CATEGORY_STANDARD_WEARABLES,
            item_id="2",
            hero=HERO,
            slot="head",
            neutralize_model_skin=True,
            required_material_groups=2,
        )
        plan = generator.Plan(
            mappings=[mapping],
            unresolved=[],
            stats={
                "resource_overrides": 1,
                "mapping_conflicts": 0,
                "unresolved": 0,
            },
        )
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            model_path = cache / generator.compiled_model_path(mapping.source)
            model_path.parent.mkdir(parents=True)
            model_path.write_bytes(
                b"materials/models/heroes/test/head.vmat\x00"
                b"materials/models/items/event/test/head.vmat\x00"
            )
            adjusted = generator.apply_model_skin_material_fallbacks(plan, cache)

        materials = [
            candidate
            for candidate in adjusted.mappings
            if candidate.resource_type == generator.RESOURCE_MATERIAL
        ]
        self.assertEqual(len(materials), 1)
        self.assertEqual(materials[0].source, "materials/models/heroes/test/head.vmat")
        self.assertEqual(materials[0].target, "materials/models/items/event/test/head.vmat")
        self.assertEqual(adjusted.stats["material_overrides"], 1)
        self.assertEqual(adjusted.stats["alternate_skin_group_patch_targets"], 1)

    def test_single_material_skin_keeps_a_distinct_default_model_replacement(self):
        mapping = generator.Mapping(
            source="models/heroes/test/default_head.vmdl",
            target="models/items/test/skinned_head.vmdl",
            reason="wearable replaced with slot default",
            category=generator.CATEGORY_STANDARD_WEARABLES,
            resource_type=generator.RESOURCE_MODEL,
            item_id="2",
            hero=HERO,
            slot="head",
            neutralize_model_skin=True,
            required_material_groups=2,
        )
        plan = generator.Plan(
            mappings=[mapping],
            unresolved=[],
            stats={"resource_overrides": 1, "mapping_conflicts": 0, "unresolved": 0},
        )
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            model_path = cache / generator.compiled_model_path(mapping.source)
            model_path.parent.mkdir(parents=True)
            model_path.write_bytes(b"materials/models/heroes/test/head.vmat\x00")
            adjusted = generator.apply_model_skin_material_fallbacks(plan, cache)

        self.assertEqual(adjusted.mappings, [mapping])
        self.assertEqual(adjusted.stats["alternate_skin_models_skipped"], 0)
        self.assertEqual(adjusted.stats["alternate_skin_material_unresolved"], 0)
        self.assertEqual(adjusted.stats["alternate_skin_material_passthrough_models"], 1)
        self.assertEqual(adjusted.stats["alternate_skin_group_patch_targets"], 1)
        self.assertEqual(adjusted.unresolved, [])

    def test_skin_only_item_can_generate_material_override_without_a_model_copy(self):
        default = item(
            1,
            slot="head",
            baseitem="1",
            model="models/heroes/test/default_head.vmdl",
        )
        skin_only = item(
            2,
            slot="head",
            model="models/heroes/test/default_head.vmdl",
            has_nondefault_skin=True,
        )
        plan = generator.build_plan(
            {},
            {record.item_id: record for record in (default, skin_only)},
            {HERO: "models/heroes/test/test.vmdl"},
            [],
        )
        self.assertEqual(len(plan.mappings), 1)
        self.assertEqual(plan.mappings[0].source, plan.mappings[0].target)

        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            model_path = cache / generator.compiled_model_path(plan.mappings[0].source)
            model_path.parent.mkdir(parents=True)
            model_path.write_bytes(
                b"materials/models/heroes/test/head_color.vmat\x00"
                b"materials/models/items/heroes/fall20/test/test_fall20_head_color.vmat\x00"
            )
            adjusted = generator.apply_model_skin_material_fallbacks(plan, cache)

        self.assertEqual(len(adjusted.mappings), 1)
        self.assertEqual(adjusted.mappings[0].resource_type, generator.RESOURCE_MATERIAL)
        self.assertEqual(
            adjusted.mappings[0].source,
            "materials/models/heroes/test/head_color.vmat",
        )

    def test_unit_and_base_item_entity_models_restore_summons(self):
        default = item(
            1,
            slot="summon",
            baseitem="1",
            visuals=[
                {
                    "type": "entity_model",
                    "asset": "npc_dota_test_ghost",
                    "modifier": "models/heroes/test/default_ghost.vmdl",
                }
            ],
        )
        cosmetic = item(
            2,
            slot="summon",
            visuals=[
                {
                    "type": "entity_model",
                    "asset": "npc_dota_test_ghost",
                    "modifier": "models/items/test/cosmetic_ghost.vmdl",
                },
                {
                    "type": "entity_clientside_model",
                    "asset": "npc_dota_test_ward",
                    "modifier": "models/items/test/cosmetic_ward.vmdl",
                },
            ],
        )

        plan = generator.build_plan(
            {},
            {record.item_id: record for record in (default, cosmetic)},
            {HERO: "models/heroes/test/test.vmdl"},
            [],
            unit_models={"npc_dota_test_ward_2": "models/heroes/test/default_ward.vmdl"},
        )
        by_target = {mapping.target: mapping for mapping in plan.mappings}
        self.assertEqual(
            by_target["models/items/test/cosmetic_ghost.vmdl"].source,
            "models/heroes/test/default_ghost.vmdl",
        )
        self.assertEqual(
            by_target["models/items/test/cosmetic_ward.vmdl"].source,
            "models/heroes/test/default_ward.vmdl",
        )
        self.assertEqual(plan.stats["entity_default_replacements"], 2)

    def test_unit_model_loader_resolves_include_inheritance(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "npc_units.txt"
            path.write_text(
                '''"DOTAUnits"
{
    "npc_dota_test_ward_1" { "Model" "models/heroes/test/default_ward.vmdl" }
    "npc_dota_test_ward_2" { "include_keys_from" "npc_dota_test_ward_1" }
}
''',
                encoding="utf-8",
            )
            models = generator.load_unit_models(path)

        self.assertEqual(
            models["npc_dota_test_ward_2"],
            "models/heroes/test/default_ward.vmdl",
        )

    def test_hero_model_loader_exposes_growth_variants_for_entity_rules(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "npc_heroes.txt"
            path.write_text(
                '''"DOTAHeroes"
{
    "npc_dota_hero_base" { }
    "npc_dota_hero_tiny"
    {
        "Model" "models/heroes/tiny/tiny_01.vmdl"
        "Model1" "models/heroes/tiny/tiny_02.vmdl"
        "Model2" "models/heroes/tiny/tiny_03.vmdl"
        "Model3" "models/heroes/tiny/tiny_04.vmdl"
    }
}
''',
                encoding="utf-8",
            )
            models = generator.load_hero_models(path)

        self.assertEqual(models["npc_dota_hero_tiny"], "models/heroes/tiny/tiny_01.vmdl")
        for index in range(4):
            self.assertEqual(
                models[f"npc_dota_hero_tiny_variant_{index}"],
                f"models/heroes/tiny/tiny_0{index + 1}.vmdl",
            )

        cosmetic = item(
            2,
            slot="armor",
            visuals=[
                {
                    "type": "entity_model",
                    "asset": "npc_dota_hero_tiny_variant_2",
                    "modifier": "models/items/tiny/castle_t3.vmdl",
                }
            ],
        )
        plan = generator.build_plan(
            {},
            {cosmetic.item_id: cosmetic},
            models,
            [],
        )
        self.assertEqual(plan.mappings[0].source, "models/heroes/tiny/tiny_03.vmdl")

    def test_items_parser_flags_nondefault_visual_skin(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "items_game.txt"
            path.write_text(
                '''"items_game"
{
    "prefabs" { }
    "items"
    {
        "1"
        {
            "name" "skinned"
            "item_slot" "head"
            "used_by_heroes" { "npc_dota_hero_test" "1" }
            "model_player" "models/items/test/skinned.vmdl"
            "visuals" { "skin" "1" }
        }
    }
}
''',
                encoding="utf-8",
            )
            _prefabs, records, _global = generator.load_items_game(path)

        self.assertTrue(records["1"].has_nondefault_skin)
        self.assertEqual(records["1"].required_material_groups, 2)

    def test_items_parser_finds_nested_style_skins_and_bundle_members(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "items_game.txt"
            path.write_text(
                '''"items_game"
{
    "prefabs" { }
    "items"
    {
        "1"
        {
            "name" "Styled Head"
            "item_slot" "head"
            "used_by_heroes" { "npc_dota_hero_test" "1" }
            "model_player" "models/items/test/styled.vmdl"
            "styles" { "1" { "skin" "2" } }
        }
        "2"
        {
            "name" "Styled Set"
            "bundle" { "Styled Head" "1" "Styled Back" "1" }
        }
    }
}
''',
                encoding="utf-8",
            )
            _prefabs, records, _global = generator.load_items_game(path)

        self.assertTrue(records["1"].has_nondefault_skin)
        self.assertEqual(records["1"].required_material_groups, 3)
        self.assertEqual(records["2"].bundle_members, ("Styled Head", "Styled Back"))

    def test_bundle_skin_sensitivity_stays_scoped_to_the_actual_item(self):
        default_head = item(
            1,
            name="Default Head",
            slot="head",
            baseitem="1",
            model="models/heroes/test/head.vmdl",
        )
        default_back = item(
            2,
            name="Default Back",
            slot="back",
            baseitem="1",
            model="models/heroes/test/back.vmdl",
        )
        styled_head = item(
            3,
            name="Styled Head",
            slot="head",
            model="models/items/test/styled_head.vmdl",
            has_nondefault_skin=True,
        )
        styled_back = item(
            4,
            name="Styled Back",
            slot="back",
            model="models/items/test/styled_back.vmdl",
        )
        bundle = item(
            5,
            name="Styled Set",
            slot="",
            hero=None,
            bundle_members=("Styled Head", "Styled Back"),
        )

        plan = generator.build_plan(
            {},
            {
                record.item_id: record
                for record in (default_head, default_back, styled_head, styled_back, bundle)
            },
            {HERO: "models/heroes/test/test.vmdl"},
            [],
        )

        by_target = {mapping.target: mapping for mapping in plan.mappings}
        self.assertTrue(by_target["models/items/test/styled_head.vmdl"].neutralize_model_skin)
        self.assertFalse(by_target["models/items/test/styled_back.vmdl"].neutralize_model_skin)

    def test_schema_categories_are_propagated_and_filterable(self):
        default = item(
            1,
            slot="head",
            baseitem="1",
            model="models/heroes/test/default_head.vmdl",
        )
        misleading_standard = item(
            2,
            slot="head",
            model="models/items/test/arcana_named_but_standard.vmdl",
        )
        special = item(
            3,
            slot="head",
            model="models/items/test/plain_special_wearable.vmdl",
            visuals=[
                {
                    "type": "entity_model",
                    "asset": HERO,
                    "modifier": "models/items/test/plain_hero_swap.vmdl",
                }
            ],
        )
        persona = item(
            4,
            slot="persona_head",
            model="models/items/test/persona_piece.vmdl",
            visuals=[
                {
                    "type": "entity_model",
                    "asset": HERO,
                    "modifier": "models/items/test/persona_hero_swap.vmdl",
                }
            ],
        )
        additional = item(
            5,
            slot="head",
            visuals=[
                {
                    "type": "additional_wearable",
                    "asset": "models/items/test/extra_attachment.vmdl",
                }
            ],
        )
        records = {record.item_id: record for record in (default, misleading_standard, special, persona, additional)}
        hero_models = {HERO: "models/heroes/test/test.vmdl"}
        plan = generator.build_plan({}, records, hero_models, [])
        categories = {mapping.target: mapping.category for mapping in plan.mappings}

        self.assertEqual(
            categories["models/items/test/arcana_named_but_standard.vmdl"],
            generator.CATEGORY_STANDARD_WEARABLES,
        )
        self.assertEqual(
            categories["models/items/test/plain_special_wearable.vmdl"],
            generator.CATEGORY_SPECIAL_MODELS,
        )
        self.assertEqual(
            categories["models/items/test/plain_hero_swap.vmdl"],
            generator.CATEGORY_SPECIAL_MODELS,
        )
        self.assertEqual(
            categories["models/items/test/persona_piece.vmdl"],
            generator.CATEGORY_PERSONA_MODELS,
        )
        self.assertEqual(
            categories["models/items/test/persona_hero_swap.vmdl"],
            generator.CATEGORY_PERSONA_MODELS,
        )
        self.assertEqual(
            categories["models/items/test/extra_attachment.vmdl"],
            generator.CATEGORY_ADDITIONAL_WEARABLES,
        )

        standard_only = generator.build_plan(
            {},
            records,
            hero_models,
            [],
            enabled_categories={generator.CATEGORY_STANDARD_WEARABLES},
        )
        self.assertEqual(
            {mapping.target for mapping in standard_only.mappings},
            {"models/items/test/arcana_named_but_standard.vmdl"},
        )

    def test_model_refit_uses_inferred_default_even_when_standard_category_is_disabled(self):
        default = item(
            1,
            slot="head",
            baseitem="1",
            model="models/heroes/test/default_head.vmdl",
        )
        standard = item(
            2,
            slot="head",
            model="models/items/test/fancy_head.vmdl",
        )
        special = item(
            3,
            slot="head",
            visuals=[
                {
                    "type": "model",
                    "asset": "models/items/test/fancy_head.vmdl",
                    "modifier": "models/items/test/fancy_head_arcana_refit.vmdl",
                }
            ],
        )
        records = {record.item_id: record for record in (default, standard, special)}

        plan = generator.build_plan(
            {},
            records,
            {HERO: "models/heroes/test/test.vmdl"},
            [],
            enabled_categories={generator.CATEGORY_SPECIAL_MODELS},
        )

        self.assertEqual(len(plan.mappings), 1)
        mapping = plan.mappings[0]
        self.assertEqual(mapping.target, "models/items/test/fancy_head_arcana_refit.vmdl")
        self.assertEqual(mapping.source, "models/heroes/test/default_head.vmdl")
        self.assertEqual(mapping.reason, "model asset override replaced with inferred default")
        self.assertEqual(plan.stats["model_asset_defaults_inferred"], 1)
        self.assertEqual(plan.stats["model_asset_original_fallbacks"], 0)

    def test_model_refit_with_unknown_asset_keeps_original_fallback(self):
        special = item(
            4,
            slot="head",
            visuals=[
                {
                    "type": "model",
                    "asset": "models/items/test/unindexed_original.vmdl",
                    "modifier": "models/items/test/unindexed_refit.vmdl",
                }
            ],
        )

        plan = generator.build_plan(
            {},
            {special.item_id: special},
            {HERO: "models/heroes/test/test.vmdl"},
            [],
        )

        self.assertEqual(plan.mappings[0].source, "models/items/test/unindexed_original.vmdl")
        self.assertEqual(plan.mappings[0].reason, "model asset override replaced with original")
        self.assertEqual(plan.stats["model_asset_original_fallbacks"], 1)

    def test_known_stale_arcana_refit_uses_reviewed_default(self):
        special = item(
            12930,
            slot="back",
            visuals=[
                {
                    "type": "model",
                    "asset": (
                        "models/items/queenofpain/tpl_shoulder_lvl1/"
                        "tpl_shoulder_lvl2.vmdl"
                    ),
                    "modifier": (
                        "models/items/queenofpain/tpl_shoulder_lvl1/"
                        "tpl_shoulder_lvl2_arcrefit.vmdl"
                    ),
                }
            ],
        )

        plan = generator.build_plan(
            {},
            {special.item_id: special},
            {HERO: "models/heroes/test/test.vmdl"},
            [],
        )

        self.assertEqual(plan.mappings[0].source, "models/heroes/queenofpain/shoulders.vmdl")
        self.assertEqual(plan.stats["model_asset_reviewed_exceptions"], 1)

    def test_particle_defaults_additive_effects_snapshots_and_chains_are_replaced_safely(self):
        default = item(
            1,
            slot="head",
            baseitem="1",
            visuals=[
                {
                    "type": "particle_create",
                    "asset": "particles/units/heroes/test/default_ambient.vpcf",
                }
            ],
        )
        cosmetic = item(
            2,
            slot="head",
            visuals=[
                {
                    "type": "particle",
                    "asset": "particles/units/heroes/test/default_attack.vpcf",
                    "modifier": "particles/econ/items/test/cosmetic_attack.vpcf",
                },
                {
                    "type": "particle_combined",
                    "asset": "particles/econ/items/test/cosmetic_attack.vpcf",
                    "modifier": "particles/econ/items/test/cosmetic_attack_combo.vpcf",
                },
                {
                    "type": "particle_clientside",
                    "asset": "particles/units/heroes/test/default_client.vpcf",
                    "modifier": "particles/econ/items/test/cosmetic_client.vpcf",
                },
                {
                    "type": "particle_create",
                    "modifier": "particles/units/heroes/test/default_ambient.vpcf",
                },
                {
                    "type": "particle_create",
                    "modifier": "particles/econ/items/test/cosmetic_ambient.vpcf",
                },
                {
                    "type": "particle_create",
                    "modifier": "particles/ui/shared_status.vpcf",
                },
                {
                    "type": "particle_snapshot",
                    "asset": "particles/units/heroes/test/default_pose.vsnap",
                    "modifier": "particles/econ/items/test/cosmetic_pose.vsnap",
                },
                {
                    "type": "particle_control_point",
                    "asset": "particles/econ/items/test/cosmetic_attack.vpcf",
                },
            ],
        )
        global_visuals = [
            {
                "type": "particle",
                "asset": "particles/units/heroes/test/default_global.vpcf",
                "modifier": "particles/econ/items/test/cosmetic_global.vpcf",
            }
        ]

        plan = generator.build_plan(
            {},
            {record.item_id: record for record in (default, cosmetic)},
            {},
            global_visuals,
            enabled_categories={generator.CATEGORY_PARTICLE_EFFECTS},
        )
        by_target = {mapping.target: mapping for mapping in plan.mappings}

        self.assertEqual(
            by_target["particles/econ/items/test/cosmetic_attack.vpcf"].source,
            "particles/units/heroes/test/default_attack.vpcf",
        )
        self.assertEqual(
            by_target["particles/econ/items/test/cosmetic_attack_combo.vpcf"].source,
            "particles/units/heroes/test/default_attack.vpcf",
        )
        self.assertEqual(
            by_target["particles/econ/items/test/cosmetic_client.vpcf"].source,
            "particles/units/heroes/test/default_client.vpcf",
        )
        self.assertEqual(
            by_target["particles/econ/items/test/cosmetic_ambient.vpcf"].source,
            generator.NEUTRAL_PARTICLE,
        )
        self.assertNotIn("particles/units/heroes/test/default_ambient.vpcf", by_target)
        self.assertNotIn("particles/ui/shared_status.vpcf", by_target)
        self.assertEqual(
            by_target["particles/econ/items/test/cosmetic_pose.vsnap"].source,
            "particles/units/heroes/test/default_pose.vsnap",
        )
        self.assertEqual(
            by_target["particles/econ/items/test/cosmetic_pose.vsnap"].resource_type,
            generator.RESOURCE_SNAPSHOT,
        )
        self.assertEqual(
            by_target["particles/econ/items/test/cosmetic_global.vpcf"].source,
            "particles/units/heroes/test/default_global.vpcf",
        )
        self.assertTrue(
            all(mapping.category == generator.CATEGORY_PARTICLE_EFFECTS for mapping in plan.mappings)
        )
        self.assertEqual(plan.stats["particle_default_creates_preserved"], 1)
        self.assertEqual(plan.stats["particle_additive_shared_paths_skipped"], 1)
        self.assertEqual(plan.stats["particle_rules_unsupported"], 1)
        self.assertEqual(plan.stats["particle_defaults_resolved_transitively"], 1)

        models_only = generator.build_plan(
            {},
            {record.item_id: record for record in (default, cosmetic)},
            {},
            global_visuals,
            enabled_categories={generator.CATEGORY_STANDARD_WEARABLES},
        )
        self.assertEqual(models_only.mappings, [])

    def test_cyclic_particle_replacements_are_reported_and_skipped(self):
        cosmetic = item(
            2,
            slot="head",
            visuals=[
                {
                    "type": "particle",
                    "asset": "particles/econ/items/test/a.vpcf",
                    "modifier": "particles/econ/items/test/b.vpcf",
                },
                {
                    "type": "particle",
                    "asset": "particles/econ/items/test/b.vpcf",
                    "modifier": "particles/econ/items/test/a.vpcf",
                },
            ],
        )
        plan = generator.build_plan(
            {},
            {cosmetic.item_id: cosmetic},
            {},
            [],
            enabled_categories={generator.CATEGORY_PARTICLE_EFFECTS},
        )
        self.assertEqual(plan.mappings, [])
        self.assertEqual(plan.stats["particle_resolution_cycles"], 2)
        self.assertEqual(
            {entry["type"] for entry in plan.unresolved},
            {"particle_mapping_cycle"},
        )

    def test_cosmetic_create_restores_the_best_matching_suppressed_default_particle(self):
        cosmetic = item(
            2,
            slot="offhand",
            visuals=[
                {
                    "type": "particle",
                    "asset": "particles/units/heroes/test/test_lantern_ambient.vpcf",
                    "modifier": generator.NEUTRAL_PARTICLE,
                },
                {
                    "type": "particle",
                    "asset": "particles/units/heroes/test/test_wisp_ambient.vpcf",
                    "modifier": generator.NEUTRAL_PARTICLE,
                },
                {
                    "type": "particle_create",
                    "modifier": "particles/econ/items/test/ether_lantern_ambient.vpcf",
                },
            ],
        )

        plan = generator.build_plan(
            {},
            {cosmetic.item_id: cosmetic},
            {},
            [],
            enabled_categories={generator.CATEGORY_PARTICLE_EFFECTS},
        )

        self.assertEqual(len(plan.mappings), 1)
        self.assertEqual(
            plan.mappings[0].source,
            "particles/units/heroes/test/test_lantern_ambient.vpcf",
        )
        self.assertEqual(
            plan.mappings[0].reason,
            "cosmetic particle restored from suppressed default",
        )
        self.assertEqual(plan.stats["particle_suppressed_defaults_restored"], 1)

    def test_cosmetic_created_weapon_effects_restore_slot_default_particles(self):
        default = item(
            1,
            slot="weapon",
            baseitem="1",
            model="models/heroes/test/default_weapon.vmdl",
            visuals=[
                {
                    "type": "particle_create",
                    "modifier": "particles/units/heroes/test/default_sword.vpcf",
                },
                {
                    "type": "particle_create",
                    "modifier": "particles/units/heroes/test/default_sword_blade.vpcf",
                },
            ],
        )
        cosmetic = item(
            2,
            slot="weapon",
            model="models/items/test/cosmetic_weapon.vmdl",
            visuals=[
                {
                    "type": "particle_create",
                    "modifier": "particles/econ/items/test/cosmetic_sword.vpcf",
                },
                {
                    "type": "particle_create",
                    "modifier": "particles/econ/items/test/cosmetic_sword_blade.vpcf",
                },
            ],
        )

        plan = generator.build_plan(
            {},
            {record.item_id: record for record in (default, cosmetic)},
            {HERO: "models/heroes/test/test.vmdl"},
            [],
        )
        particles = {
            mapping.target: mapping.source
            for mapping in plan.mappings
            if mapping.resource_type == generator.RESOURCE_PARTICLE
        }

        self.assertEqual(
            particles["particles/econ/items/test/cosmetic_sword.vpcf"],
            "particles/units/heroes/test/default_sword.vpcf",
        )
        self.assertEqual(
            particles["particles/econ/items/test/cosmetic_sword_blade.vpcf"],
            "particles/units/heroes/test/default_sword_blade.vpcf",
        )

    def test_missing_default_particle_uses_neutral_but_missing_snapshots_do_not(self):
        plan = generator.Plan(
            mappings=[
                generator.Mapping(
                    source="particles/removed/default.vpcf",
                    target="particles/econ/items/test/cosmetic.vpcf",
                    reason="particle override replaced with schema default",
                    category=generator.CATEGORY_PARTICLE_EFFECTS,
                    resource_type=generator.RESOURCE_PARTICLE,
                ),
                generator.Mapping(
                    source="particles/ability_modifier_placeholder/test_passive.vpcf",
                    target="particles/econ/items/test/cosmetic_placeholder.vpcf",
                    reason="particle override replaced with schema default",
                    category=generator.CATEGORY_PARTICLE_EFFECTS,
                    resource_type=generator.RESOURCE_PARTICLE,
                ),
                generator.Mapping(
                    source="particles/removed/default.vsnap",
                    target="particles/econ/items/test/cosmetic.vsnap",
                    reason="particle snapshot replaced with schema default",
                    category=generator.CATEGORY_PARTICLE_EFFECTS,
                    resource_type=generator.RESOURCE_SNAPSHOT,
                ),
            ],
            unresolved=[],
            stats={"particle_missing_defaults_hidden": 0},
        )
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            neutral = cache / generator.compiled_particle_path(generator.NEUTRAL_PARTICLE)
            neutral.parent.mkdir(parents=True)
            neutral.write_bytes(b"neutral")

            adjusted = generator.apply_missing_particle_fallbacks(plan, cache)

        self.assertEqual(adjusted.mappings[0].source, generator.NEUTRAL_PARTICLE)
        self.assertIn("neutral fallback", adjusted.mappings[0].reason)
        self.assertEqual(adjusted.mappings[1].source, generator.NEUTRAL_PARTICLE)
        self.assertIn("by design", adjusted.mappings[1].reason)
        self.assertEqual(adjusted.mappings[2].source, "particles/removed/default.vsnap")
        self.assertEqual(adjusted.stats["particle_missing_defaults_hidden"], 2)
        self.assertEqual(adjusted.stats["particle_virtual_defaults_neutralized"], 1)
        self.assertEqual(adjusted.stats["particle_unknown_defaults_neutralized"], 1)

    def test_reviewed_stale_particle_paths_use_current_defaults(self):
        reviewed = generator.PARTICLE_DEFAULT_PATH_EXCEPTIONS
        cosmetic = item(
            2,
            slot="weapon",
            visuals=[
                {
                    "type": "particle",
                    "asset": stale,
                    "modifier": f"particles/econ/items/test/cosmetic_{index}.vpcf",
                }
                for index, stale in enumerate(reviewed)
            ],
        )

        plan = generator.build_plan(
            {},
            {cosmetic.item_id: cosmetic},
            {},
            [],
            enabled_categories={generator.CATEGORY_PARTICLE_EFFECTS},
        )

        self.assertEqual(
            {mapping.source for mapping in plan.mappings},
            set(reviewed.values()),
        )
        self.assertEqual(
            plan.stats["particle_reviewed_default_fallbacks"],
            len(reviewed),
        )


class GuiViewModelTests(unittest.TestCase):
    def test_settings_round_trip_and_corruption_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / disabler_gui.SETTINGS_FILENAME
            disabler_gui.save_ui_settings(
                "D:/Steam/dota 2 beta",
                {generator.CATEGORY_PERSONA_MODELS},
                path,
                language="finnish",
            )
            self.assertEqual(
                disabler_gui.load_ui_settings(path),
                {
                    "format_version": disabler_gui.SETTINGS_FORMAT_VERSION,
                    "dota_path": "D:/Steam/dota 2 beta",
                    "enabled_categories": [generator.CATEGORY_PERSONA_MODELS],
                    "language": "finnish",
                },
            )
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["language"] = "defaultmodels"
            path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(
                disabler_gui.load_ui_settings(path)["language"],
                generator.DEFAULT_LANGUAGE,
            )
            path.write_text("not json", encoding="utf-8")
            fallback = disabler_gui.load_ui_settings(path)
            self.assertEqual(set(fallback["enabled_categories"]), generator.DEFAULT_CATEGORIES)
            self.assertEqual(fallback["language"], generator.DEFAULT_LANGUAGE)

    def test_language_choices_cover_every_recognized_mount_with_dutch_first(self):
        self.assertEqual(
            set(disabler_gui.LANGUAGE_LABEL_TO_CODE.values()),
            generator.RECOGNIZED_LANGUAGES,
        )
        self.assertEqual(
            disabler_gui.LANGUAGE_LABEL_TO_CODE[disabler_gui.LANGUAGE_LABELS[0]],
            generator.DEFAULT_LANGUAGE,
        )

    def test_statuses_map_to_clear_ui_actions(self):
        current = disabler_gui.status_presentation({"status": "current"})
        stale = disabler_gui.status_presentation({"status": "stale"})
        not_built = disabler_gui.status_presentation({"status": "not_built"})
        unknown = disabler_gui.status_presentation({"status": "unknown"})
        legacy = disabler_gui.status_presentation({"status": "legacy"})
        broken = disabler_gui.status_presentation({"status": "broken"})
        self.assertEqual(current["badge"], "CURRENT")
        self.assertIn("Installed Build", stale["action"])
        self.assertEqual(not_built["action"], "Build Overrides")
        self.assertEqual(unknown["badge"], "CHECK NEEDED")
        self.assertEqual(legacy["badge"], "REBUILD REQUIRED")
        self.assertEqual(broken["action"], "Repair Overrides")

        pending = disabler_gui.status_presentation(
            {
                "status": "current",
                "enabled_categories": [generator.CATEGORY_STANDARD_WEARABLES],
            },
            {generator.CATEGORY_PERSONA_MODELS},
        )
        self.assertEqual(pending["badge"], "CHANGES PENDING")
        self.assertEqual(pending["action"], "Apply Selection")

        stale_with_selection_changes = disabler_gui.status_presentation(
            {
                "status": "stale",
                "enabled_categories": [generator.CATEGORY_STANDARD_WEARABLES],
            },
            {generator.CATEGORY_PERSONA_MODELS},
        )
        self.assertEqual(stale_with_selection_changes["badge"], "UPDATE FOUND")

    def test_feature_tiles_group_internal_categories_without_hiding_personas(self):
        self.assertEqual(
            {
                category
                for feature in disabler_gui.FEATURES
                for category in feature["categories"]
            },
            generator.DEFAULT_CATEGORIES,
        )
        self.assertEqual(len(disabler_gui.FEATURES), 4)
        wearables = next(
            feature
            for feature in disabler_gui.FEATURES
            if feature["key"] == "wearables_attachments"
        )
        self.assertEqual(
            set(wearables["categories"]),
            {
                generator.CATEGORY_STANDARD_WEARABLES,
                generator.CATEGORY_ADDITIONAL_WEARABLES,
            },
        )
        personas = next(
            feature
            for feature in disabler_gui.FEATURES
            if feature["key"] == "persona_models"
        )
        self.assertEqual(personas["categories"], (generator.CATEGORY_PERSONA_MODELS,))
        self.assertEqual(personas["tag"], "EXPERIMENTAL")
        self.assertFalse(hasattr(disabler_gui, "PLANNED_FEATURES"))

    def test_status_is_only_reused_for_the_same_path(self):
        result = {"dota_path": str(Path("D:/Steam/dota 2 beta"))}
        self.assertTrue(disabler_gui.status_matches_path(result, "D:/Steam/dota 2 beta"))
        self.assertFalse(disabler_gui.status_matches_path(result, "E:/Other/dota 2 beta"))

    def test_detected_path_survives_a_status_validation_error(self):
        dota = Path("D:/Steam/dota 2 beta")
        with patch.object(disabler_gui.engine, "get_status", side_effect=generator.UnsafeOutputError("unowned")):
            status, error = disabler_gui.try_get_status(dota, "finnish")
        self.assertIsNone(status)
        self.assertIsInstance(error, generator.UnsafeOutputError)


class ModelPatcherDiscoveryTests(unittest.TestCase):
    def test_current_model_patcher_version_is_accepted(self):
        process = subprocess.CompletedProcess(
            [],
            0,
            stdout=(
                f"Dota2ModelSkinPatcher {model_patcher.MODEL_PATCHER_VERSION} "
                "(ValveResourceFormat 15.0.4937)\n"
            ),
            stderr="",
        )
        with patch.object(model_patcher, "run", return_value=process):
            model_patcher.validate_model_patcher(Path("patcher"))

    def test_stale_model_patcher_version_is_rejected(self):
        process = subprocess.CompletedProcess(
            [],
            0,
            stdout="Dota2ModelSkinPatcher 0.1.0 (ValveResourceFormat 15.0.4937)\n",
            stderr="",
        )
        with (
            patch.object(model_patcher, "run", return_value=process),
            self.assertRaisesRegex(
                generator.GeneratorError,
                r"Expected 0\.1\.1.*0\.1\.0",
            ),
        ):
            model_patcher.validate_model_patcher(Path("patcher"))


class PathSafetyTests(unittest.TestCase):
    def test_compiled_model_path_rejects_traversal(self):
        with self.assertRaises(ValueError):
            generator.compiled_model_path("../outside.vmdl")

    def test_compiled_model_path_rejects_absolute_paths(self):
        with self.assertRaises(ValueError):
            generator.compiled_model_path("/outside.vmdl")

    def test_compiled_particle_paths_are_canonical_and_reject_unsafe_inputs(self):
        self.assertEqual(
            generator.compiled_override_path("particles/econ/test.vpcf"),
            "particles/econ/test.vpcf_c",
        )
        self.assertEqual(
            generator.compiled_override_path("particles/econ/test.vsnap"),
            "particles/econ/test.vsnap_c",
        )
        with self.assertRaises(ValueError):
            generator.compiled_particle_path("../outside.vpcf")
        with self.assertRaises(ValueError):
            generator.compiled_particle_snapshot_path("C:/outside.vsnap")

    def test_compiled_material_paths_are_canonical_and_reject_unsafe_inputs(self):
        self.assertEqual(
            generator.compiled_override_path("materials/models/test.vmat"),
            "materials/models/test.vmat_c",
        )
        with self.assertRaises(ValueError):
            generator.compiled_material_path("../outside.vmat")


class DeploymentTests(unittest.TestCase):
    @staticmethod
    def extractor_path():
        published_extractor = os.environ.get("DOTA2_COSMETIC_DISABLER_TEST_EXTRACTOR")
        if published_extractor:
            return Path(published_extractor).resolve()
        executable = "Dota2VpkExtractor.exe" if generator.os.name == "nt" else "Dota2VpkExtractor"
        return (
            Path(__file__).resolve().parents[1]
            / "tools"
            / "VpkExtractor"
            / "bin"
            / "Release"
            / "net8.0"
            / executable
        )

    def make_plan(self):
        return generator.Plan(
            mappings=[
                generator.Mapping(
                    source="models/heroes/test/default.vmdl",
                    target="models/items/test/cosmetic.vmdl",
                    reason="test",
                )
            ],
            unresolved=[],
            stats={},
        )

    def test_operation_lock_rejects_an_overlapping_build_or_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary:
            dota = Path(temporary) / "dota 2 beta"
            dota.mkdir()
            with generator.dota_operation_lock(dota):
                with self.assertRaisesRegex(generator.GeneratorError, "Another build or cleanup"):
                    with generator.dota_operation_lock(dota):
                        self.fail("The second lock should not have been acquired")

    def test_deploy_and_clean_preserve_untracked_files(self):
        extractor = self.extractor_path()
        if not extractor.is_file():
            self.skipTest("Build tools/VpkExtractor in Release mode to run the deployment test")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            work = root / "work"
            output = root / "dota_dutch"
            source = cache / "models/heroes/test/default.vmdl_c"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"compiled model")
            output.mkdir()
            unrelated = output / "keep-me.txt"
            unrelated.write_text("user file", encoding="utf-8")
            official = output / "pak01_dir.vpk"
            official.write_bytes(b"unowned language pack")

            progress_updates = []
            copied, missing = generator.deploy_overrides(
                self.make_plan(),
                cache,
                output,
                work,
                extractor=extractor,
                clean_first=True,
                allow_missing=False,
                language="dutch",
                progress_update=lambda percent, message: progress_updates.append(
                    (percent, message)
                ),
            )
            self.assertEqual(copied, 1)
            self.assertEqual(missing, [])
            percentages = [value for value, _message in progress_updates]
            self.assertEqual(percentages, sorted(percentages))
            self.assertEqual(percentages[0], 0)
            self.assertEqual(percentages[-1], 100)
            self.assertTrue(any(not value.is_integer() for value in percentages))
            marker = json.loads((output / generator.MARKER_FILENAME).read_text(encoding="utf-8"))
            self.assertEqual(marker["deployment_mode"], generator.VPK_DEPLOYMENT_MODE)
            self.assertEqual(marker["files"], ["pak98_dir.vpk"])
            self.assertEqual(marker["resources"], ["models/items/test/cosmetic.vmdl_c"])
            self.assertEqual(marker["enabled_categories"], [generator.CATEGORY_STANDARD_WEARABLES])
            self.assertEqual(
                marker["archive_sha256"],
                generator.sha256_file(output / "pak98_dir.vpk"),
            )

            unpacked = root / "unpacked"
            generator.extract_vpk(
                extractor,
                output / "pak98_dir.vpk",
                marker["resources"],
                unpacked,
            )
            self.assertEqual(
                (unpacked / "models/items/test/cosmetic.vmdl_c").read_bytes(),
                b"compiled model",
            )

            removed = generator.clean_output(output, allow_shared_directory=True)
            self.assertEqual(removed, 1)
            self.assertTrue(unrelated.is_file())
            self.assertTrue(official.is_file())
            self.assertFalse((output / "pak98_dir.vpk").exists())

    def test_skin_sensitive_model_is_patched_before_vpk_packaging(self):
        extractor = self.extractor_path()
        if not extractor.is_file():
            self.skipTest("Build tools/VpkExtractor in Release mode to run the deployment test")
        plan = generator.Plan(
            mappings=[
                generator.Mapping(
                    source="models/heroes/test/default.vmdl",
                    target="models/items/test/skinned.vmdl",
                    reason="test",
                    neutralize_model_skin=True,
                    required_material_groups=3,
                )
            ],
            unresolved=[],
            stats={},
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            source = cache / "models/heroes/test/default.vmdl_c"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"compiled model")
            output = root / "dota_dutch"

            def fake_patch(
                _patcher,
                jobs,
                _manifest_directory,
                *,
                progress,
                progress_update,
            ):
                self.assertEqual(len(jobs), 1)
                patch_source, destination, groups = jobs[0]
                self.assertTrue(os.path.samefile(patch_source, source))
                self.assertEqual(groups, 3)
                destination.parent.mkdir(parents=True)
                destination.write_bytes(patch_source.read_bytes() + b";groups=3")
                progress_update("patch", 1, 1)

            with patch(
                "dota_disabler.deployment.patch_model_material_groups_batch",
                side_effect=fake_patch,
            ) as patch_model:
                copied, missing = generator.deploy_overrides(
                    plan,
                    cache,
                    output,
                    root / "work",
                    extractor=extractor,
                    model_patcher=root / "patcher.exe",
                    clean_first=True,
                    allow_missing=False,
                    language="dutch",
                )

            self.assertEqual((copied, missing), (1, []))
            patch_model.assert_called_once()
            marker = json.loads((output / generator.MARKER_FILENAME).read_text(encoding="utf-8"))
            unpacked = root / "unpacked"
            generator.extract_vpk(
                extractor,
                output / marker["files"][0],
                marker["resources"],
                unpacked,
            )
            self.assertEqual(
                (unpacked / "models/items/test/skinned.vmdl_c").read_bytes(),
                b"compiled model;groups=3",
            )

    def test_skin_sensitive_deploy_refuses_to_run_without_model_patcher(self):
        plan = generator.Plan(
            mappings=[
                generator.Mapping(
                    source="models/heroes/test/default.vmdl",
                    target="models/items/test/skinned.vmdl",
                    reason="test",
                    neutralize_model_skin=True,
                    required_material_groups=2,
                )
            ],
            unresolved=[],
            stats={},
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            source = cache / "models/heroes/test/default.vmdl_c"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"compiled model")
            output = root / "dota_dutch"

            with self.assertRaisesRegex(generator.GeneratorError, "model skin patcher"):
                generator.deploy_overrides(
                    plan,
                    cache,
                    output,
                    root / "work",
                    extractor=root / "extractor.exe",
                    clean_first=True,
                    allow_missing=False,
                    language="dutch",
                )

            self.assertFalse(output.exists())
            self.assertFalse((output / "pak98_dir.vpk").exists())

    def test_first_deploy_rolls_back_archive_when_marker_write_fails(self):
        extractor = self.extractor_path()
        if not extractor.is_file():
            self.skipTest("Build tools/VpkExtractor in Release mode to run the deployment test")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            output = root / "dota_dutch"
            source = cache / "models/heroes/test/default.vmdl_c"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"compiled model")
            output.mkdir()
            unrelated = output / "keep-me.txt"
            unrelated.write_text("user file", encoding="utf-8")

            with patch(
                "dota_disabler.deployment.write_json",
                side_effect=OSError("marker disk failure"),
            ):
                with self.assertRaisesRegex(OSError, "marker disk failure"):
                    generator.deploy_overrides(
                        self.make_plan(),
                        cache,
                        output,
                        root / "work",
                        extractor=extractor,
                        clean_first=True,
                        allow_missing=False,
                        language="dutch",
                    )

            self.assertTrue(unrelated.is_file())
            self.assertFalse((output / generator.MARKER_FILENAME).exists())
            self.assertFalse(any(output.glob("pak*_dir.vpk")))
            self.assertFalse(any(output.glob("*.rollback")))

    def test_rebuild_restores_previous_archive_when_marker_write_fails(self):
        extractor = self.extractor_path()
        if not extractor.is_file():
            self.skipTest("Build tools/VpkExtractor in Release mode to run the deployment test")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            output = root / "dota_dutch"
            source = cache / "models/heroes/test/default.vmdl_c"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"first compiled model")

            generator.deploy_overrides(
                self.make_plan(),
                cache,
                output,
                root / "work",
                extractor=extractor,
                clean_first=True,
                allow_missing=False,
                language="dutch",
            )
            marker_path = output / generator.MARKER_FILENAME
            marker_before = marker_path.read_bytes()
            marker = generator.read_marker(output, allow_shared_directory=True)
            archive = output / marker["files"][0]
            archive_before = archive.read_bytes()
            source.write_bytes(b"second compiled model")

            with patch(
                "dota_disabler.deployment.write_json",
                side_effect=OSError("marker disk failure"),
            ):
                with self.assertRaisesRegex(OSError, "marker disk failure"):
                    generator.deploy_overrides(
                        self.make_plan(),
                        cache,
                        output,
                        root / "work",
                        extractor=extractor,
                        clean_first=True,
                        allow_missing=False,
                        language="dutch",
                    )

            self.assertEqual(marker_path.read_bytes(), marker_before)
            self.assertEqual(archive.read_bytes(), archive_before)
            self.assertEqual(generator.sha256_file(archive), marker["archive_sha256"])
            self.assertFalse(any(output.glob("*.rollback")))

    def test_legacy_loose_files_are_restored_when_vpk_marker_write_fails(self):
        extractor = self.extractor_path()
        if not extractor.is_file():
            self.skipTest("Build tools/VpkExtractor in Release mode to run the deployment test")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            output = root / "dota_dutch"
            source = cache / "models/heroes/test/default.vmdl_c"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"new compiled model")

            legacy_relative = "models/items/test/legacy_cosmetic.vmdl_c"
            legacy_file = output.joinpath(*legacy_relative.split("/"))
            legacy_file.parent.mkdir(parents=True)
            legacy_file.write_bytes(b"old owned loose model")
            marker_path = output / generator.MARKER_FILENAME
            generator.write_json(
                marker_path,
                {
                    "kind": generator.MARKER_KIND,
                    "generator_version": "0.5.1",
                    "files": [legacy_relative],
                },
            )
            marker_before = marker_path.read_bytes()

            with patch(
                "dota_disabler.deployment.write_json",
                side_effect=OSError("marker disk failure"),
            ):
                with self.assertRaisesRegex(OSError, "marker disk failure"):
                    generator.deploy_overrides(
                        self.make_plan(),
                        cache,
                        output,
                        root / "work",
                        extractor=extractor,
                        clean_first=True,
                        allow_missing=False,
                        language="dutch",
                    )

            self.assertEqual(marker_path.read_bytes(), marker_before)
            self.assertEqual(legacy_file.read_bytes(), b"old owned loose model")
            self.assertFalse(any(output.glob("pak*_dir.vpk")))
            self.assertFalse(any(output.rglob("*.rollback")))

    def test_deploy_does_not_pack_ignored_bodygroup_schema_overlay(self):
        extractor = self.extractor_path()
        if not extractor.is_file():
            self.skipTest("Build tools/VpkExtractor in Release mode to run the deployment test")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            output = root / "dota_dutch"
            source = cache / generator.compiled_model_path("models/heroes/test/test.vmdl")
            source.parent.mkdir(parents=True)
            source.write_bytes(b"compiled full hero model")
            items_schema = cache / generator.ITEMS_SCHEMA_RESOURCE
            items_schema.parent.mkdir(parents=True)
            items_schema.write_text(
                '''"items_game"
{
    "items"
    {
        "2"
        {
            "visuals"
            {
                "asset_modifier"
                {
                    "type" "bodygroup_visibility"
                    "asset" "models/heroes/test/test.vmdl"
                    "modifier" "head"
                    "value" "1"
                }
            }
        }
    }
}
''',
                encoding="utf-8",
            )
            plan = generator.Plan(
                mappings=[
                    generator.Mapping(
                        source="models/heroes/test/test.vmdl",
                        target="models/items/test/integrated_head.vmdl",
                        reason="bodygroup wearable replaced with full hero fallback",
                        item_id="2",
                        hero=HERO,
                        slot="head",
                    )
                ],
                unresolved=[],
                stats={},
            )

            copied, missing = generator.deploy_overrides(
                plan,
                cache,
                output,
                root / "work",
                extractor=extractor,
                items_schema=items_schema,
                clean_first=True,
                allow_missing=False,
                language="dutch",
            )

            self.assertEqual((copied, missing), (1, []))
            marker = generator.read_marker(output, allow_shared_directory=True)
            self.assertEqual(marker["schema_resources"], [])
            extracted = root / "extracted-bodygroup-build"
            generator.extract_vpk(
                extractor,
                output / marker["files"][0],
                ["models/items/test/integrated_head.vmdl_c"],
                extracted,
            )
            self.assertEqual(
                (extracted / "models/items/test/integrated_head.vmdl_c").read_bytes(),
                b"compiled full hero model",
            )

    def test_shared_language_directory_uses_a_free_vpk_slot(self):
        extractor = self.extractor_path()
        if not extractor.is_file():
            self.skipTest("Build tools/VpkExtractor in Release mode to run the deployment test")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            work = root / "work"
            output = root / "dota_dutch"
            source = cache / "models/heroes/test/default.vmdl_c"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"compiled model")
            output.mkdir()
            occupied = output / "pak98_dir.vpk"
            occupied.write_bytes(b"unowned VPK")

            generator.deploy_overrides(
                self.make_plan(),
                cache,
                output,
                work,
                extractor=extractor,
                clean_first=True,
                allow_missing=False,
                language="dutch",
            )
            marker = generator.read_marker(output, allow_shared_directory=True)
            self.assertEqual(marker["files"], ["pak97_dir.vpk"])
            self.assertEqual(occupied.read_bytes(), b"unowned VPK")

    def test_missing_source_aborts_before_output_is_created(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "dota_defaultmodels"
            with self.assertRaises(generator.GeneratorError):
                generator.deploy_overrides(
                    self.make_plan(),
                    root / "cache",
                    output,
                    root / "work",
                    extractor=Path("missing-extractor"),
                    clean_first=True,
                    allow_missing=False,
                    language="dutch",
                )
            self.assertFalse(output.exists())

    def test_deploy_refuses_when_every_owned_vpk_slot_is_unavailable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            work = root / "work"
            output = root / "dota_dutch"
            source = cache / "models/heroes/test/default.vmdl_c"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"compiled model")
            output.mkdir()
            for name in generator.VPK_ARCHIVE_CANDIDATES:
                (output / name).write_bytes(b"unowned VPK")

            with self.assertRaisesRegex(generator.UnsafeOutputError, "No free owned VPK slot"):
                generator.deploy_overrides(
                    self.make_plan(),
                    cache,
                    output,
                    work,
                    extractor=Path("unused-extractor"),
                    clean_first=True,
                    allow_missing=False,
                    language="dutch",
                )

    def test_vpk_replacement_can_disable_a_category_without_retaining_old_resources(self):
        extractor = self.extractor_path()
        if not extractor.is_file():
            self.skipTest("Build tools/VpkExtractor in Release mode to run the deployment test")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            output = root / "dota_dutch"
            source = cache / "models/heroes/test/default.vmdl_c"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"compiled model")
            persona_source = cache / "models/development/invisiblebox.vmdl_c"
            persona_source.parent.mkdir(parents=True, exist_ok=True)
            persona_source.write_bytes(b"invisible model")
            full_plan = generator.Plan(
                mappings=self.make_plan().mappings
                + [
                    generator.Mapping(
                        source=generator.INVISIBLE_MODEL,
                        target="models/items/test/persona.vmdl",
                        reason="test persona",
                        category=generator.CATEGORY_PERSONA_MODELS,
                    )
                ],
                unresolved=[],
                stats={},
            )

            generator.deploy_overrides(
                full_plan,
                cache,
                output,
                root / "work",
                extractor=extractor,
                clean_first=True,
                allow_missing=False,
                language="dutch",
                enabled_categories={
                    generator.CATEGORY_STANDARD_WEARABLES,
                    generator.CATEGORY_PERSONA_MODELS,
                },
            )
            generator.deploy_overrides(
                self.make_plan(),
                cache,
                output,
                root / "work",
                extractor=extractor,
                clean_first=False,
                allow_missing=False,
                language="dutch",
                enabled_categories={generator.CATEGORY_STANDARD_WEARABLES},
            )
            marker = generator.read_marker(output, allow_shared_directory=True)
            self.assertEqual(marker["enabled_categories"], [generator.CATEGORY_STANDARD_WEARABLES])
            self.assertEqual(marker["resources"], ["models/items/test/cosmetic.vmdl_c"])
            unpacked = root / "after-category-change"
            generator.extract_vpk(
                extractor,
                output / marker["files"][0],
                (
                    "models/items/test/cosmetic.vmdl_c",
                    "models/items/test/persona.vmdl_c",
                ),
                unpacked,
            )
            self.assertTrue((unpacked / "models/items/test/cosmetic.vmdl_c").is_file())
            self.assertFalse((unpacked / "models/items/test/persona.vmdl_c").exists())

    def test_clean_rejects_marker_entries_that_are_not_compiled_models(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "dota_defaultmodels"
            output.mkdir()
            unrelated = output / "keep-me.txt"
            unrelated.write_text("user data", encoding="utf-8")
            generator.write_json(
                output / generator.MARKER_FILENAME,
                {
                    "kind": generator.MARKER_KIND,
                    "files": ["keep-me.txt"],
                },
            )

            with self.assertRaises(generator.UnsafeOutputError):
                generator.clean_output(output)
            self.assertEqual(unrelated.read_text(encoding="utf-8"), "user data")

    def test_clean_rejects_vpk_marker_outside_the_reserved_slots(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "dota_dutch"
            output.mkdir()
            unrelated = output / "pak99_dir.vpk"
            unrelated.write_bytes(b"another tool's archive")
            generator.write_json(
                output / generator.MARKER_FILENAME,
                {
                    "kind": generator.MARKER_KIND,
                    "deployment_mode": generator.VPK_DEPLOYMENT_MODE,
                    "files": [unrelated.name],
                    "resources": [],
                    "archive_sha256": generator.sha256_file(unrelated),
                },
            )

            with self.assertRaises(generator.UnsafeOutputError):
                generator.clean_output(output, allow_shared_directory=True)
            self.assertEqual(unrelated.read_bytes(), b"another tool's archive")

    def test_clean_rejects_unsupported_resource_entries_in_a_vpk_marker(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "dota_dutch"
            output.mkdir()
            archive = output / generator.VPK_ARCHIVE_CANDIDATES[0]
            archive.write_bytes(b"owned archive fixture")
            generator.write_json(
                output / generator.MARKER_FILENAME,
                {
                    "kind": generator.MARKER_KIND,
                    "deployment_mode": generator.VPK_DEPLOYMENT_MODE,
                    "files": [archive.name],
                    "resources": ["scripts/keep-me.txt"],
                    "archive_sha256": generator.sha256_file(archive),
                },
            )

            with self.assertRaises(generator.UnsafeOutputError):
                generator.clean_output(output, allow_shared_directory=True)
            self.assertEqual(archive.read_bytes(), b"owned archive fixture")

    def test_clean_rejects_unexpected_schema_entries_in_a_vpk_marker(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "dota_dutch"
            output.mkdir()
            archive = output / generator.VPK_ARCHIVE_CANDIDATES[0]
            archive.write_bytes(b"owned archive fixture")
            generator.write_json(
                output / generator.MARKER_FILENAME,
                {
                    "kind": generator.MARKER_KIND,
                    "deployment_mode": generator.VPK_DEPLOYMENT_MODE,
                    "files": [archive.name],
                    "resources": [],
                    "schema_resources": ["scripts/vscripts/unowned.txt"],
                    "archive_sha256": generator.sha256_file(archive),
                },
            )

            with self.assertRaises(generator.UnsafeOutputError):
                generator.clean_output(output, allow_shared_directory=True)
            self.assertEqual(archive.read_bytes(), b"owned archive fixture")

    def test_migration_removes_only_owned_legacy_loose_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            dota = Path(temporary) / "dota 2 beta"
            legacy = dota / f"game/dota_{generator.LEGACY_LANGUAGE}"
            owned = legacy / "models/items/test/cosmetic.vmdl_c"
            owned.parent.mkdir(parents=True)
            owned.write_bytes(b"old generated model")
            unrelated = legacy / "keep-me.txt"
            unrelated.write_text("user data", encoding="utf-8")
            generator.write_json(
                legacy / generator.MARKER_FILENAME,
                {
                    "kind": generator.MARKER_KIND,
                    "files": ["models/items/test/cosmetic.vmdl_c"],
                },
            )

            removed = generator.clean_legacy_output_after_migration(
                dota,
                progress=lambda _message: None,
                warning=self.fail,
            )

            self.assertEqual(removed, 1)
            self.assertFalse(owned.exists())
            self.assertTrue(unrelated.is_file())
            self.assertFalse((legacy / generator.MARKER_FILENAME).exists())

    def test_switching_mounts_removes_only_the_previous_owned_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            dota = Path(temporary) / "dota 2 beta"
            previous = dota / "game/dota_finnish"
            previous.mkdir(parents=True)
            archive = previous / generator.VPK_ARCHIVE_CANDIDATES[0]
            archive.write_bytes(b"previous owned archive")
            unrelated = previous / "keep-me.txt"
            unrelated.write_text("user data", encoding="utf-8")
            generator.write_json(
                previous / generator.MARKER_FILENAME,
                {
                    "kind": generator.MARKER_KIND,
                    "deployment_mode": generator.VPK_DEPLOYMENT_MODE,
                    "files": [archive.name],
                    "resources": [],
                    "archive_sha256": generator.sha256_file(archive),
                },
            )

            removed = generator.clean_other_language_outputs_after_migration(
                dota,
                "dutch",
                progress=lambda _message: None,
                warning=self.fail,
            )

            self.assertEqual(removed, 1)
            self.assertFalse(archive.exists())
            self.assertFalse((previous / generator.MARKER_FILENAME).exists())
            self.assertEqual(unrelated.read_text(encoding="utf-8"), "user data")


class VpkExtractorIntegrationTests(unittest.TestCase):
    @staticmethod
    def extractor_path():
        published_extractor = os.environ.get("DOTA2_COSMETIC_DISABLER_TEST_EXTRACTOR")
        if published_extractor:
            return Path(published_extractor).resolve()
        executable = "Dota2VpkExtractor.exe" if generator.os.name == "nt" else "Dota2VpkExtractor"
        return (
            Path(__file__).resolve().parents[1]
            / "tools"
            / "VpkExtractor"
            / "bin"
            / "Release"
            / "net8.0"
            / executable
        )

    @staticmethod
    def write_test_vpk(path, resource_path, payload):
        VpkExtractorIntegrationTests.write_test_vpk_entries(path, {resource_path: payload})

    @staticmethod
    def write_test_vpk_entries(path, resources):
        grouped = {}
        for resource_path, payload in resources.items():
            directory, filename = resource_path.rsplit("/", 1)
            stem, extension = filename.rsplit(".", 1)
            grouped.setdefault(extension, {}).setdefault(directory, []).append((stem, payload))

        tree = bytearray()
        for extension in sorted(grouped):
            tree.extend(extension.encode("utf-8") + b"\0")
            for directory in sorted(grouped[extension]):
                tree.extend(directory.encode("utf-8") + b"\0")
                for stem, payload in sorted(grouped[extension][directory]):
                    tree.extend(stem.encode("utf-8") + b"\0")
                    tree.extend(
                        struct.pack("<IHHIIH", zlib.crc32(payload), len(payload), 0x7FFF, 0, 0, 0xFFFF)
                    )
                    tree.extend(payload)
                tree.extend(b"\0")
            tree.extend(b"\0")
        tree.extend(b"\0")
        path.write_bytes(struct.pack("<III", 0x55AA1234, 1, len(tree)) + tree)

    def test_internal_extractor_reads_and_crc_validates_a_vpk_entry(self):
        extractor = self.extractor_path()
        if not extractor.is_file():
            self.skipTest("Build tools/VpkExtractor in Release mode to run the integration test")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            vpk = root / "pak01_dir.vpk"
            paths_file = root / "paths.txt"
            output = root / "output"
            payload = b"current Dota schema fixture"
            self.write_test_vpk(vpk, "scripts/sample.txt", payload)
            paths_file.write_text("scripts/sample.txt\n", encoding="utf-8")

            process = subprocess.run(
                [
                    str(extractor),
                    "--vpk",
                    str(vpk),
                    "--output",
                    str(output),
                    "--paths-file",
                    str(paths_file),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual((output / "scripts/sample.txt").read_bytes(), payload)
            result = json.loads(process.stdout)
            self.assertEqual(result["requested"], 1)
            self.assertEqual(result["extracted"], 1)
            self.assertEqual(result["missing"], [])

    def test_internal_packer_creates_a_crc_validated_override_vpk(self):
        extractor = self.extractor_path()
        if not extractor.is_file():
            self.skipTest("Build tools/VpkExtractor in Release mode to run the integration test")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            staged = root / "staged"
            resources = {
                "models/items/test/cosmetic.vmdl_c": b"compiled cosmetic model fixture",
                "materials/models/items/test/cosmetic.vmat_c": b"compiled cosmetic material fixture",
                "particles/econ/items/test/cosmetic.vpcf_c": b"compiled cosmetic particle fixture",
                "particles/econ/items/test/cosmetic.vsnap_c": b"compiled cosmetic snapshot fixture",
            }
            for relative, payload in resources.items():
                resource = staged.joinpath(*relative.split("/"))
                resource.parent.mkdir(parents=True, exist_ok=True)
                resource.write_bytes(payload)
            archive = root / "pak99_dir.vpk"

            pack_progress = []
            packed = generator.pack_vpk(
                extractor,
                staged,
                archive,
                progress_update=lambda phase, completed, total: pack_progress.append(
                    (phase, completed, total)
                ),
            )
            self.assertEqual(packed, len(resources))
            self.assertEqual(pack_progress[0], ("pack", 1, len(resources)))
            self.assertEqual(pack_progress[-1], ("verify", len(resources), len(resources)))

            extracted = root / "extracted"
            extract_progress = []
            generator.extract_vpk(
                extractor,
                archive,
                tuple(resources),
                extracted,
                progress_update=lambda phase, completed, total: extract_progress.append(
                    (phase, completed, total)
                ),
            )
            self.assertEqual(
                extract_progress[-1],
                ("extract", len(resources), len(resources)),
            )
            for relative, payload in resources.items():
                self.assertEqual(extracted.joinpath(*relative.split("/")).read_bytes(), payload)

    def test_english_language_support_is_listed_extracted_and_renamed(self):
        extractor = self.extractor_path()
        if not extractor.is_file():
            self.skipTest("Build tools/VpkExtractor in Release mode to run the integration test")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            vpk = root / "pak01_dir.vpk"
            self.write_test_vpk_entries(
                vpk,
                {
                    "resource/localization/dota_english.txt": b"english UI",
                    "resource/subtitles/intro_english.vtt": b"WEBVTT",
                    "resource/localization/dota_russian.txt": b"russian UI",
                },
            )
            listed = generator.list_vpk_resources(
                extractor,
                vpk,
                ("_english.txt", "_english.vtt"),
            )
            self.assertEqual(
                listed,
                [
                    "resource/localization/dota_english.txt",
                    "resource/subtitles/intro_english.vtt",
                ],
            )

            staged = root / "staged"
            support = generator.stage_english_language_support(
                extractor,
                vpk,
                root / "work",
                staged,
                "finnish",
            )
            self.assertEqual(
                support,
                [
                    "resource/localization/dota_finnish.txt",
                    "resource/subtitles/intro_finnish.vtt",
                ],
            )
            self.assertEqual(
                (staged / "resource/localization/dota_finnish.txt").read_bytes(),
                b"english UI",
            )

    def test_python_extraction_does_not_reuse_a_stale_missing_resource(self):
        extractor = self.extractor_path()
        if not extractor.is_file():
            self.skipTest("Build tools/VpkExtractor in Release mode to run the integration test")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            vpk = root / "pak01_dir.vpk"
            output = root / "output"
            payload = b"fresh fixture"
            self.write_test_vpk(vpk, "scripts/sample.txt", payload)
            stale = output / "scripts/missing.txt"
            stale.parent.mkdir(parents=True)
            stale.write_bytes(b"stale data from an older build")

            generator.extract_vpk(
                extractor,
                vpk,
                ("scripts/sample.txt", "scripts/missing.txt"),
                output,
            )
            self.assertEqual((output / "scripts/sample.txt").read_bytes(), payload)
            self.assertFalse(stale.exists())

    def test_internal_extractor_rejects_traversal(self):
        extractor = self.extractor_path()
        if not extractor.is_file():
            self.skipTest("Build tools/VpkExtractor in Release mode to run the integration test")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            vpk = root / "pak01_dir.vpk"
            paths_file = root / "paths.txt"
            output = root / "output"
            self.write_test_vpk(vpk, "scripts/sample.txt", b"fixture")
            paths_file.write_text("../outside.txt\n", encoding="utf-8")

            process = subprocess.run(
                [
                    str(extractor),
                    "--vpk",
                    str(vpk),
                    "--output",
                    str(output),
                    "--paths-file",
                    str(paths_file),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(process.returncode, 0)
            self.assertFalse((root / "outside.txt").exists())


class PackagedReleaseSmokeTests(unittest.TestCase):
    def test_packaged_application_uses_its_embedded_extractor(self):
        exact_application = os.environ.get("DOTA2_COSMETIC_DISABLER_EXE")
        if not exact_application:
            self.skipTest("The release pipeline supplies the exact packaged executable")
        application = Path(exact_application).resolve()
        self.assertTrue(application.is_file(), f"Packaged application was not found: {application}")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            isolated_cwd = root / "isolated-working-directory"
            isolated_cwd.mkdir()
            clean_environment = os.environ.copy()
            clean_environment.pop("PYTHONPATH", None)
            clean_environment.pop("PYTHONHOME", None)
            clean_environment.pop("DOTA_DISABLE_COSMETICS_VPK_EXTRACTOR", None)
            clean_environment.pop("DOTA_DISABLE_COSMETICS_MODEL_PATCHER", None)

            gui_smoke = subprocess.run(
                [str(application), "gui", "--smoke-test"],
                cwd=isolated_cwd,
                env=clean_environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=20,
            )
            self.assertEqual(gui_smoke.returncode, 0, gui_smoke.stderr + gui_smoke.stdout)

            dota = root / "steamapps/common/dota 2 beta"
            pak = dota / "game/dota/pak01_dir.vpk"
            pak.parent.mkdir(parents=True)
            manifest = root / "steamapps/appmanifest_570.acf"
            manifest.write_text(
                '"AppState" { "appid" "570" "buildid" "98765432" "LastUpdated" "1770000000" }\n',
                encoding="utf-8",
            )
            default_model = (
                b"compiled default model fixture\x00"
                b"materials/models/heroes/test/head_color.vmat\x00"
                b"materials/models/items/event/test/head_color.vmat\x00"
            )
            default_material = b"compiled default material fixture"
            default_particle = b"compiled default particle fixture"
            neutral_particle = b"compiled neutral particle fixture"
            default_snapshot = b"compiled default particle snapshot fixture"
            items_game = b'''"items_game"
{
    "prefabs" { }
    "items"
    {
        "1"
        {
            "name" "default_head"
            "item_slot" "head"
            "baseitem" "1"
            "used_by_heroes" { "npc_dota_hero_test" "1" }
            "model_player" "models/heroes/test/default_head.vmdl"
        }
        "2"
        {
            "name" "cosmetic_head"
            "item_slot" "head"
            "used_by_heroes" { "npc_dota_hero_test" "1" }
            "model_player" "models/items/test/cosmetic_head.vmdl"
            "visuals"
            {
                "asset_modifier0"
                {
                    "type" "particle"
                    "asset" "particles/units/heroes/test/default_attack.vpcf"
                    "modifier" "particles/econ/items/test/cosmetic_attack.vpcf"
                }
                "asset_modifier1"
                {
                    "type" "particle_create"
                    "modifier" "particles/econ/items/test/cosmetic_ambient.vpcf"
                }
                "asset_modifier2"
                {
                    "type" "particle_snapshot"
                    "asset" "particles/units/heroes/test/default_pose.vsnap"
                    "modifier" "particles/econ/items/test/cosmetic_pose.vsnap"
                }
            }
        }
    }
}
'''
            npc_heroes = b'''"DOTAHeroes"
{
    "npc_dota_hero_base" { }
    "npc_dota_hero_test" { "Model" "models/heroes/test/test.vmdl" }
}
'''
            npc_units = b'''"DOTAUnits"
{
    "npc_dota_units_base" { "Model" "models/development/invisiblebox.vmdl" }
}
'''
            VpkExtractorIntegrationTests.write_test_vpk_entries(
                pak,
                {
                    "scripts/items/items_game.txt": items_game,
                    "scripts/npc/npc_heroes.txt": npc_heroes,
                    "scripts/npc/npc_units.txt": npc_units,
                    "models/heroes/test/default_head.vmdl_c": default_model,
                    "materials/models/heroes/test/head_color.vmat_c": default_material,
                    "particles/units/heroes/test/default_attack.vpcf_c": default_particle,
                    "particles/error/null.vpcf_c": neutral_particle,
                    "particles/units/heroes/test/default_pose.vsnap_c": default_snapshot,
                },
            )

            process = subprocess.run(
                [
                    str(application),
                    "build",
                    "--dota",
                    str(dota),
                    "--work",
                    str(root / "work"),
                ],
                cwd=isolated_cwd,
                env=clean_environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
            self.assertEqual(process.returncode, 0, process.stderr + process.stdout)
            output = dota / f"game/dota_{generator.DEFAULT_LANGUAGE}"
            marker = json.loads((output / generator.MARKER_FILENAME).read_text(encoding="utf-8"))
            self.assertEqual(marker["deployment_mode"], generator.VPK_DEPLOYMENT_MODE)
            expected_resources = {
                "models/items/test/cosmetic_head.vmdl_c": default_model,
                "particles/econ/items/test/cosmetic_attack.vpcf_c": default_particle,
                "particles/econ/items/test/cosmetic_ambient.vpcf_c": neutral_particle,
                "particles/econ/items/test/cosmetic_pose.vsnap_c": default_snapshot,
            }
            self.assertEqual(set(marker["resources"]), set(expected_resources))
            archive = output / marker["files"][0]
            self.assertTrue(archive.is_file())
            self.assertEqual(marker["archive_sha256"], generator.sha256_file(archive))
            unpacked = root / "unpacked-release-vpk"
            generator.extract_vpk(
                VpkExtractorIntegrationTests.extractor_path(),
                archive,
                marker["resources"],
                unpacked,
            )
            for relative, payload in expected_resources.items():
                self.assertEqual(unpacked.joinpath(*relative.split("/")).read_bytes(), payload)
            self.assertEqual(marker["dota_version"]["steam_build_id"], "98765432")
            self.assertEqual(set(marker["enabled_categories"]), generator.DEFAULT_CATEGORIES)
            history = json.loads((root / "work" / generator.HISTORY_FILENAME).read_text(encoding="utf-8"))
            self.assertEqual(history["entries"][-1]["dota_version"]["steam_build_id"], "98765432")
            self.assertEqual(
                set(history["entries"][-1]["enabled_categories"]),
                generator.DEFAULT_CATEGORIES,
            )

            status = subprocess.run(
                [str(application), "status", "--dota", str(dota)],
                cwd=isolated_cwd,
                env=clean_environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            self.assertEqual(status.returncode, 0, status.stderr + status.stdout)
            self.assertIn("Status: CURRENT", status.stdout)

            history_command = subprocess.run(
                [str(application), "history", "--work", str(root / "work")],
                cwd=isolated_cwd,
                env=clean_environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            self.assertEqual(
                history_command.returncode,
                0,
                history_command.stderr + history_command.stdout,
            )
            self.assertIn("Steam build 98765432", history_command.stdout)

            sentinel = output / "unowned-language-file.txt"
            sentinel.write_text("preserve me", encoding="utf-8")
            clean_command = subprocess.run(
                [str(application), "clean", "--dota", str(dota)],
                cwd=isolated_cwd,
                env=clean_environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            self.assertEqual(
                clean_command.returncode,
                0,
                clean_command.stderr + clean_command.stdout,
            )
            self.assertFalse(archive.exists())
            self.assertFalse((output / generator.MARKER_FILENAME).exists())
            self.assertTrue(sentinel.is_file())

            post_clean_status = subprocess.run(
                [str(application), "status", "--dota", str(dota)],
                cwd=isolated_cwd,
                env=clean_environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            self.assertEqual(
                post_clean_status.returncode,
                0,
                post_clean_status.stderr + post_clean_status.stdout,
            )
            self.assertIn("Status: NOT BUILT", post_clean_status.stdout)


if __name__ == "__main__":
    unittest.main()
