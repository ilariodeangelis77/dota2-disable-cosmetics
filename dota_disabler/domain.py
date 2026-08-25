"""Domain records shared by planning, deployment, CLI, and UI adapters."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .constants import (
    CATEGORY_STANDARD_WEARABLES,
    DEFAULT_CATEGORIES,
    DEFAULT_LANGUAGE,
    RESOURCE_MODEL,
)


ProgressCallback = Callable[[str], None]
ProgressUpdateCallback = Callable[[int, str], None]


@dataclass
class ItemRecord:
    item_id: str
    name: str
    prefab: str
    item_slot: str
    baseitem: str
    hero: Optional[str]
    top_models: list[tuple[str, str]]
    nested_models: list[tuple[str, str]]
    visuals: list[dict[str, str]]
    has_nondefault_skin: bool = False
    required_material_groups: int = 1
    bundle_members: tuple[str, ...] = ()


@dataclass(frozen=True)
class Mapping:
    """Copy a default/neutral compiled resource over a cosmetic resource path."""

    source: str
    target: str
    reason: str
    category: str = CATEGORY_STANDARD_WEARABLES
    resource_type: str = RESOURCE_MODEL
    item_id: Optional[str] = None
    hero: Optional[str] = None
    slot: Optional[str] = None
    neutralize_model_skin: bool = False
    required_material_groups: int = 1
    neutralize_bodygroup: bool = False


@dataclass
class Plan:
    mappings: list[Mapping]
    unresolved: list[dict]
    stats: dict[str, int]


@dataclass(frozen=True)
class BuildOptions:
    dota: Optional[str] = None
    extractor: Optional[str] = None
    language: str = DEFAULT_LANGUAGE
    work: Optional[str] = None
    clean_first: bool = True
    allow_missing: bool = False
    enabled_categories: frozenset[str] = DEFAULT_CATEGORIES


@dataclass(frozen=True)
class BuildResult:
    dota: Path
    output_root: Path
    report: Path
    history: Path
    copied: int
    missing: tuple[dict, ...]
    stats: dict[str, int]
    dota_version: dict
    enabled_categories: tuple[str, ...]
    history_recorded: bool


@dataclass(frozen=True)
class CleanResult:
    dota: Path
    output_root: Path
    removed: int


__all__ = [
    "BuildOptions",
    "BuildResult",
    "CleanResult",
    "ItemRecord",
    "Mapping",
    "Plan",
    "ProgressCallback",
    "ProgressUpdateCallback",
]
