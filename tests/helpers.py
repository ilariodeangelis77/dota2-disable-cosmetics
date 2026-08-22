"""Small synthetic fixture builders shared by refactor-focused tests."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import disable_cosmetics as engine


TEST_HERO = "npc_dota_hero_test"


def make_item(
    item_id: int | str,
    *,
    slot: str,
    name: str | None = None,
    hero: str | None = TEST_HERO,
    baseitem: bool = False,
    model: str | None = None,
    has_nondefault_skin: bool = False,
    required_material_groups: int | None = None,
    bundle_members: Iterable[str] = (),
) -> engine.ItemRecord:
    """Build the smallest ItemRecord needed by planner characterization tests."""

    return engine.ItemRecord(
        item_id=str(item_id),
        name=name or f"item_{item_id}",
        prefab="",
        item_slot=slot,
        baseitem="1" if baseitem else "0",
        hero=hero,
        top_models=[("model_player", model)] if model else [],
        nested_models=[],
        visuals=[],
        has_nondefault_skin=has_nondefault_skin,
        required_material_groups=(
            required_material_groups
            if required_material_groups is not None
            else 2 if has_nondefault_skin else 1
        ),
        bundle_members=tuple(bundle_members),
    )


def write_model_dependencies(
    cache: Path,
    model: str,
    material_paths: Iterable[str],
) -> None:
    """Write an opaque compiled-model stand-in containing material dependency paths."""

    compiled = cache / engine.compiled_model_path(model)
    compiled.parent.mkdir(parents=True, exist_ok=True)
    compiled.write_bytes(b"\x00".join(path.encode("ascii") for path in material_paths))
