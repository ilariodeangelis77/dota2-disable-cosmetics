"""Compatibility contracts that must survive the package refactor."""

from __future__ import annotations

import argparse
import importlib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import disable_cosmetics as legacy


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class PublicFacadeContractTests(unittest.TestCase):
    """Keep the root module usable by the GUI and existing integrations."""

    def test_root_facade_reexports_canonical_public_types_and_services(self):
        public = importlib.import_module("dota_disabler.public")
        public_symbols = (
            "BuildOptions",
            "BuildResult",
            "CleanResult",
            "GeneratorError",
            "ItemRecord",
            "Mapping",
            "Plan",
            "UnsafeOutputError",
            "application_root",
            "apply_missing_particle_fallbacks",
            "apply_model_skin_material_fallbacks",
            "build_cosmetics",
            "build_plan",
            "clean_cosmetics",
            "dota_version_label",
            "find_dota_install",
            "get_status",
            "validate_language",
            "write_json",
        )

        for name in public_symbols:
            with self.subTest(name=name):
                self.assertIs(getattr(legacy, name), getattr(public, name))

    def test_root_facade_preserves_gui_configuration_constants(self):
        public = importlib.import_module("dota_disabler.public")
        constant_names = (
            "CATEGORY_ADDITIONAL_WEARABLES",
            "CATEGORY_PARTICLE_EFFECTS",
            "CATEGORY_PERSONA_MODELS",
            "CATEGORY_SPECIAL_MODELS",
            "CATEGORY_STANDARD_WEARABLES",
            "DEFAULT_CATEGORIES",
            "DEFAULT_LANGUAGE",
            "RECOGNIZED_LANGUAGES",
            "SUPPORTED_CATEGORIES",
        )

        for name in constant_names:
            with self.subTest(name=name):
                self.assertEqual(getattr(legacy, name), getattr(public, name))

    def test_root_and_package_report_one_semantic_version(self):
        version_module = importlib.import_module("dota_disabler.version")

        self.assertIsInstance(legacy.VERSION, str)
        self.assertRegex(
            legacy.VERSION,
            r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*)?$",
        )
        self.assertEqual(legacy.VERSION, version_module.VERSION)


class CliContractTests(unittest.TestCase):
    @staticmethod
    def command_names(parser: argparse.ArgumentParser) -> set[str]:
        subparsers = next(
            action
            for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        )
        return set(subparsers.choices)

    def test_cli_retains_all_existing_commands(self):
        self.assertEqual(
            self.command_names(legacy.make_parser()),
            {"analyze", "build", "clean", "status", "history", "gui"},
        )

    def test_build_parser_defaults_and_repeatable_categories_are_stable(self):
        parser = legacy.make_parser()
        defaults = parser.parse_args(["build"])
        self.assertEqual(defaults.language, legacy.DEFAULT_LANGUAGE)
        self.assertTrue(defaults.clean_first)
        self.assertFalse(defaults.allow_missing)
        self.assertIsNone(defaults.category)

        selected = parser.parse_args(
            [
                "build",
                "--language",
                "finnish",
                "--no-clean-first",
                "--allow-missing",
                "--category",
                legacy.CATEGORY_STANDARD_WEARABLES,
                "--category",
                legacy.CATEGORY_PARTICLE_EFFECTS,
            ]
        )
        self.assertEqual(selected.language, "finnish")
        self.assertFalse(selected.clean_first)
        self.assertTrue(selected.allow_missing)
        self.assertEqual(
            selected.category,
            [legacy.CATEGORY_STANDARD_WEARABLES, legacy.CATEGORY_PARTICLE_EFFECTS],
        )


class RuntimePathContractTests(unittest.TestCase):
    def test_source_execution_keeps_assets_and_work_beside_the_root_launcher(self):
        with patch.object(sys, "frozen", False, create=True), patch.object(
            sys, "_MEIPASS", None, create=True
        ):
            self.assertEqual(legacy.runtime_asset_root(), PROJECT_ROOT)
            self.assertEqual(legacy.application_root(), PROJECT_ROOT)
            self.assertEqual(legacy.application_root() / ".work", PROJECT_ROOT / ".work")

    def test_frozen_execution_separates_bundled_assets_from_persistent_work(self):
        fake_bundle = PROJECT_ROOT / "synthetic-pyinstaller-bundle"
        fake_executable = PROJECT_ROOT / "portable" / "Dota2CosmeticDisabler.exe"
        with patch.object(sys, "frozen", True, create=True), patch.object(
            sys, "_MEIPASS", str(fake_bundle), create=True
        ), patch.object(sys, "executable", str(fake_executable)):
            self.assertEqual(legacy.runtime_asset_root(), fake_bundle.resolve())
            self.assertEqual(legacy.application_root(), fake_executable.parent.resolve())
            self.assertNotEqual(legacy.application_root(), legacy.runtime_asset_root())

    def test_resource_path_facade_preserves_canonical_and_safe_semantics(self):
        self.assertEqual(
            legacy.compiled_override_path(r"Models\Items\Test\Head.VMDL"),
            "models/items/test/head.vmdl_c",
        )
        for unsafe in ("../outside.vmdl", "/outside.vmdl", "C:/outside.vmdl"):
            with self.subTest(unsafe=unsafe), self.assertRaises(ValueError):
                legacy.compiled_model_path(unsafe)


if __name__ == "__main__":
    unittest.main()
