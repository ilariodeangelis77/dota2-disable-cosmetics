"""Readable orchestration for the schema-to-plan pipeline."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Optional

from ..domain import ItemRecord, Plan, WorkProgressCallback
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
    work_progress: Optional[WorkProgressCallback] = None,
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

    item_count = len(items)
    finalization_steps = 4
    work_total = item_count + finalization_steps
    for index, item in enumerate(items.values(), start=1):
        if context.is_retired_item(item):
            context.increment("retired_items_skipped")
        else:
            item_state = process_item_models(context, item)
            process_item_effects(context, item_state)
        if work_progress is not None:
            work_progress("plan", index, work_total)

    process_global_effects(context)
    if work_progress is not None:
        work_progress("global_effects", item_count + 1, work_total)
    resolve_pending_model_overrides(context)
    if work_progress is not None:
        work_progress("model_overrides", item_count + 2, work_total)
    mappings = resolve_candidates(context)
    if work_progress is not None:
        work_progress("conflicts", item_count + 3, work_total)
    plan = finalize_plan(context, mappings)
    if work_progress is not None:
        work_progress("finalize", item_count + 4, work_total)
    return plan


__all__ = ["build_plan"]
