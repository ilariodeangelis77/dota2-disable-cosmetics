"""Readable orchestration for the schema-to-plan pipeline."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Optional

from ..domain import ItemRecord, Plan
from .conflicts import resolve_candidates, resolve_pending_model_overrides
from .context import PlanningContext
from .effects import process_global_effects, process_item_effects
from .models import process_item_models
from .validation import finalize_plan


def build_plan(
    prefabs: dict[str, dict[str, str]],
    items: dict[str, ItemRecord],
    hero_models: dict[str, str],
    global_visuals: list[dict[str, str]],
    enabled_categories: Optional[Iterable[str]] = None,
    unit_models: Optional[dict[str, str]] = None,
) -> Plan:
    """Build a deterministic replacement plan from parsed Dota schemas."""

    context = PlanningContext.create(
        prefabs,
        items,
        hero_models,
        global_visuals,
        enabled_categories,
        unit_models,
    )

    for item in items.values():
        if context.is_retired_item(item):
            context.increment("retired_items_skipped")
            continue
        item_state = process_item_models(context, item)
        process_item_effects(context, item_state)

    process_global_effects(context)
    resolve_pending_model_overrides(context)
    mappings = resolve_candidates(context)
    return finalize_plan(context, mappings)


__all__ = ["build_plan"]
