"""Manifest-owned VPK deployment, status, migration, and cleanup."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from .constants import (
    DEFAULT_LANGUAGE,
    ITEMS_SCHEMA_RESOURCE,
    LEGACY_LANGUAGE,
    MARKER_FILENAME,
    MARKER_KIND,
    MODEL_CATEGORIES,
    RECOGNIZED_LANGUAGES,
    RESOURCE_MODEL,
    SUPPORTED_CATEGORIES,
    VPK_ARCHIVE_CANDIDATES,
    VPK_DEPLOYMENT_MODE,
)
from .domain import CleanResult, Plan, ProgressCallback, ProgressUpdateCallback
from .errors import GeneratorError, UnsafeOutputError
from .model_patcher import patch_model_material_groups_batch
from .paths import path_under
from .progress import DEPLOYMENT_PHASE_WEIGHTS, WeightedProgress
from .reporting import write_json
from .resources import (
    compiled_model_path,
    compiled_override_path,
    is_safe_resource_path,
)
from .version import VERSION
from .versioning import (
    capture_dota_version,
    compare_dota_versions,
    dota_operation_lock,
    find_dota_install,
)
from .vpk import pack_vpk, stage_english_language_support


def read_marker(output_root: Path, *, allow_shared_directory: bool = False) -> Optional[dict]:
    if not output_root.exists():
        return None
    marker_path = output_root / MARKER_FILENAME
    if not marker_path.is_file():
        if not allow_shared_directory and any(output_root.iterdir()):
            raise UnsafeOutputError(
                f"Refusing to modify non-empty directory without {MARKER_FILENAME}: {output_root}"
            )
        return None
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UnsafeOutputError(f"Could not validate generator marker: {marker_path}") from exc
    files = marker.get("files")
    if (
        marker.get("kind") != MARKER_KIND
        or not isinstance(files, list)
        or not all(isinstance(item, str) for item in files)
    ):
        raise UnsafeOutputError(f"Invalid generator marker: {marker_path}")
    if marker.get("deployment_mode") == VPK_DEPLOYMENT_MODE:
        if len(files) != 1 or files[0] not in VPK_ARCHIVE_CANDIDATES:
            raise UnsafeOutputError(f"Invalid owned VPK entry in marker: {marker_path}")
        path_under(output_root, files[0])
        resources = marker.get("resources")
        if not isinstance(resources, list) or not all(
            isinstance(item, str) for item in resources
        ):
            raise UnsafeOutputError(f"Invalid VPK resource list in marker: {marker_path}")
        for relative in resources:
            try:
                normalized = compiled_override_path(relative)
            except ValueError as exc:
                raise UnsafeOutputError(
                    f"Invalid VPK resource in marker: {relative!r}"
                ) from exc
            if relative != normalized:
                raise UnsafeOutputError(
                    f"Non-canonical VPK resource in marker: {relative!r}"
                )
        support_resources = marker.get("support_resources", [])
        if (
            not isinstance(support_resources, list)
            or not all(
                isinstance(item, str)
                and is_safe_resource_path(item)
                and bool(re.search(r"_[a-z]+\.(?:txt|vtt)$", item))
                for item in support_resources
            )
        ):
            raise UnsafeOutputError(
                f"Invalid language-support resource list in marker: {marker_path}"
            )
        schema_resources = marker.get("schema_resources", [])
        if schema_resources not in ([], [ITEMS_SCHEMA_RESOURCE]):
            raise UnsafeOutputError(f"Invalid schema resource list in marker: {marker_path}")
        archive_sha256 = marker.get("archive_sha256")
        if not isinstance(archive_sha256, str) or not re.fullmatch(
            r"[0-9a-f]{64}", archive_sha256
        ):
            raise UnsafeOutputError(f"Invalid VPK checksum in marker: {marker_path}")
    else:
        for relative in files:
            try:
                normalized = compiled_model_path(relative)
            except ValueError as exc:
                raise UnsafeOutputError(
                    f"Invalid generated file in marker: {relative!r}"
                ) from exc
            if relative != normalized:
                raise UnsafeOutputError(
                    f"Non-canonical generated file in marker: {relative!r}"
                )
            path_under(output_root, relative)
    return marker


def choose_vpk_archive_name(output_root: Path, marker: Optional[dict]) -> str:
    if marker and marker.get("deployment_mode") == VPK_DEPLOYMENT_MODE:
        current = marker["files"][0]
        if current in VPK_ARCHIVE_CANDIDATES:
            return current
    for candidate in VPK_ARCHIVE_CANDIDATES:
        if not (output_root / candidate).exists():
            return candidate
    raise UnsafeOutputError(
        f"No free owned VPK slot is available under {output_root}; "
        "the tool will not overwrite pak90 through pak98."
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def marker_enabled_categories(marker: dict) -> set[str]:
    recorded = marker.get("enabled_categories")
    if (
        isinstance(recorded, list)
        and all(isinstance(category, str) for category in recorded)
        and set(recorded).issubset(SUPPORTED_CATEGORIES)
    ):
        return set(recorded)
    # Markers written before category tracking represented the original all-model build.
    return set(MODEL_CATEGORIES)


def validate_category_transition(
    marker: Optional[dict],
    enabled_categories: Iterable[str],
    *,
    clean_first: bool,
) -> None:
    if marker is None or clean_first:
        return
    if marker.get("deployment_mode") == VPK_DEPLOYMENT_MODE:
        # A VPK is replaced as one archive, so unselected categories cannot be
        # retained accidentally even when the compatibility flag is supplied.
        return
    previous_categories = marker_enabled_categories(marker)
    selected_categories = set(enabled_categories)
    disabled_but_retained = previous_categories.difference(selected_categories)
    if disabled_but_retained:
        raise GeneratorError(
            "--no-clean-first cannot disable replacement categories because their old files would remain active. "
            "Run with --clean-first when changing to a smaller category selection."
        )


def remove_tracked_files(output_root: Path, files: Iterable[str]) -> None:
    parents: set[Path] = set()
    for relative in files:
        target = path_under(output_root, relative)
        if target.exists() and not target.is_file():
            raise UnsafeOutputError(
                f"Expected a generated file but found another file type: {target}"
            )
        if target.is_file():
            target.unlink()
        parent = target.parent
        while parent != output_root and output_root in parent.parents:
            parents.add(parent)
            parent = parent.parent
    for directory in sorted(
        parents,
        key=lambda candidate: len(candidate.parts),
        reverse=True,
    ):
        try:
            directory.rmdir()
        except OSError:
            pass


def deploy_overrides(
    plan: Plan,
    cache: Path,
    output_root: Path,
    work: Path,
    *,
    extractor: Path,
    model_patcher: Optional[Path] = None,
    game_pak: Optional[Path] = None,
    items_schema: Optional[Path] = None,
    clean_first: bool,
    allow_missing: bool,
    language: str,
    dota_version: Optional[dict] = None,
    generated_at_utc: Optional[str] = None,
    enabled_categories: Optional[Iterable[str]] = None,
    progress: ProgressCallback = print,
    progress_update: Optional[ProgressUpdateCallback] = None,
) -> tuple[int, list[dict]]:
    # ``items_schema`` remains in the API for compatibility with pre-0.7 callers;
    # current Dota ignores a recognized-language economy-schema overlay.
    del items_schema
    selected_categories = (
        set(enabled_categories)
        if enabled_categories is not None
        else {mapping.category for mapping in plan.mappings}
    )
    deployment_progress = WeightedProgress(progress_update, DEPLOYMENT_PHASE_WEIGHTS)
    deployment_progress.begin("staging", "Preparing override files")

    staging = work / "staging" / language
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    skin_patch_mappings = [
        mapping
        for mapping in plan.mappings
        if mapping.resource_type == RESOURCE_MODEL and mapping.required_material_groups > 1
    ]
    if skin_patch_mappings and model_patcher is None:
        raise GeneratorError(
            "Skin-sensitive replacements require the bundled model skin patcher, but it "
            "was not found. The Dota directory was not modified."
        )

    missing: list[dict] = []
    staged_files: list[str] = []
    patch_jobs: list[tuple[Path, Path, int]] = []
    mapping_count = len(plan.mappings)
    for index, mapping in enumerate(plan.mappings, start=1):
        source_relative = compiled_override_path(mapping.source, mapping.resource_type)
        target_relative = compiled_override_path(mapping.target, mapping.resource_type)
        source = path_under(cache, source_relative)
        if not source.is_file():
            missing.append(
                {
                    "source": mapping.source,
                    "target": mapping.target,
                    "reason": mapping.reason,
                    "resource_type": mapping.resource_type,
                    "item_id": mapping.item_id,
                }
            )
            deployment_progress.work(
                "staging",
                index,
                mapping_count,
                f"Staging overrides ({index:,} of {mapping_count:,})",
            )
            continue
        destination = path_under(staging, target_relative)
        if mapping.resource_type == RESOURCE_MODEL and mapping.required_material_groups > 1:
            patch_jobs.append((source, destination, mapping.required_material_groups))
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        staged_files.append(target_relative)
        deployment_progress.work(
            "staging",
            index,
            mapping_count,
            f"Staging overrides ({index:,} of {mapping_count:,})",
        )

    deployment_progress.complete(
        "staging", f"Staged {len(staged_files):,} override resource(s)"
    )

    missing_path = work / "missing-resources.json"
    legacy_missing_path = work / "missing-models.json"
    if legacy_missing_path.is_file():
        legacy_missing_path.unlink()
    if missing:
        write_json(missing_path, missing)
        if not allow_missing:
            raise GeneratorError(
                f"{len(missing)} replacement source resource(s) were not extracted. "
                f"The Dota directory was not modified; see {missing_path}. "
                "Use --allow-missing only if a partial build is intentional."
            )
    elif missing_path.is_file():
        missing_path.unlink()

    deployment_progress.begin("model_patch", "Preparing skin-sensitive default models")
    if patch_jobs:
        patch_model_material_groups_batch(
            model_patcher,
            patch_jobs,
            staging,
            progress=progress,
            progress_update=deployment_progress.work_callback(
                "model_patch", "Patching skin-sensitive models"
            ),
        )
    deployment_progress.complete("model_patch", "Default model preparation complete")

    def language_progress(operation: str, completed: int, total: int) -> None:
        phase = "language_stage" if operation == "stage" else "language_extract"
        label = (
            "Staging English interface resources"
            if operation == "stage"
            else "Extracting English interface resources"
        )
        deployment_progress.work(
            phase,
            completed,
            total,
            f"{label} ({completed:,} of {total:,})",
        )

    deployment_progress.begin("language_extract", "Adding English interface resources")
    support_resources = (
        stage_english_language_support(
            extractor,
            game_pak,
            work,
            staging,
            language,
            progress=progress,
            progress_update=language_progress,
        )
        if game_pak is not None
        else []
    )
    deployment_progress.complete(
        "language_extract", "English interface resources extracted"
    )
    deployment_progress.complete(
        "language_stage", "Language compatibility resources ready"
    )
    # Current Dota loads the economy schema before recognized-language VPK
    # overrides are considered. A structurally valid items_game.txt overlay was
    # therefore ignored in live testing. Bodygroup-sensitive wearables now use
    # model-only fallbacks and no schema copy is deployed.
    schema_resources: list[str] = []

    existing = read_marker(output_root, allow_shared_directory=True)
    validate_category_transition(existing, selected_categories, clean_first=clean_first)
    old_files = list(existing["files"]) if existing else []
    old_file_set = set(old_files)
    archive_name = choose_vpk_archive_name(output_root, existing)
    archive_destination = path_under(output_root, archive_name)
    if archive_destination.exists() and not archive_destination.is_file():
        raise UnsafeOutputError(
            f"Expected an owned VPK file but found another file type: {archive_destination}"
        )
    if archive_destination.is_file() and archive_name not in old_file_set:
        raise UnsafeOutputError(
            f"Refusing to overwrite a VPK that is not owned by this tool: {archive_destination}"
        )

    package_root = work / "package" / language
    if package_root.exists():
        shutil.rmtree(package_root)
    package_root.mkdir(parents=True)
    staged_archive = package_root / archive_name

    def archive_progress(operation: str, completed: int, total: int) -> None:
        phase = "verify" if operation == "verify" else "pack"
        label = (
            "CRC-validating override files"
            if operation == "verify"
            else "Packing override files"
        )
        deployment_progress.work(
            phase,
            completed,
            total,
            f"{label} ({completed:,} of {total:,})",
        )

    deployment_progress.begin("pack", "Packing the override VPK")
    packed = pack_vpk(
        extractor,
        staging,
        staged_archive,
        progress=progress,
        progress_update=archive_progress,
    )
    deployment_progress.complete("pack", "Override files packed")
    deployment_progress.complete("verify", "Override VPK packed and CRC-validated")
    unique_resources = sorted(set(staged_files))
    if packed != len(unique_resources) + len(support_resources) + len(schema_resources):
        raise GeneratorError("The VPK resource count does not match the staged override count.")

    output_root.mkdir(parents=True, exist_ok=True)
    temporary_destination = output_root / f".{archive_name}.{os.getpid()}.tmp"
    rollback_destination = output_root / f".{archive_name}.{os.getpid()}.rollback"
    if rollback_destination.exists():
        raise UnsafeOutputError(
            f"Refusing deployment because a rollback file already exists: {rollback_destination}"
        )
    obsolete_owned_files = [
        relative for relative in old_files if relative != archive_name
    ]
    obsolete_rollbacks: list[tuple[Path, Path]] = []
    for relative in obsolete_owned_files:
        target = path_under(output_root, relative)
        if target.exists() and not target.is_file():
            raise UnsafeOutputError(
                f"Expected an owned file but found another file type: {target}"
            )
        rollback = target.with_name(f".{target.name}.{os.getpid()}.rollback")
        if rollback.exists():
            raise UnsafeOutputError(
                f"Refusing deployment because a rollback file already exists: {rollback}"
            )
        if target.is_file():
            obsolete_rollbacks.append((target, rollback))
    deployment_progress.begin("install", "Installing the validated override VPK")
    committed = False
    try:
        if archive_destination.is_file():
            os.replace(archive_destination, rollback_destination)
        deployment_progress.work("install", 1, 6, "Securing the previous owned VPK")
        for target, rollback in obsolete_rollbacks:
            os.replace(target, rollback)
        deployment_progress.work("install", 2, 6, "Securing obsolete owned files")
        shutil.copy2(staged_archive, temporary_destination)
        deployment_progress.work("install", 3, 6, "Copying the validated override VPK")
        os.replace(temporary_destination, archive_destination)
        deployment_progress.work("install", 4, 6, "Activating the validated override VPK")

        write_json(
            output_root / MARKER_FILENAME,
            {
                "kind": MARKER_KIND,
                "generator_version": VERSION,
                "deployment_mode": VPK_DEPLOYMENT_MODE,
                "language": language,
                "generated_at_utc": generated_at_utc
                or datetime.now(timezone.utc).isoformat(),
                "dota_version": dota_version,
                "enabled_categories": sorted(selected_categories),
                "files": [archive_name],
                "resources": unique_resources,
                "support_resources": support_resources,
                "schema_resources": schema_resources,
                "archive_sha256": sha256_file(archive_destination),
            },
        )
        committed = True
        deployment_progress.work("install", 5, 6, "Recording generated-file ownership")
    except Exception:
        try:
            if archive_destination.is_file():
                archive_destination.unlink()
            if rollback_destination.is_file():
                os.replace(rollback_destination, archive_destination)
            for target, rollback in obsolete_rollbacks:
                if not rollback.is_file():
                    continue
                if target.exists():
                    raise OSError(f"Cannot restore owned file over existing path: {target}")
                os.replace(rollback, target)
        except OSError as rollback_error:
            raise UnsafeOutputError(
                "Deployment failed and the previous owned output could not be restored safely. "
                f"Inspect {output_root} before rebuilding."
            ) from rollback_error
        raise
    finally:
        if temporary_destination.is_file():
            temporary_destination.unlink()
        if committed and rollback_destination.is_file():
            try:
                rollback_destination.unlink()
            except OSError as exc:
                progress(
                    "WARNING: The new marker and VPK were committed, but the temporary "
                    f"rollback file could not be removed: {exc}"
                )
        if committed:
            for _target, rollback in obsolete_rollbacks:
                if not rollback.is_file():
                    continue
                try:
                    rollback.unlink()
                except OSError as exc:
                    progress(
                        "WARNING: The new marker and VPK were committed, but a temporary "
                        f"legacy rollback file could not be removed: {exc}"
                    )
            try:
                remove_tracked_files(output_root, obsolete_owned_files)
            except (OSError, UnsafeOutputError) as exc:
                progress(
                    "WARNING: The new marker and VPK were committed, but empty legacy "
                    f"directories could not be pruned: {exc}"
                )
    deployment_progress.complete("install", "Override VPK installed")
    return len(unique_resources), missing


def clean_output(
    output_root: Path,
    *,
    allow_shared_directory: bool = False,
    progress: ProgressCallback = print,
) -> int:
    marker = read_marker(output_root, allow_shared_directory=allow_shared_directory)
    if marker is None:
        if output_root.exists():
            try:
                output_root.rmdir()
            except OSError:
                pass
        progress(f"Nothing generated by this tool was found under: {output_root}")
        return 0
    files = marker["files"]
    remove_tracked_files(output_root, files)
    (output_root / MARKER_FILENAME).unlink()
    try:
        output_root.rmdir()
    except OSError:
        pass
    progress(f"Removed {len(files)} generated override file(s) from: {output_root}")
    return len(files)


def validate_language(language: str, *, allow_legacy: bool = False) -> str:
    normalized = language.strip().lower()
    if not re.fullmatch(r"[a-z0-9_]+", normalized):
        raise ValueError("--language must contain only a-z, 0-9, and underscore")
    if normalized == "english":
        normalized = DEFAULT_LANGUAGE
    if normalized == LEGACY_LANGUAGE and allow_legacy:
        return normalized
    if normalized not in RECOGNIZED_LANGUAGES:
        raise ValueError(
            "--language must be a Dota-recognized language name; "
            f"use {DEFAULT_LANGUAGE!r} for the default English-compatible mount"
        )
    return normalized


def clean_legacy_output_after_migration(
    dota: Path,
    *,
    progress: ProgressCallback,
    warning: ProgressCallback,
) -> int:
    legacy_root = dota / "game" / f"dota_{LEGACY_LANGUAGE}"
    if not (legacy_root / MARKER_FILENAME).is_file():
        return 0
    try:
        progress(f"Removing owned legacy loose overrides from: {legacy_root}")
        return clean_output(legacy_root, progress=progress)
    except (OSError, UnsafeOutputError) as exc:
        warning(
            "WARNING: The new VPK was installed, but the legacy loose output could not be cleaned safely: "
            f"{exc}"
        )
        return 0


def clean_other_language_outputs_after_migration(
    dota: Path,
    active_language: str,
    *,
    progress: ProgressCallback,
    warning: ProgressCallback,
) -> int:
    """Remove only this tool's archives left under previously selected mounts."""

    removed = 0
    for language in sorted(RECOGNIZED_LANGUAGES):
        if language == active_language:
            continue
        output_root = dota / "game" / f"dota_{language}"
        if not (output_root / MARKER_FILENAME).is_file():
            continue
        try:
            progress(
                f"Removing owned overrides from the previous {language} compatibility mount..."
            )
            removed += clean_output(
                output_root,
                allow_shared_directory=True,
                progress=progress,
            )
        except (OSError, UnsafeOutputError) as exc:
            warning(
                "WARNING: The new VPK was installed, but an older language-mount output "
                f"could not be cleaned safely: {exc}"
            )
    return removed


def clean_cosmetics(
    dota_path: Optional[str],
    language_name: str = DEFAULT_LANGUAGE,
    *,
    progress: ProgressCallback = print,
) -> CleanResult:
    dota = find_dota_install(dota_path)
    language = validate_language(language_name, allow_legacy=True)
    output_root = dota / "game" / f"dota_{language}"
    with dota_operation_lock(dota):
        removed = clean_output(
            output_root,
            allow_shared_directory=language != LEGACY_LANGUAGE,
            progress=progress,
        )
        if language != LEGACY_LANGUAGE:
            legacy_root = dota / "game" / f"dota_{LEGACY_LANGUAGE}"
            if (legacy_root / MARKER_FILENAME).is_file():
                removed += clean_output(legacy_root, progress=progress)
    return CleanResult(dota=dota, output_root=output_root, removed=removed)


def get_status(dota_path: Optional[str], language_name: str = DEFAULT_LANGUAGE) -> dict:
    dota = find_dota_install(dota_path)
    language = validate_language(language_name, allow_legacy=True)
    output_root = dota / "game" / f"dota_{language}"
    current_version = capture_dota_version(dota)
    marker = read_marker(
        output_root,
        allow_shared_directory=language != LEGACY_LANGUAGE,
    )

    if marker is None:
        legacy_root = dota / "game" / f"dota_{LEGACY_LANGUAGE}"
        legacy_marker = None
        if language == DEFAULT_LANGUAGE and (legacy_root / MARKER_FILENAME).is_file():
            legacy_marker = read_marker(legacy_root)
        if legacy_marker is not None:
            return {
                "status": "legacy",
                "language": language,
                "comparison_basis": None,
                "dota_path": str(dota),
                "output_path": str(legacy_root),
                "current_dota_version": current_version,
                "recorded_dota_version": legacy_marker.get("dota_version"),
                "generated_at_utc": legacy_marker.get("generated_at_utc"),
                "generator_version": legacy_marker.get("generator_version"),
                "enabled_categories": legacy_marker.get("enabled_categories"),
                "deployment_mode": "legacy-loose-files",
                "archive_valid": False,
            }
        result = {
            "status": "not_built",
            "language": language,
            "comparison_basis": None,
            "dota_path": str(dota),
            "output_path": str(output_root),
            "current_dota_version": current_version,
            "recorded_dota_version": None,
            "generated_at_utc": None,
            "generator_version": None,
            "enabled_categories": None,
            "deployment_mode": None,
            "archive_valid": None,
        }
    else:
        recorded_version = marker.get("dota_version")
        comparison, basis = compare_dota_versions(recorded_version, current_version)
        archive_valid: Optional[bool] = None
        if marker.get("deployment_mode") == VPK_DEPLOYMENT_MODE:
            archive_path = path_under(output_root, marker["files"][0])
            archive_valid = (
                archive_path.is_file()
                and sha256_file(archive_path) == marker["archive_sha256"]
            )
        status = (
            "broken"
            if archive_valid is False
            else {"same": "current", "different": "stale", "unknown": "unknown"}[
                comparison
            ]
        )
        result = {
            "status": status,
            "language": language,
            "comparison_basis": basis,
            "dota_path": str(dota),
            "output_path": str(output_root),
            "current_dota_version": current_version,
            "recorded_dota_version": recorded_version,
            "generated_at_utc": marker.get("generated_at_utc"),
            "generator_version": marker.get("generator_version"),
            "enabled_categories": marker.get("enabled_categories"),
            "deployment_mode": marker.get("deployment_mode", "legacy-loose-files"),
            "archive_valid": archive_valid,
        }
    return result


__all__ = [
    "choose_vpk_archive_name",
    "clean_cosmetics",
    "clean_legacy_output_after_migration",
    "clean_other_language_outputs_after_migration",
    "clean_output",
    "deploy_overrides",
    "get_status",
    "marker_enabled_categories",
    "read_marker",
    "remove_tracked_files",
    "sha256_file",
    "validate_category_transition",
    "validate_language",
]
