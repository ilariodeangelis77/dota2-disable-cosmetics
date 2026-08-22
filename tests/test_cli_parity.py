"""Subprocess compatibility between the legacy launcher and package entry point."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LEGACY_COMMAND = [sys.executable, str(PROJECT_ROOT / "disable_cosmetics.py")]
PACKAGE_COMMAND = [sys.executable, "-m", "dota_disabler"]


def run_entrypoint(prefix: list[str], arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*prefix, *arguments],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


class CliEntrypointParityTests(unittest.TestCase):
    def test_version_exit_code_and_semantic_version_match(self):
        legacy = run_entrypoint(LEGACY_COMMAND, ["--version"])
        package = run_entrypoint(PACKAGE_COMMAND, ["--version"])

        self.assertEqual((legacy.returncode, package.returncode), (0, 0))
        self.assertEqual(legacy.stdout.split()[-1], package.stdout.split()[-1])

    def test_empty_history_json_is_identical(self):
        with tempfile.TemporaryDirectory() as temporary:
            arguments = ["history", "--work", temporary, "--json"]
            legacy = run_entrypoint(LEGACY_COMMAND, arguments)
            package = run_entrypoint(PACKAGE_COMMAND, arguments)

        self.assertEqual((legacy.returncode, package.returncode), (0, 0))
        self.assertEqual(json.loads(legacy.stdout), json.loads(package.stdout))

    def test_parser_error_exit_codes_match(self):
        arguments = ["build", "--category", "not-a-category"]
        legacy = run_entrypoint(LEGACY_COMMAND, arguments)
        package = run_entrypoint(PACKAGE_COMMAND, arguments)

        self.assertEqual((legacy.returncode, package.returncode), (2, 2))
        self.assertIn("invalid choice", legacy.stderr)
        self.assertIn("invalid choice", package.stderr)


if __name__ == "__main__":
    unittest.main()
