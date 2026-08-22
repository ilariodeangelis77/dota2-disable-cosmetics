"""Fail-first specifications for the confirmed bundle/skin regression."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

import disable_cosmetics as engine

from helpers import TEST_HERO, make_item, write_model_dependencies


def mapping_by_target(plan: engine.Plan) -> dict[str, engine.Mapping]:
    return {mapping.target: mapping for mapping in plan.mappings}


class BundleSkinRegressionTests(unittest.TestCase):
    def test_whitewind_like_bundle_does_not_taint_unskinned_siblings(self):
        default_head = make_item(
            1,
            slot="head",
            name="Default Head",
            baseitem=True,
            model="models/heroes/test/default_head.vmdl",
        )
        default_back = make_item(
            2,
            slot="back",
            name="Default Back",
            baseitem=True,
            model="models/heroes/test/default_back.vmdl",
        )
        styled_head = make_item(
            3,
            slot="head",
            name="Whitewind Head",
            model="models/items/test/whitewind_head.vmdl",
            has_nondefault_skin=True,
        )
        ordinary_back = make_item(
            4,
            slot="back",
            name="Whitewind Back",
            model="models/items/test/whitewind_back.vmdl",
        )
        bundle = make_item(
            5,
            slot="",
            name="Whitewind Battlemage",
            hero=None,
            bundle_members=("Whitewind Head", "Whitewind Back"),
        )

        plan = engine.build_plan(
            {},
            {
                record.item_id: record
                for record in (default_head, default_back, styled_head, ordinary_back, bundle)
            },
            {TEST_HERO: "models/heroes/test/test.vmdl"},
            [],
        )
        by_target = mapping_by_target(plan)

        self.assertTrue(by_target["models/items/test/whitewind_head.vmdl"].neutralize_model_skin)
        self.assertFalse(by_target["models/items/test/whitewind_back.vmdl"].neutralize_model_skin)

    def test_abominable_like_four_piece_set_keeps_every_default_model(self):
        slots = ("head", "back", "arms", "belt")
        defaults = [
            make_item(
                index,
                slot=slot,
                name=f"Default {slot}",
                baseitem=True,
                model=f"models/heroes/test/default_{slot}.vmdl",
            )
            for index, slot in enumerate(slots, start=1)
        ]
        pieces = [
            make_item(
                index,
                slot=slot,
                name=f"Snowbeast {slot}",
                model=f"models/items/test/snowbeast_{slot}.vmdl",
                has_nondefault_skin=slot == "head",
            )
            for index, slot in enumerate(slots, start=10)
        ]
        bundle = make_item(
            20,
            slot="",
            name="The Abominable Snowbeast",
            hero=None,
            bundle_members=tuple(piece.name for piece in pieces),
        )
        plan = engine.build_plan(
            {},
            {record.item_id: record for record in (*defaults, *pieces, bundle)},
            {TEST_HERO: "models/heroes/test/test.vmdl"},
            [],
        )

        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            for default in defaults:
                write_model_dependencies(
                    cache,
                    default.top_models[0][1],
                    [f"materials/models/heroes/test/{default.item_slot}.vmat"],
                )
            adjusted = engine.apply_model_skin_material_fallbacks(plan, cache)

        by_target = mapping_by_target(adjusted)
        for slot in slots:
            with self.subTest(slot=slot):
                target = f"models/items/test/snowbeast_{slot}.vmdl"
                self.assertIn(target, by_target)
                self.assertEqual(
                    by_target[target].source,
                    f"models/heroes/test/default_{slot}.vmdl",
                )


class MaterialFallbackRegressionTests(unittest.TestCase):
    @staticmethod
    def skin_mapping(*, source: str, target: str) -> engine.Mapping:
        return engine.Mapping(
            source=source,
            target=target,
            reason="wearable replaced with slot default",
            category=engine.CATEGORY_STANDARD_WEARABLES,
            resource_type=engine.RESOURCE_MODEL,
            item_id="42",
            hero=TEST_HERO,
            slot="head",
            neutralize_model_skin=True,
            required_material_groups=2,
        )

    @staticmethod
    def plan_for(mapping: engine.Mapping) -> engine.Plan:
        return engine.Plan(
            mappings=[mapping],
            unresolved=[],
            stats={"resource_overrides": 1, "mapping_conflicts": 0, "unresolved": 0},
        )

    def test_unresolved_materials_do_not_remove_a_distinct_model_replacement(self):
        mapping = self.skin_mapping(
            source="models/heroes/test/default_head.vmdl",
            target="models/items/test/skinned_head.vmdl",
        )
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            write_model_dependencies(
                cache,
                mapping.source,
                ["materials/models/heroes/test/head.vmat"],
            )
            adjusted = engine.apply_model_skin_material_fallbacks(self.plan_for(mapping), cache)

        model_mappings = [
            candidate
            for candidate in adjusted.mappings
            if candidate.resource_type == engine.RESOURCE_MODEL
        ]
        self.assertEqual(model_mappings, [mapping])
        self.assertEqual(adjusted.unresolved, [])
        self.assertEqual(adjusted.stats["alternate_skin_group_patch_targets"], 1)

    def test_unresolved_skin_only_noop_does_not_create_a_model_override(self):
        model = "models/heroes/test/default_head.vmdl"
        mapping = self.skin_mapping(source=model, target=model)
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            write_model_dependencies(
                cache,
                model,
                ["materials/models/heroes/test/head.vmat"],
            )
            adjusted = engine.apply_model_skin_material_fallbacks(self.plan_for(mapping), cache)

        self.assertFalse(
            any(candidate.resource_type == engine.RESOURCE_MODEL for candidate in adjusted.mappings)
        )
        self.assertTrue(
            any(diagnostic.get("target") == model for diagnostic in adjusted.unresolved)
        )

    def test_intentionally_invisible_skin_target_needs_no_material_group(self):
        mapping = engine.Mapping(
            source=engine.INVISIBLE_MODEL,
            target="models/items/test/hidden_attachment.vmdl",
            reason="additional wearable replaced or hidden",
            category=engine.CATEGORY_ADDITIONAL_WEARABLES,
            resource_type=engine.RESOURCE_MODEL,
            item_id="43",
            hero=TEST_HERO,
            slot="back",
            neutralize_model_skin=True,
        )
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            compiled = cache / engine.compiled_model_path(engine.INVISIBLE_MODEL)
            compiled.parent.mkdir(parents=True)
            compiled.write_bytes(b"compiled zero-geometry model")
            adjusted = engine.apply_model_skin_material_fallbacks(
                self.plan_for(mapping), cache
            )

        self.assertEqual(adjusted.mappings, [mapping])
        self.assertEqual(adjusted.unresolved, [])
        self.assertEqual(adjusted.stats["alternate_skin_group_patch_targets"], 0)


class PlannerDeterminismTests(unittest.TestCase):
    @staticmethod
    def conflicting_records() -> tuple[list[engine.ItemRecord], dict[str, str]]:
        hero_a = "npc_dota_hero_alpha"
        hero_b = "npc_dota_hero_beta"
        shared_target = "models/items/shared/conflicting_head.vmdl"
        records = [
            make_item(
                1,
                slot="head",
                hero=hero_a,
                baseitem=True,
                model="models/heroes/alpha/default_head.vmdl",
            ),
            make_item(
                2,
                slot="head",
                hero=hero_b,
                baseitem=True,
                model="models/heroes/beta/default_head.vmdl",
            ),
            make_item(3, slot="head", hero=hero_a, model=shared_target),
            make_item(4, slot="head", hero=hero_b, model=shared_target),
        ]
        return records, {
            hero_a: "models/heroes/alpha/alpha.vmdl",
            hero_b: "models/heroes/beta/beta.vmdl",
        }

    def test_equal_priority_conflict_is_independent_of_schema_order(self):
        records, hero_models = self.conflicting_records()
        forward = engine.build_plan(
            {}, {record.item_id: record for record in records}, hero_models, []
        )
        reverse = engine.build_plan(
            {}, {record.item_id: record for record in reversed(records)}, hero_models, []
        )

        self.assertEqual(
            [asdict(mapping) for mapping in forward.mappings],
            [asdict(mapping) for mapping in reverse.mappings],
        )
        self.assertEqual(forward.unresolved, reverse.unresolved)
        conflicts = [
            diagnostic
            for diagnostic in forward.unresolved
            if diagnostic.get("type") == "mapping_conflict"
        ]
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(
            conflicts[0]["candidate_sources"],
            [
                "models/heroes/alpha/default_head.vmdl",
                "models/heroes/beta/default_head.vmdl",
            ],
        )


if __name__ == "__main__":
    unittest.main()
