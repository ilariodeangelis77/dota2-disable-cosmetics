"""Resource-aware fallbacks for schema particle defaults."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from ..constants import (
    INTENTIONALLY_NEUTRAL_PARTICLE_DEFAULTS,
    INTENTIONALLY_NEUTRAL_PARTICLE_PREFIXES,
    NEUTRAL_PARTICLE,
    RESOURCE_PARTICLE,
)
from ..domain import Mapping, Plan
from ..paths import path_under
from ..resources import canonical, compiled_override_path, compiled_particle_path


def apply_missing_particle_fallbacks(plan: Plan, cache: Path) -> Plan:
    """Use Dota's null particle when a schema-referenced default no longer exists."""

    neutral_compiled = compiled_particle_path(NEUTRAL_PARTICLE)
    neutral_source = path_under(cache, neutral_compiled)
    if not neutral_source.is_file():
        return plan

    adjusted: list[Mapping] = []
    fallback_count = 0
    intentional_count = 0
    unknown_count = 0
    for mapping in plan.mappings:
        source_relative = compiled_override_path(mapping.source, mapping.resource_type)
        if (
            mapping.resource_type == RESOURCE_PARTICLE
            and not path_under(cache, source_relative).is_file()
        ):
            intentional = (
                mapping.source in INTENTIONALLY_NEUTRAL_PARTICLE_DEFAULTS
                or mapping.source.startswith(INTENTIONALLY_NEUTRAL_PARTICLE_PREFIXES)
            )
            adjusted.append(
                replace(
                    mapping,
                    source=canonical(NEUTRAL_PARTICLE),
                    reason=(
                        "virtual schema particle neutralized by design"
                        if intentional
                        else "missing default particle hidden with neutral fallback"
                    ),
                )
            )
            fallback_count += 1
            if intentional:
                intentional_count += 1
            else:
                unknown_count += 1
        else:
            adjusted.append(mapping)

    if not fallback_count:
        return plan
    stats = dict(plan.stats)
    stats["particle_missing_defaults_hidden"] = fallback_count
    stats["particle_virtual_defaults_neutralized"] = intentional_count
    stats["particle_unknown_defaults_neutralized"] = unknown_count
    return Plan(mappings=adjusted, unresolved=list(plan.unresolved), stats=stats)


__all__ = ["apply_missing_particle_fallbacks"]
