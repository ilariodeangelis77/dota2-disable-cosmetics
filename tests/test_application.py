"""Application-service orchestration guards."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dota_disabler import application
from dota_disabler.constants import CATEGORY_STANDARD_WEARABLES
from dota_disabler.domain import (
    BuildOptions,
    ModelAttachmentOffset,
    ModelComposition,
    Plan,
)
from dota_disabler.errors import GeneratorError


class BuildOrchestrationTests(unittest.TestCase):
    def test_attachment_offset_source_is_included_in_vpk_extraction(self):
        adjustment = ModelAttachmentOffset(
            source="models/heroes/test/loadout_rig.vmdl",
            target="models/heroes/test/loadout_rig.vmdl",
            attachments=("attach_orb1",),
            offset=(0.0, 0.0, 40.0),
            reason="reviewed test offset",
            category="persona_models",
            item_id="10",
            hero="npc_dota_hero_test",
            slot="head_persona_1",
        )
        plan = Plan(
            mappings=[],
            unresolved=[],
            stats={},
            model_attachment_offsets=[adjustment],
        )

        self.assertEqual(
            application._source_resources_for_plan(plan),
            {"models/heroes/test/loadout_rig.vmdl_c"},
        )

    def test_composition_sources_are_included_in_vpk_extraction(self):
        composition = ModelComposition(
            primary_source="models/heroes/test/head.vmdl",
            secondary_source="models/heroes/test/cape.vmdl",
            target="models/items/test/composed.vmdl",
            reason="reviewed test composition",
            category="persona_models",
            item_id="10",
            hero="npc_dota_hero_test",
            slot="back_persona_1",
        )
        plan = Plan(
            mappings=[],
            unresolved=[],
            stats={},
            model_compositions=[composition],
        )

        self.assertEqual(
            application._source_resources_for_plan(plan),
            {
                "models/heroes/test/head.vmdl_c",
                "models/heroes/test/cape.vmdl_c",
            },
        )

    def test_successful_build_reports_monotonic_stage_progress(self):
        dota_version = {
            "steam_build_id": "100",
            "pak01_dir": {"size_bytes": 10, "mtime_ns": 20},
        }
        plan = Plan(
            mappings=[],
            unresolved=[],
            stats={
                "model_overrides": 0,
                "particle_overrides": 0,
                "particle_snapshot_overrides": 0,
            },
        )
        updates: list[tuple[float, str]] = []

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dota = root / "dota 2 beta"
            options = BuildOptions(
                dota=str(dota),
                extractor="synthetic-extractor",
                work=str(root / "work"),
                enabled_categories=frozenset({CATEGORY_STANDARD_WEARABLES}),
            )
            schemas = tuple(
                root / name for name in ("items.txt", "heroes.txt", "units.txt")
            )

            def fake_deploy(*_args, progress_update, **_kwargs):
                progress_update(0, "Preparing override files")
                progress_update(50.5, "Staged override files")
                progress_update(100, "Override VPK installed")
                return 0, []

            with (
                patch.object(
                    application,
                    "capture_dota_version",
                    side_effect=[dota_version, dota_version],
                ),
                patch.object(
                    application,
                    "find_vpk_extractor",
                    return_value=root / "extractor.exe",
                ),
                patch.object(application, "validate_vpk_extractor"),
                patch.object(application, "read_marker", return_value=None),
                patch.object(application, "read_version_history", return_value=None),
                patch.object(application, "load_or_extract_schema", return_value=schemas),
                patch.object(application, "parse_schemas", return_value=plan),
                patch.object(application, "write_plan"),
                patch.object(application, "extract_vpk"),
                patch.object(
                    application,
                    "apply_model_skin_material_fallbacks",
                    return_value=plan,
                ),
                patch.object(
                    application,
                    "apply_missing_particle_fallbacks",
                    return_value=plan,
                ),
                patch.object(application, "deploy_overrides", side_effect=fake_deploy),
                patch.object(application, "clean_legacy_output_after_migration"),
                patch.object(application, "clean_other_language_outputs_after_migration"),
                patch.object(application, "safely_append_version_history", return_value=True),
            ):
                application._build_cosmetics_unlocked(
                    options,
                    progress=lambda _message: None,
                    progress_update=lambda percent, message: updates.append(
                        (percent, message)
                    ),
                    resolved_dota=dota,
                )

        percentages = [percent for percent, _message in updates]
        self.assertEqual(percentages, sorted(percentages))
        self.assertEqual(percentages[0], 0)
        self.assertEqual(percentages[-1], 100)
        self.assertTrue(any(not percent.is_integer() for percent in percentages))
        self.assertIn(
            "Staged override files",
            {message for _percent, message in updates},
        )

    def test_dota_change_after_planning_aborts_before_deploy_and_history(self):
        initial_version = {
            "steam_build_id": "100",
            "pak01_dir": {"size_bytes": 10, "mtime_ns": 20},
        }
        latest_version = {
            "steam_build_id": "101",
            "pak01_dir": {"size_bytes": 11, "mtime_ns": 21},
        }
        plan = Plan(mappings=[], unresolved=[], stats={})

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dota = root / "dota 2 beta"
            options = BuildOptions(
                dota=str(dota),
                extractor="synthetic-extractor",
                work=str(root / "work"),
                enabled_categories=frozenset({CATEGORY_STANDARD_WEARABLES}),
            )
            schemas = tuple(root / name for name in ("items.txt", "heroes.txt", "units.txt"))

            with (
                patch.object(
                    application,
                    "capture_dota_version",
                    side_effect=[initial_version, latest_version],
                ),
                patch.object(
                    application,
                    "find_vpk_extractor",
                    return_value=root / "extractor.exe",
                ),
                patch.object(application, "validate_vpk_extractor"),
                patch.object(application, "read_marker", return_value=None),
                patch.object(application, "load_or_extract_schema", return_value=schemas),
                patch.object(application, "parse_schemas", return_value=plan),
                patch.object(application, "write_plan"),
                patch.object(application, "extract_vpk"),
                patch.object(
                    application,
                    "apply_model_skin_material_fallbacks",
                    return_value=plan,
                ),
                patch.object(
                    application,
                    "apply_missing_particle_fallbacks",
                    return_value=plan,
                ),
                patch.object(application, "deploy_overrides") as deploy,
                patch.object(application, "safely_append_version_history") as append_history,
            ):
                with self.assertRaisesRegex(GeneratorError, "Dota changed"):
                    application._build_cosmetics_unlocked(
                        options,
                        progress=lambda _message: None,
                        resolved_dota=dota,
                    )

            deploy.assert_not_called()
            append_history.assert_not_called()
            self.assertFalse((dota / "game/dota_dutch").exists())


if __name__ == "__main__":
    unittest.main()
