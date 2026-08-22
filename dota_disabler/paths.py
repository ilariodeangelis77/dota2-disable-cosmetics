"""Application-root and contained-filesystem path helpers."""

from __future__ import annotations

import sys
from pathlib import Path, PurePosixPath

from .resources import is_safe_resource_path


def source_root() -> Path:
    """Return the source checkout root that formerly contained the entry script."""

    return Path(__file__).resolve().parent.parent


def runtime_asset_root() -> Path:
    """Return PyInstaller's bundle root or the source checkout root."""

    bundled = getattr(sys, "_MEIPASS", None)
    return Path(bundled).resolve() if bundled else source_root()


def application_root() -> Path:
    """Return the directory used for user-visible work files and settings."""

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return source_root()


def path_under(root: Path, relative_posix: str) -> Path:
    """Resolve a safe POSIX resource path below ``root`` without traversal."""

    if not is_safe_resource_path(relative_posix):
        raise ValueError(f"Unsafe relative path: {relative_posix!r}")
    root_resolved = root.resolve()
    candidate = root_resolved.joinpath(*PurePosixPath(relative_posix).parts).resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"Path escapes output root: {relative_posix!r}") from exc
    return candidate


__all__ = ["application_root", "path_under", "runtime_asset_root", "source_root"]
