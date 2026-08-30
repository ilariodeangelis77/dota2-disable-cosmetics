"""Self-contained VPK helper discovery and subprocess operations."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable, Optional

from .domain import ProgressCallback, WorkProgressCallback
from .errors import GeneratorError
from .paths import path_under, runtime_asset_root, source_root
from .resources import canonical, compiled_override_path, is_safe_resource_path


def find_vpk_extractor(explicit: Optional[str]) -> Path:
    executable_name = "Dota2VpkExtractor.exe" if os.name == "nt" else "Dota2VpkExtractor"
    if explicit:
        candidate = Path(explicit).expanduser().resolve()
        if candidate.is_dir():
            candidate /= executable_name
        if candidate.is_file():
            return candidate
        raise FileNotFoundError(f"Internal VPK extractor not found: {candidate}")

    override = os.environ.get("DOTA_DISABLE_COSMETICS_VPK_EXTRACTOR")
    candidates: list[Path] = []
    if override:
        candidates.append(Path(override).expanduser())
    project_root = source_root()
    candidates.extend(
        (
            runtime_asset_root() / "tools" / executable_name,
            project_root / "tools" / executable_name,
            project_root / "tools/VpkExtractor/bin/Release/net8.0" / executable_name,
        )
    )
    found = shutil.which(executable_name)
    if found:
        candidates.append(Path(found))
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError(
        "The bundled Dota2VpkExtractor was not found. End users should use the self-contained "
        "release. Source users can build tools/VpkExtractor or pass --extractor with its path."
    )


def run(
    command: list[str],
    *,
    quiet: bool = False,
    progress: ProgressCallback = print,
    progress_update: Optional[WorkProgressCallback] = None,
) -> subprocess.CompletedProcess[str]:
    if not quiet:
        progress(f"+ {subprocess.list2cmdline(command)}")
    if progress_update is None:
        process = subprocess.run(
            command,
            stdout=subprocess.PIPE if quiet else None,
            stderr=subprocess.PIPE if quiet else None,
            text=True,
            check=False,
        )
    else:
        output_lines: list[str] = []
        with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as error_stream:
            with subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=error_stream,
                text=True,
            ) as child:
                assert child.stdout is not None
                for line in child.stdout:
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        output_lines.append(line)
                        continue
                    update = record.get("progress") if isinstance(record, dict) else None
                    if update is None:
                        output_lines.append(line)
                        continue
                    try:
                        phase = update["phase"]
                        completed = int(update["completed"])
                        total = int(update["total"])
                        if (
                            not isinstance(phase, str)
                            or not phase
                            or completed < 0
                            or total < 1
                            or completed > total
                        ):
                            raise ValueError
                    except (KeyError, TypeError, ValueError) as exc:
                        child.kill()
                        child.wait()
                        raise GeneratorError(
                            "A compiled-resource helper returned an invalid progress update."
                        ) from exc
                    progress_update(phase, completed, total)
                returncode = child.wait()
            error_stream.seek(0)
            stderr = error_stream.read()
        process = subprocess.CompletedProcess(
            command,
            returncode,
            stdout="".join(output_lines),
            stderr=stderr,
        )
    if process.returncode != 0:
        detail = ""
        if quiet:
            detail = f"\nstdout:\n{process.stdout}\nstderr:\n{process.stderr}"
        raise GeneratorError(
            f"Command failed with exit code {process.returncode}: {command}{detail}"
        )
    return process


def validate_vpk_extractor(extractor: Path) -> None:
    run([str(extractor), "--version"], quiet=True)


def extract_vpk(
    extractor: Path,
    pak: Path,
    resource_paths: Iterable[str],
    output: Path,
    *,
    progress: ProgressCallback = print,
    progress_update: Optional[WorkProgressCallback] = None,
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    paths = sorted({canonical(path) for path in resource_paths})
    if not paths:
        return
    for path in paths:
        if not is_safe_resource_path(path):
            raise ValueError(f"Unsafe VPK resource path: {path!r}")
        cached = path_under(output, path)
        if cached.exists() and not cached.is_file():
            raise GeneratorError(f"Expected a cached file but found another file type: {cached}")
        if cached.is_file():
            # Never allow a removed/renamed VPK entry to survive from an older build.
            cached.unlink()

    manifest_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=".vpk-paths-",
            suffix=".txt",
            dir=output,
            delete=False,
        ) as manifest:
            manifest.write("\n".join(paths) + "\n")
            manifest_path = Path(manifest.name)
        progress(f"Extracting {len(paths)} resource(s) from Dota's VPK...")
        command = [
            str(extractor),
            "--vpk",
            str(pak),
            "--output",
            str(output),
            "--paths-file",
            str(manifest_path),
        ]
        if progress_update is not None:
            command.append("--progress")
        process = run(
            command,
            quiet=True,
            progress_update=progress_update,
        )
        try:
            result = json.loads(process.stdout or "")
            requested = int(result["requested"])
            extracted = int(result["extracted"])
            missing = result["missing"]
            if (
                requested != len(paths)
                or not isinstance(missing, list)
                or not all(isinstance(path, str) for path in missing)
                or extracted + len(missing) != requested
            ):
                raise ValueError("inconsistent extraction counts")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise GeneratorError("The internal VPK extractor returned an invalid result.") from exc
    finally:
        if manifest_path and manifest_path.is_file():
            manifest_path.unlink()


def pack_vpk(
    extractor: Path,
    input_root: Path,
    output_path: Path,
    *,
    progress: ProgressCallback = print,
    progress_update: Optional[WorkProgressCallback] = None,
) -> int:
    resources = sorted(
        canonical(path.relative_to(input_root).as_posix())
        for path in input_root.rglob("*")
        if path.is_file()
    )
    if not resources:
        raise GeneratorError("No compiled override resources were staged for the VPK.")
    for resource in resources:
        try:
            is_override = compiled_override_path(resource) == resource
        except ValueError:
            is_override = False
        is_language_support = bool(re.search(r"_[a-z]+\.(?:txt|vtt)$", resource))
        if not (is_override or is_language_support):
            raise GeneratorError(f"Refusing to package an unsupported resource: {resource}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.is_file():
        output_path.unlink()
    progress(f"Packing and CRC-validating {len(resources)} resource(s)...")
    command = [
        str(extractor),
        "pack",
        "--input",
        str(input_root),
        "--output",
        str(output_path),
    ]
    if progress_update is not None:
        command.append("--progress")
    process = run(
        command,
        quiet=True,
        progress_update=progress_update,
    )
    try:
        result = json.loads(process.stdout or "")
        packed = int(result["packed"])
        verified = int(result["verified"])
        output_bytes = int(result["output_bytes"])
        if (
            packed != len(resources)
            or verified != packed
            or output_bytes != output_path.stat().st_size
        ):
            raise ValueError("inconsistent pack verification counts")
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise GeneratorError("The internal VPK packer returned an invalid result.") from exc
    return packed


def list_vpk_resources(extractor: Path, pak: Path, suffixes: Iterable[str]) -> list[str]:
    normalized_suffixes = sorted(
        {suffix.strip().lower() for suffix in suffixes if suffix.strip()}
    )
    if not normalized_suffixes or any(
        "/" in suffix or "\\" in suffix for suffix in normalized_suffixes
    ):
        raise ValueError(
            "VPK listing suffixes must be non-empty filenames without path separators."
        )
    process = run(
        [
            str(extractor),
            "list",
            "--vpk",
            str(pak),
            "--suffixes",
            ";".join(normalized_suffixes),
        ],
        quiet=True,
    )
    try:
        result = json.loads(process.stdout or "")
        resources = result["resources"]
        count = int(result["count"])
        if (
            not isinstance(resources, list)
            or not all(
                isinstance(path, str) and is_safe_resource_path(path) for path in resources
            )
            or count != len(resources)
        ):
            raise ValueError("invalid resource list")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise GeneratorError("The internal VPK lister returned an invalid result.") from exc
    return [canonical(path) for path in resources]


def stage_english_language_support(
    extractor: Path,
    pak: Path,
    work: Path,
    staging: Path,
    language: str,
    *,
    progress: ProgressCallback = print,
    progress_update: Optional[WorkProgressCallback] = None,
) -> list[str]:
    english_resources = list_vpk_resources(extractor, pak, ("_english.txt", "_english.vtt"))
    if not english_resources:
        progress(
            "NOTE: No English localization resources were found for the language compatibility layer."
        )
        return []
    source_root_path = work / "language-support" / "english"
    extract_vpk(
        extractor,
        pak,
        english_resources,
        source_root_path,
        progress=progress,
        progress_update=progress_update,
    )
    staged: list[str] = []
    for index, source_relative in enumerate(english_resources, start=1):
        if source_relative.endswith("_english.txt"):
            target_relative = source_relative[: -len("_english.txt")] + f"_{language}.txt"
        elif source_relative.endswith("_english.vtt"):
            target_relative = source_relative[: -len("_english.vtt")] + f"_{language}.vtt"
        else:
            continue
        source = path_under(source_root_path, source_relative)
        if not source.is_file():
            raise GeneratorError(
                f"English language resource was not extracted: {source_relative}"
            )
        destination = path_under(staging, target_relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        staged.append(target_relative)
        if progress_update is not None:
            progress_update("stage", index, len(english_resources))
    progress(
        f"Added {len(staged)} English-language compatibility resource(s) for -language {language}."
    )
    return sorted(staged)


__all__ = [
    "extract_vpk",
    "find_vpk_extractor",
    "list_vpk_resources",
    "pack_vpk",
    "run",
    "stage_english_language_support",
    "validate_vpk_extractor",
]
