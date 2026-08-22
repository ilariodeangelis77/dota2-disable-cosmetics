"""Canonicalization and validation for Source 2 resource paths."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Optional

from .constants import (
    COSMETIC_ADDITIVE_PARTICLE_PREFIXES,
    RESOURCE_MATERIAL,
    RESOURCE_MODEL,
    RESOURCE_PARTICLE,
    RESOURCE_SNAPSHOT,
)


def canonical(path: str) -> str:
    return path.strip().replace("\\", "/").lower()


def is_safe_resource_path(path: str) -> bool:
    normalized = canonical(path)
    parts = PurePosixPath(normalized).parts
    return (
        bool(parts)
        and not normalized.startswith("/")
        and ".." not in parts
        and not any(":" in part for part in parts)
    )


def looks_like_model(path: str) -> bool:
    normalized = canonical(path)
    return is_safe_resource_path(normalized) and (
        normalized.endswith(".vmdl") or normalized.endswith(".vmdl_c")
    )


def compiled_model_path(path: str) -> str:
    normalized = canonical(path)
    if not is_safe_resource_path(normalized):
        raise ValueError(f"Unsafe resource path: {path!r}")
    if normalized.endswith(".vmdl_c"):
        return normalized
    if normalized.endswith(".vmdl"):
        return normalized + "_c"
    raise ValueError(f"Unsupported model path: {path!r}")


def looks_like_material(path: str) -> bool:
    normalized = canonical(path)
    return is_safe_resource_path(normalized) and (
        normalized.endswith(".vmat") or normalized.endswith(".vmat_c")
    )


def compiled_material_path(path: str) -> str:
    normalized = canonical(path)
    if not is_safe_resource_path(normalized):
        raise ValueError(f"Unsafe resource path: {path!r}")
    if normalized.endswith(".vmat_c"):
        return normalized
    if normalized.endswith(".vmat"):
        return normalized + "_c"
    raise ValueError(f"Unsupported material path: {path!r}")


def looks_like_particle(path: str) -> bool:
    normalized = canonical(path)
    return is_safe_resource_path(normalized) and (
        normalized.endswith(".vpcf") or normalized.endswith(".vpcf_c")
    )


def compiled_particle_path(path: str) -> str:
    normalized = canonical(path)
    if not is_safe_resource_path(normalized):
        raise ValueError(f"Unsafe resource path: {path!r}")
    if normalized.endswith(".vpcf_c"):
        return normalized
    if normalized.endswith(".vpcf"):
        return normalized + "_c"
    raise ValueError(f"Unsupported particle path: {path!r}")


def looks_like_particle_snapshot(path: str) -> bool:
    normalized = canonical(path)
    return is_safe_resource_path(normalized) and (
        normalized.endswith(".vsnap") or normalized.endswith(".vsnap_c")
    )


def is_cosmetic_additive_particle(path: str) -> bool:
    normalized = canonical(path)
    return looks_like_particle(normalized) and normalized.startswith(
        COSMETIC_ADDITIVE_PARTICLE_PREFIXES
    )


def compiled_particle_snapshot_path(path: str) -> str:
    normalized = canonical(path)
    if not is_safe_resource_path(normalized):
        raise ValueError(f"Unsafe resource path: {path!r}")
    if normalized.endswith(".vsnap_c"):
        return normalized
    if normalized.endswith(".vsnap"):
        return normalized + "_c"
    raise ValueError(f"Unsupported particle snapshot path: {path!r}")


def compiled_override_path(path: str, resource_type: Optional[str] = None) -> str:
    if resource_type == RESOURCE_MODEL or (resource_type is None and looks_like_model(path)):
        return compiled_model_path(path)
    if resource_type == RESOURCE_MATERIAL or (
        resource_type is None and looks_like_material(path)
    ):
        return compiled_material_path(path)
    if resource_type == RESOURCE_PARTICLE or (
        resource_type is None and looks_like_particle(path)
    ):
        return compiled_particle_path(path)
    if resource_type == RESOURCE_SNAPSHOT or (
        resource_type is None and looks_like_particle_snapshot(path)
    ):
        return compiled_particle_snapshot_path(path)
    raise ValueError(f"Unsupported override resource path: {path!r}")


__all__ = [
    "canonical",
    "compiled_material_path",
    "compiled_model_path",
    "compiled_override_path",
    "compiled_particle_path",
    "compiled_particle_snapshot_path",
    "is_cosmetic_additive_particle",
    "is_safe_resource_path",
    "looks_like_material",
    "looks_like_model",
    "looks_like_particle",
    "looks_like_particle_snapshot",
]
