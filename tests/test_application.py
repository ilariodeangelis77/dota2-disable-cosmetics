"""Application-service orchestration guards."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dota_disabler import application
from dota_disabler.constants import CATEGORY_STANDARD_WEARABLES
from dota_disabler.domain import BuildOptions, Plan
from dota_disabler.errors import GeneratorError


class BuildOrchestrationTests(unittest.TestCase):
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
