"""Executable dependency and launcher contracts for the modular package."""

from __future__ import annotations

import ast
import subprocess
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "dota_disabler"


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                modules.add(node.module)
            else:
                modules.update(alias.name for alias in node.names)
    return modules


class ArchitectureContractTests(unittest.TestCase):
    def test_package_never_imports_the_legacy_launchers(self):
        forbidden = {"disable_cosmetics", "disabler_gui"}
        for path in PACKAGE_ROOT.rglob("*.py"):
            with self.subTest(module=path.relative_to(PROJECT_ROOT).as_posix()):
                self.assertTrue(imported_modules(path).isdisjoint(forbidden))

    def test_core_and_application_layers_do_not_depend_on_adapters(self):
        adapter_names = {"cli", "gui", "gui_engine", "gui_model", "public"}
        lower_layers = (
            "constants.py",
            "deployment.py",
            "domain.py",
            "errors.py",
            "keyvalues.py",
            "paths.py",
            "reporting.py",
            "resources.py",
            "schema.py",
            "versioning.py",
            "vpk.py",
            "application.py",
        )
        for relative in lower_layers:
            modules = imported_modules(PACKAGE_ROOT / relative)
            imported_leaf_names = {name.rsplit(".", 1)[-1] for name in modules}
            with self.subTest(module=relative):
                self.assertTrue(imported_leaf_names.isdisjoint(adapter_names))

    def test_gui_adapters_do_not_depend_on_cli_or_public_facades(self):
        for relative in ("gui.py", "gui_engine.py", "gui_model.py"):
            modules = imported_modules(PACKAGE_ROOT / relative)
            imported_leaf_names = {name.rsplit(".", 1)[-1] for name in modules}
            with self.subTest(module=relative):
                self.assertTrue(imported_leaf_names.isdisjoint({"cli", "public"}))

    def test_planning_layers_do_not_depend_on_services_or_adapters(self):
        forbidden = {
            "application",
            "cli",
            "deployment",
            "gui",
            "gui_engine",
            "gui_model",
            "public",
            "versioning",
            "vpk",
        }
        for path in (PACKAGE_ROOT / "planning").glob("*.py"):
            modules = imported_modules(path)
            imported_leaf_names = {name.rsplit(".", 1)[-1] for name in modules}
            with self.subTest(module=path.name):
                self.assertTrue(imported_leaf_names.isdisjoint(forbidden))

    def test_root_launchers_remain_thin_and_definition_free(self):
        for name in ("disable_cosmetics.py", "disabler_gui.py"):
            path = PROJECT_ROOT / name
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            maintained_definitions = [
                node
                for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            ]
            with self.subTest(launcher=name):
                self.assertLessEqual(len(source.splitlines()), 60)
                self.assertEqual(maintained_definitions, [])

    def test_public_and_view_model_imports_do_not_load_tk(self):
        for module_name in ("dota_disabler.public", "dota_disabler.gui_model"):
            process = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        f"import {module_name}, sys; "
                        "raise SystemExit(1 if 'tkinter' in sys.modules else 0)"
                    ),
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            with self.subTest(module=module_name):
                self.assertEqual(process.returncode, 0, process.stderr)


if __name__ == "__main__":
    unittest.main()
