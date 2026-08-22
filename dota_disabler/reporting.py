"""Atomic JSON persistence and human-auditable mapping reports."""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Iterable, Optional

from .domain import Plan


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def write_plan(
    plan: Plan,
    path: Path,
    *,
    enabled_categories: Optional[Iterable[str]] = None,
) -> None:
    write_json(
        path,
        {
            "stats": plan.stats,
            "enabled_categories": sorted(
                set(enabled_categories)
                if enabled_categories is not None
                else {mapping.category for mapping in plan.mappings}
            ),
            "mappings": [asdict(mapping) for mapping in plan.mappings],
            "unresolved": plan.unresolved,
        },
    )


__all__ = ["write_json", "write_plan"]
