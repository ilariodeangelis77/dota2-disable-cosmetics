"""Offline regressions for the #base parser failure reported in issue #1."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dota_disabler import schema_files
from dota_disabler.keyvalues import KVObject, parse_value
from dota_disabler.schema import load_hero_models, load_items_game, load_unit_models


class SchemaBaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def write(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8-sig")
        return path

    def test_hero_models_merge_defaults_without_overwriting_local_models(self):
        path = self.write("scripts/npc/npc_heroes.txt", '''
            #base "npc_heroes_base.txt"
            "DOTAHeroes" {
                "npc_dota_hero_axe" { "Model" "models/heroes/axe/local.vmdl" }
            }
        ''')
        self.write("scripts/npc/npc_heroes_base.txt", '''
            "DOTAHeroes" {
                "npc_dota_hero_base" { "Model" "models/error.vmdl" }
                "npc_dota_hero_axe" {
                    "Model" "models/heroes/axe/base.vmdl"
                    "Model1" "models/heroes/axe/variant.vmdl"
                }
                "npc_dota_hero_lina" { "Model" "models/heroes/lina/lina.vmdl" }
            }
        ''')
        models = load_hero_models(path)
        self.assertEqual(models["npc_dota_hero_axe"], "models/heroes/axe/local.vmdl")
        self.assertEqual(models["npc_dota_hero_axe_variant_1"], "models/heroes/axe/variant.vmdl")
        self.assertEqual(models["npc_dota_hero_lina"], "models/heroes/lina/lina.vmdl")
        self.assertNotIn("npc_dota_hero_base", models)

    def test_nested_relative_paths_and_base_only_forwarding_files(self):
        path = self.write("scripts/npc/npc_heroes.txt", '#base "heroes/axe.txt"')
        self.write("scripts/npc/heroes/axe.txt", '#base "../defaults/axe.txt"')
        self.write("scripts/npc/defaults/axe.txt", '''
            "DOTAHeroes" { "npc_dota_hero_axe" { "Model" "models/heroes/axe/axe.vmdl" } }
        ''')
        self.assertEqual(load_hero_models(path)["npc_dota_hero_axe"], "models/heroes/axe/axe.vmdl")

    def test_version_only_hero_index_loads_129_sibling_base_files_in_order(self):
        # Match the supplied index's structure without distributing Valve schemas.
        names = ["npc_dota_hero_base", *(f"npc_dota_hero_fixture_{index}" for index in range(128))]
        references = []
        expected = {}
        for hero in names:
            relative = f"heroes/{hero}.txt"
            references.append(f'#base "{relative}"')
            model = f"models/heroes/fixture/{hero}.vmdl"
            self.write(f"scripts/npc/{relative}", f'"DOTAHeroes" {{ "{hero}" {{ "Model" "{model}" }} }}')
            if hero != "npc_dota_hero_base":
                expected[hero] = model
        path = self.write("scripts/npc/npc_heroes.txt", "\n".join(references) + '\n"DOTAHeroes" { "Version" "1" }')
        self.assertEqual(len(schema_files.schema_base_files(path, self.root)), 129)
        models = load_hero_models(path)
        actual = {hero: model for hero, model in models.items() if "_variant_" not in hero}
        self.assertEqual(actual, expected)
        self.assertEqual(list(actual), names[1:])
        self.assertEqual(len(models), 256)
        (path.parent / f"heroes/{names[-1]}.txt").unlink()
        with self.assertRaisesRegex(FileNotFoundError, names[-1]):
            load_hero_models(path)

    def test_multiple_bases_before_and_after_root_keep_first_base_precedence(self):
        path = self.write("heroes.txt", '''
            #BASE "first.txt"
            "DOTAHeroes" { "npc_dota_hero_axe" { "Enabled" "1" } }
            #base "second.txt"
        ''')
        for filename, model in (("first.txt", "first"), ("second.txt", "second")):
            self.write(filename, f'''"DOTAHeroes" {{
                "npc_dota_hero_axe" {{ "Model" "models/heroes/axe/{model}.vmdl" }}
            }}''')
        self.assertEqual(load_hero_models(path)["npc_dota_hero_axe"], "models/heroes/axe/first.vmdl")

    def test_local_duplicate_keys_are_preserved_while_nested_defaults_are_filled(self):
        path = self.write("items.txt", '''
            #base "base.txt"
            "items_game" {
                "visuals" {
                    "asset_modifier" { "modifier" "local_one" }
                    "asset_modifier" { "modifier" "local_two" }
                }
            }
        ''')
        self.write("base.txt", '''"items_game" {
            "visuals" { "asset_modifier" { "type" "model" "modifier" "base" } }
        }''')
        tokens = schema_files.schema_tokens(path, "items_game")
        self.assertEqual(tokens.next(), "items_game")
        value = parse_value(tokens)
        visuals = value.get_last("visuals")
        self.assertIsInstance(visuals, KVObject)
        self.assertEqual(len(visuals), 2)
        self.assertEqual(visuals[0][1].get_last("modifier"), "local_one")
        self.assertEqual(visuals[0][1].get_last("type"), "model")
        self.assertEqual(visuals[1][1].get_last("modifier"), "local_two")
        self.assertIsNone(visuals[1][1].get_last("type"))

    def test_items_reader_consumes_inherited_prefabs_and_item_fields(self):
        path = self.write("items.txt", '''
            #base "base.txt"
            "items_game" { "items" { "1" { "name" "local_name" } } }
        ''')
        self.write("base.txt", '''"items_game" {
            "prefabs" { "wearable" { "item_slot" "head" } }
            "items" { "1" {
                "name" "base_name" "prefab" "wearable"
                "used_by_heroes" { "npc_dota_hero_axe" "1" }
                "model_player" "models/heroes/axe/head.vmdl"
            } }
        }''')
        prefabs, items, _ = load_items_game(path)
        self.assertEqual(prefabs["wearable"]["item_slot"], "head")
        self.assertEqual(items["1"].name, "local_name")
        self.assertEqual(items["1"].hero, "npc_dota_hero_axe")
        self.assertEqual(items["1"].top_models, [("model_player", "models/heroes/axe/head.vmdl")])

    def test_units_reader_retains_unit_inheritance_after_file_inheritance(self):
        path = self.write("units.txt", '''
            "DOTAUnits" { "summon" { "include_keys_from" "base_summon" } }
            #base "base.txt"
        ''')
        self.write("base.txt", '''"DOTAUnits" {
            "base_summon" { "Model" "models/creeps/summon.vmdl" }
        }''')
        self.assertEqual(load_unit_models(path)["summon"], "models/creeps/summon.vmdl")

    def test_missing_base_is_an_error_even_when_local_models_exist(self):
        path = self.write("heroes.txt", '''#base "missing.txt"
            "DOTAHeroes" { "npc_dota_hero_axe" { "Model" "models/heroes/axe/axe.vmdl" } }
        ''')
        with self.assertRaisesRegex(FileNotFoundError, "missing.txt.*referenced by"):
            load_hero_models(path)

    def test_cycles_report_reference_chain(self):
        path = self.write("heroes.txt", '#base "base.txt"\n"DOTAHeroes" {}')
        self.write("base.txt", '#base "heroes.txt"\n"DOTAHeroes" {}')
        with self.assertRaisesRegex(ValueError, "Cyclic.*heroes.txt.*base.txt.*heroes.txt"):
            load_hero_models(path)

    def test_shared_base_is_not_mistaken_for_a_cycle(self):
        path = self.write("heroes.txt", '#base "one.txt"\n#base "two.txt"\n"DOTAHeroes" {}')
        self.write("one.txt", '#base "shared.txt"\n"DOTAHeroes" {}')
        self.write("two.txt", '#base "shared.txt"\n"DOTAHeroes" {}')
        self.write("shared.txt", '''"DOTAHeroes" {
            "npc_dota_hero_axe" { "Model" "models/heroes/axe/axe.vmdl" }
        }''')
        self.assertEqual(load_hero_models(path)["npc_dota_hero_axe"], "models/heroes/axe/axe.vmdl")

    def test_wrong_root_in_base_is_rejected(self):
        path = self.write("heroes.txt", '#base "base.txt"\n"DOTAHeroes" {}')
        self.write("base.txt", '"DOTAUnits" {}')
        with self.assertRaisesRegex(ValueError, "Expected DOTAHeroes root.*DOTAUnits.*base.txt"):
            load_hero_models(path)

    def test_unsafe_paths_are_rejected(self):
        for reference in ("../outside.txt", "/outside.txt", "C:/outside.txt", "\\\\host\\share.txt", "base.vpk", ""):
            with self.subTest(reference=reference):
                path = self.write("heroes.txt", f'#base "{reference}"\n"DOTAHeroes" {{}}')
                with self.assertRaisesRegex(ValueError, "Unsafe #base path"):
                    load_hero_models(path)

    def test_malformed_directives_and_roots_fail(self):
        for text in ('#base', '#base {}', '#base "base.txt" "DOTAHeroes" "scalar"',
                     '#base "base.txt" "DOTAHeroes" {} "DOTAHeroes" {}'):
            with self.subTest(text=text):
                path = self.write("heroes.txt", text)
                with self.assertRaises(ValueError):
                    load_hero_models(path)

    def test_nesting_limit_is_explicit(self):
        for index in range(4):
            self.write(f"{index}.txt", f'#base "{index + 1}.txt"\n"DOTAHeroes" {{}}')
        with patch.object(schema_files, "MAX_BASE_DEPTH", 3):
            with self.assertRaisesRegex(ValueError, "nesting exceeds 3"):
                load_hero_models(self.root / "0.txt")

    def test_dependency_scanner_ignores_comments_and_nested_directive_names(self):
        path = self.write("heroes.txt", '''
            // #base "comment.txt"
            "DOTAHeroes" { "nested" { "#base" "ordinary_value.txt" } }
            /* #base "comment_two.txt" */
            #base "BASE.TXT"
        ''')
        self.assertEqual(schema_files.schema_base_files(path, self.root), [self.root / "base.txt"])

    def test_plain_schemas_and_commented_directives_still_parse(self):
        for comment in ("", '// #base "missing.txt"\n'):
            with self.subTest(comment=comment):
                path = self.write("heroes.txt", comment + '''"DOTAHeroes" {
                    "npc_dota_hero_axe" { "Model" "models/heroes/axe/axe.vmdl" }
                }''')
                self.assertEqual(load_hero_models(path)["npc_dota_hero_axe"], "models/heroes/axe/axe.vmdl")
