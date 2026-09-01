"""Discovery and invocation of the bundled Source 2 model skin patcher."""

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


MODEL_PATCHER_VERSION = "0.1.1"


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
        raise FileNotFoundError(f"Internal model skin patcher not found: {candidate}")

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
            project_root / "tools/ModelPatcher/bin/Release/net9.0" / executable_name,
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
    "find_model_patcher",
    "patch_model_material_groups",
    "patch_model_material_groups_batch",
    "validate_model_patcher",
]
