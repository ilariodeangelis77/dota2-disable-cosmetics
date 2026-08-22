"""Schema extraction from Dota's KeyValues economy and NPC definitions."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .constants import MODEL_KEYS
from .domain import ItemRecord
from .keyvalues import (
    KVObject,
    TokenStream,
    as_str,
    obj_to_simple_dict,
    parse_value,
    skip_value,
)
from .resources import canonical, looks_like_model


def hero_from_item(item: KVObject) -> Optional[str]:
    used_by = item.get_last("used_by_heroes")
    if not isinstance(used_by, KVObject):
        return None
    heroes = [
        key
        for key, value in used_by
        if isinstance(value, str) and value == "1" and key.startswith("npc_dota_hero_")
    ]
    return heroes[0] if len(heroes) == 1 else None


def find_model_fields(
    obj: KVObject,
    *,
    recursive: bool = False,
) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for key, value in obj:
        if key in MODEL_KEYS and isinstance(value, str) and looks_like_model(value):
            found.append((key, canonical(value)))
        if recursive and isinstance(value, KVObject):
            found.extend(find_model_fields(value, recursive=True))
    return found


def visual_modifiers(obj: KVObject) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []

    def walk(node: KVObject) -> None:
        for key, value in node:
            if not isinstance(value, KVObject):
                continue
            modifier_type = as_str(value.get_last("type"))
            if modifier_type and (
                key.startswith("asset_modifier") or value.get_last("asset") is not None
            ):
                record = obj_to_simple_dict(value)
                record["_key"] = key
                found.append(record)
            walk(value)

    walk(obj)
    return found


def load_items_game(
    path: Path,
) -> tuple[dict[str, dict[str, str]], dict[str, ItemRecord], list[dict[str, str]]]:
    tokens = TokenStream(path.read_text(encoding="utf-8-sig", errors="replace"))
    root_name = tokens.next()
    if root_name != "items_game":
        raise ValueError(f"Expected items_game root, got {root_name!r}")
    tokens.expect("{")

    prefabs: dict[str, dict[str, str]] = {}
    items: dict[str, ItemRecord] = {}
    global_visuals: list[dict[str, str]] = []

    while True:
        key = tokens.next()
        if key == "}":
            break
        if key == "prefabs":
            tokens.expect("{")
            while True:
                prefab_name = tokens.next()
                if prefab_name == "}":
                    break
                value = parse_value(tokens)
                if isinstance(value, KVObject):
                    prefabs[prefab_name] = obj_to_simple_dict(value)
        elif key == "items":
            tokens.expect("{")
            while True:
                item_id = tokens.next()
                if item_id == "}":
                    break
                value = parse_value(tokens)
                if not isinstance(value, KVObject):
                    continue
                visuals_object = value.get_last("visuals")
                visuals = (
                    visual_modifiers(visuals_object)
                    if isinstance(visuals_object, KVObject)
                    else []
                )

                def max_skin_index(node: Optional[KVObject]) -> int:
                    if not isinstance(node, KVObject):
                        return 0
                    maximum = 0
                    for child_key, child_value in node:
                        if (
                            child_key == "skin"
                            and isinstance(child_value, str)
                            and child_value.strip() not in {"", "0"}
                        ):
                            try:
                                maximum = max(maximum, int(child_value.strip()))
                            except ValueError:
                                maximum = max(maximum, 1)
                        if isinstance(child_value, KVObject):
                            maximum = max(maximum, max_skin_index(child_value))
                    return maximum

                item_max_skin_index = max_skin_index(value)

                bundle_object = value.get_last("bundle")
                bundle_members = (
                    tuple(
                        member_name
                        for member_name, enabled in bundle_object
                        if isinstance(enabled, str) and enabled == "1"
                    )
                    if isinstance(bundle_object, KVObject)
                    else ()
                )

                items[item_id] = ItemRecord(
                    item_id=item_id,
                    name=as_str(value.get_last("name")) or "",
                    prefab=as_str(value.get_last("prefab")) or "",
                    item_slot=as_str(value.get_last("item_slot")) or "",
                    baseitem=as_str(value.get_last("baseitem")) or "",
                    hero=hero_from_item(value),
                    top_models=find_model_fields(value),
                    nested_models=(
                        find_model_fields(visuals_object, recursive=True)
                        if isinstance(visuals_object, KVObject)
                        else []
                    ),
                    visuals=visuals,
                    has_nondefault_skin=item_max_skin_index > 0,
                    required_material_groups=item_max_skin_index + 1,
                    bundle_members=bundle_members,
                )
        elif key == "asset_modifiers":
            value = parse_value(tokens)
            if isinstance(value, KVObject):
                global_visuals.extend(visual_modifiers(value))
        else:
            skip_value(tokens)

    return prefabs, items, global_visuals


def load_hero_models(path: Path) -> dict[str, str]:
    tokens = TokenStream(path.read_text(encoding="utf-8-sig", errors="replace"))
    root_name = tokens.next()
    if root_name != "DOTAHeroes":
        raise ValueError(f"Expected DOTAHeroes root, got {root_name!r}")
    tokens.expect("{")

    models: dict[str, str] = {}
    while True:
        hero = tokens.next()
        if hero == "}":
            break
        value = parse_value(tokens)
        if (
            not isinstance(value, KVObject)
            or not hero.startswith("npc_dota_hero_")
            or hero == "npc_dota_hero_base"
        ):
            continue
        for variant_index in range(5):
            field = "Model" if variant_index == 0 else f"Model{variant_index}"
            model = as_str(value.get_last(field))
            if not model or not looks_like_model(model):
                continue
            normalized = canonical(model)
            if variant_index == 0:
                models[hero] = normalized
            models[f"{hero}_variant_{variant_index}"] = normalized
    return models


def load_unit_models(path: Path) -> dict[str, str]:
    """Load default summon/ward models, including inherited unit definitions."""

    tokens = TokenStream(path.read_text(encoding="utf-8-sig", errors="replace"))
    root_name = tokens.next()
    if root_name != "DOTAUnits":
        raise ValueError(f"Expected DOTAUnits root, got {root_name!r}")
    tokens.expect("{")

    definitions: dict[str, KVObject] = {}
    while True:
        unit = tokens.next()
        if unit == "}":
            break
        value = parse_value(tokens)
        if isinstance(value, KVObject):
            definitions[unit] = value

    resolved: dict[str, str] = {}
    resolving: set[str] = set()

    def resolve(unit: str) -> Optional[str]:
        if unit in resolved:
            return resolved[unit]
        if unit in resolving:
            return None
        definition = definitions.get(unit)
        if definition is None:
            return None
        resolving.add(unit)
        model = as_str(definition.get_last("Model"))
        if not model or not looks_like_model(model):
            for parent_key in ("include_keys_from", "BaseClass"):
                parent = as_str(definition.get_last(parent_key))
                if parent and parent != unit:
                    model = resolve(parent)
                    if model:
                        break
        resolving.remove(unit)
        if model and looks_like_model(model):
            resolved[unit] = canonical(model)
            return resolved[unit]
        return None

    for unit_name in definitions:
        resolve(unit_name)
    return resolved


def prefab_attr(
    prefabs: dict[str, dict[str, str]],
    prefab_expression: str,
    key: str,
) -> str:
    result = ""
    seen: set[str] = set()

    def visit(name: str) -> None:
        nonlocal result
        if not name or name in seen:
            return
        seen.add(name)
        prefab = prefabs.get(name, {})
        for parent in prefab.get("prefab", "").split():
            visit(parent)
        if key in prefab:
            result = prefab[key]

    for prefab_name in prefab_expression.split():
        visit(prefab_name)
    return result


def item_attr(
    item: ItemRecord,
    prefabs: dict[str, dict[str, str]],
    key: str,
) -> str:
    direct = getattr(item, key, "")
    return direct if isinstance(direct, str) and direct else prefab_attr(prefabs, item.prefab, key)


__all__ = [
    "find_model_fields",
    "hero_from_item",
    "item_attr",
    "load_hero_models",
    "load_items_game",
    "load_unit_models",
    "prefab_attr",
    "visual_modifiers",
]
