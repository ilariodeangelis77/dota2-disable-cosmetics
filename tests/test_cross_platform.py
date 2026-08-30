import tempfile
import unittest
from pathlib import Path

from dota_disabler import versioning


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class SteamDiscoveryContractTests(unittest.TestCase):
    def test_default_posix_roots_cover_linux_flatpak_and_macos(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            self.assertEqual(
                versioning._steam_roots_posix("darwin", home),
                [home / "Library/Application Support/Steam"],
            )
            self.assertEqual(
                versioning._steam_roots_posix("linux", home),
                [
                    home / ".steam/steam",
                    home / ".local/share/Steam",
                    home / ".var/app/com.valvesoftware.Steam/data/Steam",
                ],
            )

    def test_posix_libraryfolders_adds_secondary_dota_install(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            steam_root = root / "Steam"
            secondary = root / "Secondary Library"
            library_file = steam_root / "steamapps/libraryfolders.vdf"
            library_file.parent.mkdir(parents=True)
            library_file.write_text(
                f'"libraryfolders" {{ "1" {{ "path" "{secondary.as_posix()}" }} }}\n',
                encoding="utf-8",
            )

            candidates = versioning._dota_install_candidates(
                [steam_root],
                windows_paths=False,
            )

            self.assertEqual(candidates[0], steam_root / "steamapps/common/dota 2 beta")
            self.assertIn(secondary / "steamapps/common/dota 2 beta", candidates)


class ReleaseWorkflowContractTests(unittest.TestCase):
    def test_desktop_progress_bar_uses_determinate_build_updates(self):
        gui = (PROJECT_ROOT / "dota_disabler/gui.py").read_text(encoding="utf-8")

        self.assertIn('mode="determinate"', gui)
        self.assertIn("progress_update=lambda percent, message", gui)
        self.assertIn('f"{percent:.1f}%"', gui)
        self.assertNotIn("self.progress.start(", gui)
        self.assertNotIn("self.progress.stop(", gui)

    def test_native_release_matrix_and_archive_formats_are_kept(self):
        workflow = (PROJECT_ROOT / ".github/workflows/build-releases.yml").read_text(
            encoding="utf-8"
        )
        for runner, runtime, archive in (
            ("windows-latest", "win-x64", "zip"),
            ("ubuntu-22.04", "linux-x64", "tar.gz"),
            ("macos-15-intel", "osx-x64", "tar.gz"),
            ("macos-15", "osx-arm64", "tar.gz"),
        ):
            with self.subTest(runtime=runtime):
                self.assertIn(f"runner: {runner}", workflow)
                self.assertIn(f"runtime: {runtime}", workflow)
                self.assertIn(f"archive: {archive}", workflow)
        self.assertIn("actions/upload-artifact@v6", workflow)
        self.assertIn("xvfb-run --auto-servernum", workflow)

    def test_tag_builds_create_only_a_verified_draft_release(self):
        workflow = (PROJECT_ROOT / ".github/workflows/build-releases.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("Verify release tag matches application version", workflow)
        self.assertIn("needs: build", workflow)
        self.assertIn("actions/download-artifact@v6", workflow)
        self.assertIn("sha256sum --check", workflow)
        self.assertIn("contents: write", workflow)
        self.assertIn('gh release view "$tag" --json isDraft', workflow)
        self.assertIn('release create "$tag" "${assets[@]}"', workflow)
        self.assertIn("--draft", workflow)
        self.assertIn("create_args+=(--prerelease)", workflow)
        self.assertIn('gh release upload "$tag" "${assets[@]}" --clobber', workflow)
        self.assertIn("Refusing to overwrite the already-published release", workflow)

    def test_release_script_rejects_cross_compilation_and_preserves_posix_modes(self):
        script = (PROJECT_ROOT / "scripts/build_release.ps1").read_text(encoding="utf-8")
        for runtime in ("win-x64", "linux-x64", "osx-x64", "osx-arm64"):
            self.assertIn(f'"{runtime}"', script)
        self.assertIn("must be built on its native", script)
        self.assertIn('if ($onWindows) { ".zip" } else { ".tar.gz" }', script)
        self.assertIn("chmod +x", script)
        self.assertIn("tar -czf", script)


class GitHubCommunityContractTests(unittest.TestCase):
    def test_bug_report_form_collects_live_validation_context(self):
        form_path = PROJECT_ROOT / ".github/ISSUE_TEMPLATE/bug-report.yml"
        form = form_path.read_text(encoding="utf-8")

        self.assertTrue(form.startswith("name: Cosmetic replacement bug\n"))
        for field_id in (
            "disabler-version",
            "platform",
            "dota-build",
            "language-mount",
            "categories",
            "observed-in",
            "cosmetic",
            "expected",
            "actual",
            "regression",
            "reproduction",
            "activity-log",
            "attachments",
            "confirmations",
        ):
            with self.subTest(field_id=field_id):
                self.assertEqual(form.count(f"    id: {field_id}\n"), 1)
        self.assertIn("Do not upload Dota game files", form)
        self.assertIn("I rebuilt the overrides after the current Dota update.", form)


if __name__ == "__main__":
    unittest.main()
