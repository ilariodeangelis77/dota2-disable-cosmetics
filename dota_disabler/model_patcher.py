"""Discovery and invocation of the bundled Source 2 compiled-model helper."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Optional

from .domain import ProgressCallback, WorkProgressCallback
from .errors import GeneratorError
from .paths import runtime_asset_root, source_root
from .vpk import run


MODEL_PATCHER_VERSION = "0.7.0"
MODEL_COMPOSITION_MODES = frozenset(
    {"shared-root", "skeleton-overlay", "skeleton-union"}
)


def find_model_patcher(explicit: Optional[str] = None) -> Path:
    executable_name = (
        "Dota2ModelSkinPatcher.exe" if os.name == "nt" else "Dota2ModelSkinPatcher"
    )
    if explicit:
        candidate = Path(explicit).expanduser().resolve()
        if candidate.is_dir():
            candidate /= executable_name
        if candidate.is_file():
            return candidate
        raise FileNotFoundError(f"Internal model helper not found: {candidate}")

    override = os.environ.get("DOTA_DISABLE_COSMETICS_MODEL_PATCHER")
    candidates: list[Path] = []
    if override:
        candidates.append(Path(override).expanduser())
    project_root = source_root()
    candidates.extend(
        (
            runtime_asset_root() / "tools" / executable_name,
            project_root / "tools" / executable_name,
            project_root / "build/model-patcher" / executable_name,
            project_root / "tools/ModelPatcher/bin/Release/net10.0" / executable_name,
        )
    )
    found = shutil.which(executable_name)
    if found:
        candidates.append(Path(found))
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError(
        "The bundled Dota2ModelSkinPatcher was not found. End users should use the "
        "self-contained release. Source users can build tools/ModelPatcher or set "
        "DOTA_DISABLE_COSMETICS_MODEL_PATCHER."
    )


def validate_model_patcher(patcher: Path) -> None:
    process = run([str(patcher), "--version"], quiet=True)
    version_line = (process.stdout or "").strip()
    expected_prefix = f"Dota2ModelSkinPatcher {MODEL_PATCHER_VERSION} "
    if not version_line.startswith(expected_prefix):
        reported = version_line or "no version information"
        raise GeneratorError(
            "The internal model skin patcher is incompatible with this application. "
            f"Expected {MODEL_PATCHER_VERSION}, but it reported: {reported}. "
            "Rebuild tools/ModelPatcher or use a current self-contained release."
        )


def patch_model_material_groups(
    patcher: Path,
    source: Path,
    destination: Path,
    required_groups: int,
    *,
    progress: ProgressCallback = print,
) -> None:
    if required_groups < 2:
        raise ValueError("A model skin patch requires at least two material groups.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    progress(
        f"Adding {required_groups} default material groups: {destination.name}"
    )
    process = run(
        [
            str(patcher),
            "patch",
            "--input",
            str(source),
            "--output",
            str(destination),
            "--groups",
            str(required_groups),
        ],
        quiet=True,
    )
    try:
        result = json.loads(process.stdout or "")
        output_groups = int(result["output_groups"])
        reported_required = int(result["required_groups"])
        output_bytes = int(result["output_bytes"])
        if (
            reported_required != required_groups
            or output_groups < required_groups
            or output_bytes != destination.stat().st_size
        ):
            raise ValueError("inconsistent model patch verification")
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise GeneratorError(
            "The internal model skin patcher returned an invalid result."
        ) from exc


def compose_models(
    patcher: Path,
    primary_source: Path,
    secondary_source: Path,
    destination: Path,
    *,
    mode: str = "shared-root",
    progress: ProgressCallback = print,
) -> None:
    """Compose two compatible compiled models and verify the helper result."""

    if mode not in MODEL_COMPOSITION_MODES:
        raise ValueError(f"Unsupported model composition mode: {mode}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    progress(f"Composing compatible default models: {destination.name}")
    process = run(
        [
            str(patcher),
            "compose",
            "--primary",
            str(primary_source),
            "--secondary",
            str(secondary_source),
            "--output",
            str(destination),
            "--mode",
            mode,
        ],
        quiet=True,
    )
    try:
        result = json.loads(process.stdout or "")
        primary_meshes = int(result["primary_meshes"])
        secondary_meshes = int(result["secondary_meshes"])
        output_meshes = int(result["output_meshes"])
        primary_bones = int(result["primary_bones"])
        secondary_bones = int(result["secondary_bones"])
        shared_bones = int(result["shared_bones"])
        output_bones = int(result["output_bones"])
        remapped_bone_references = int(result["remapped_bone_references"])
        output_references = int(result["output_references"])
        output_bytes = int(result["output_bytes"])
        valid_shared_bones = (
            shared_bones == 1
            if mode == "shared-root"
            else shared_bones == secondary_bones
            if mode == "skeleton-overlay"
            else 1 <= shared_bones <= min(primary_bones, secondary_bones)
        )
        if (
            result["mode"] != mode
            or primary_meshes < 1
            or secondary_meshes < 1
            or output_meshes != primary_meshes + secondary_meshes
            or primary_bones < 1
            or secondary_bones < 1
            or not valid_shared_bones
            or output_bones != primary_bones + secondary_bones - shared_bones
            or remapped_bone_references < 0
            or (mode == "skeleton-overlay" and remapped_bone_references == 0)
            or output_references < 0
            or not destination.is_file()
            or output_bytes != destination.stat().st_size
        ):
            raise ValueError("inconsistent model composition verification")
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise GeneratorError(
            "The internal model helper returned an invalid composition result."
        ) from exc


def offset_model_attachments(
    patcher: Path,
    source: Path,
    destination: Path,
    attachments: tuple[str, ...],
    offset: tuple[float, float, float],
    *,
    progress: ProgressCallback = print,
) -> None:
    """Translate reviewed attachment points and verify the helper result."""

    if not attachments or any(not name.strip() for name in attachments):
        raise ValueError("At least one valid attachment name is required.")
    if len(offset) != 3 or not any(offset):
        raise ValueError("A non-zero three-axis attachment offset is required.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    progress(f"Adjusting reviewed model attachments: {destination.name}")
    process = run(
        [
            str(patcher),
            "offset-attachments",
            "--input",
            str(source),
            "--output",
            str(destination),
            "--attachments",
            ",".join(attachments),
            "--offset-x",
            format(offset[0], ".17g"),
            "--offset-y",
            format(offset[1], ".17g"),
            "--offset-z",
            format(offset[2], ".17g"),
        ],
        quiet=True,
    )
    try:
        result = json.loads(process.stdout or "")
        reported_offset = (
            float(result["offset_x"]),
            float(result["offset_y"]),
            float(result["offset_z"]),
        )
        if (
            int(result["attachments"]) != len(attachments)
            or reported_offset != offset
            or not destination.is_file()
            or int(result["output_bytes"]) != destination.stat().st_size
        ):
            raise ValueError("inconsistent model attachment-offset verification")
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise GeneratorError(
            "The internal model helper returned an invalid attachment-offset result."
        ) from exc


def patch_model_material_groups_batch(
    patcher: Path,
    requests: Iterable[tuple[Path, Path, int]],
    manifest_directory: Path,
    *,
    progress: ProgressCallback = print,
    progress_update: Optional[WorkProgressCallback] = None,
) -> None:
    jobs = list(requests)
    if not jobs:
        return
    manifest_directory.mkdir(parents=True, exist_ok=True)
    for source, destination, required_groups in jobs:
        if required_groups < 2:
            raise ValueError("A model skin patch requires at least two material groups.")
        if any("\t" in str(path) or "\n" in str(path) or "\r" in str(path) for path in (source, destination)):
            raise ValueError("Model patch paths may not contain tabs or newlines.")
        destination.parent.mkdir(parents=True, exist_ok=True)

    manifest_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=".model-skin-patches-",
            suffix=".tsv",
            dir=manifest_directory,
            delete=False,
        ) as manifest:
            for source, destination, required_groups in jobs:
                manifest.write(f"{source}\t{destination}\t{required_groups}\n")
            manifest_path = Path(manifest.name)
        progress(f"Adding default material groups to {len(jobs)} skin-sensitive model(s)...")
        command = [str(patcher), "patch-batch", "--manifest", str(manifest_path)]
        if progress_update is not None:
            command.append("--progress")
        process = run(
            command,
            quiet=True,
            progress_update=progress_update,
        )
        try:
            lines = (process.stdout or "").splitlines()
            result = json.loads(lines[-1] if lines else "")
            if int(result["patched"]) != len(jobs):
                raise ValueError("inconsistent model batch count")
            if any(not destination.is_file() for _, destination, _ in jobs):
                raise ValueError("a patched model output is missing")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise GeneratorError(
                "The internal model skin patcher returned an invalid batch result."
            ) from exc
    finally:
        if manifest_path and manifest_path.is_file():
            manifest_path.unlink()


__all__ = [
    "MODEL_PATCHER_VERSION",
    "compose_models",
    "find_model_patcher",
    "offset_model_attachments",
    "patch_model_material_groups",
    "patch_model_material_groups_batch",
    "validate_model_patcher",
]
