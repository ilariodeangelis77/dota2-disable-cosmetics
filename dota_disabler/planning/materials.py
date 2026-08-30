"""Resource-aware neutralization of alternate model material groups."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Optional

from ..constants import INVISIBLE_MODEL, RESOURCE_MATERIAL, RESOURCE_MODEL, SUPPORTED_CATEGORIES
from ..domain import Mapping, Plan, WorkProgressCallback
from ..paths import path_under
from ..resources import canonical, compiled_model_path, looks_like_material


MATERIAL_REFERENCE_PATTERN = re.compile(
    rb"materials[/\\][a-zA-Z0-9_./\\-]+\.vmat(?:_c)?",
    re.IGNORECASE,
)


def apply_model_skin_material_fallbacks(
    plan: Plan,
    cache: Path,
    *,
    work_progress: Optional[WorkProgressCallback] = None,
) -> Plan:
    """Redirect confidently matched alternate model materials to their base variant.

    A schema ``skin`` value still applies after a cosmetic model is replaced. Source 2
    models keep the material-group resources as plain dependency paths, so matching
    paths with the same filename can be neutralized without editing Dota's item schema.
    """

    skin_mappings = [
        mapping
        for mapping in plan.mappings
        if mapping.resource_type == RESOURCE_MODEL and mapping.neutralize_model_skin
    ]
    if not skin_mappings:
        stats = dict(plan.stats)
        stats.update(
            {
                "material_overrides": 0,
                "unique_source_materials": 0,
                "alternate_skin_material_models": 0,
                "alternate_skin_material_variants": 0,
                "alternate_skin_material_unresolved": 0,
                "alternate_skin_material_passthrough_models": 0,
                "alternate_skin_group_patch_targets": 0,
            }
        )
        return Plan(mappings=list(plan.mappings), unresolved=list(plan.unresolved), stats=stats)

    mappings_by_source: dict[str, list[Mapping]] = {}
    for mapping in skin_mappings:
        mappings_by_source.setdefault(mapping.source, []).append(mapping)

    material_candidates: dict[str, Mapping] = {}
    unresolved = list(plan.unresolved)
    conflicts = 0
    resolved_models = 0
    passthrough_models = 0
    unresolved_material_targets: set[str] = set()
    preserved_model_targets: set[str] = set()
    group_patch_targets: set[str] = set()
    source_count = len(mappings_by_source)
    for source_index, (source_model, owners) in enumerate(
        mappings_by_source.items(),
        start=1,
    ):
        compiled_source = path_under(cache, compiled_model_path(source_model))
        if not compiled_source.is_file():
            # Missing model sources are reported later by the normal deployment path.
            if work_progress is not None:
                work_progress("analyze", source_index, source_count)
            continue
        references: list[str] = []
        seen: set[str] = set()
        for match in MATERIAL_REFERENCE_PATTERN.findall(compiled_source.read_bytes()):
            reference = canonical(match.decode("ascii", errors="ignore"))
            if reference.endswith(".vmat_c"):
                reference = reference[:-2]
            if looks_like_material(reference) and reference not in seen:
                seen.add(reference)
                references.append(reference)

        by_filename: dict[str, list[str]] = {}
        for reference in references:
            by_filename.setdefault(PurePosixPath(reference).name, []).append(reference)

        pairs: list[tuple[str, str]] = []
        paired_alternates: set[str] = set()
        for variants in by_filename.values():
            if len(variants) < 2:
                continue
            base = min(
                variants,
                key=lambda path: (
                    0 if "/models/heroes/" in f"/{path}" else 1,
                    len(path),
                    variants.index(path),
                ),
            )
            for alternate in variants:
                if alternate != base:
                    pairs.append((base, alternate))
                    paired_alternates.add(alternate)

        base_materials = [
            reference
            for reference in references
            if "/models/heroes/" in f"/{reference}"
        ]
        alternate_materials = [
            reference
            for reference in references
            if reference not in base_materials and reference not in paired_alternates
        ]
        ignored_tokens = {
            "color",
            "diretide",
            "fall20",
            "hero",
            "heroes",
            "item",
            "items",
            "material",
            "materials",
            "model",
            "models",
            "vmat",
        }

        def material_tokens(path: str) -> set[str]:
            return {
                token
                for token in re.findall(r"[a-z0-9]+", PurePosixPath(path).stem.casefold())
                if token not in ignored_tokens
            }

        for alternate in alternate_materials:
            alternate_tokens = material_tokens(alternate)
            ranked: list[tuple[int, int, str]] = []
            for base in base_materials:
                shared = alternate_tokens.intersection(material_tokens(base))
                ranked.append((len(shared), sum(len(token) for token in shared), base))
            ranked.sort(reverse=True)
            if not ranked or ranked[0][0] == 0:
                continue
            if len(ranked) > 1 and ranked[0][:2] == ranked[1][:2]:
                continue
            pairs.append((ranked[0][2], alternate))
            paired_alternates.add(alternate)

        if not pairs:
            passthrough_models += 1
            for owner in owners:
                model_is_noop = owner.source == owner.target
                if owner.source == canonical(INVISIBLE_MODEL):
                    # Hidden Persona/pet/attachment models contain no render material,
                    # so an equipped skin index cannot produce visible error geometry.
                    continue
                if not model_is_noop and owner.required_material_groups > 1:
                    group_patch_targets.add(owner.target)
                    continue
                unresolved_material_targets.add(owner.target)
                if model_is_noop:
                    preserved_model_targets.add(owner.target)
                unresolved.append(
                    {
                        "type": (
                            "alternate_skin_model_preserved"
                            if model_is_noop
                            else "alternate_skin_material_unresolved"
                        ),
                        "item_id": owner.item_id,
                        "hero": owner.hero,
                        "slot": owner.slot,
                        "target": owner.target,
                        "reason": (
                            "the material-only cosmetic could not be neutralized safely"
                            if model_is_noop
                            else "no compatible base material could be inferred; the valid "
                            "default-model replacement was retained"
                        ),
                    }
                )
            if work_progress is not None:
                work_progress("analyze", source_index, source_count)
            continue

        resolved_models += 1
        owner = owners[0]
        for source_material, target_material in pairs:
            candidate = Mapping(
                source=source_material,
                target=target_material,
                reason="alternate model material replaced with base material",
                category=owner.category,
                resource_type=RESOURCE_MATERIAL,
                item_id=owner.item_id,
                hero=owner.hero,
                slot=owner.slot,
            )
            existing = material_candidates.get(candidate.target)
            if existing is None:
                material_candidates[candidate.target] = candidate
            elif existing.source != candidate.source:
                conflicts += 1
                unresolved.append(
                    {
                        "type": "material_mapping_conflict",
                        "target": candidate.target,
                        "kept_source": existing.source,
                        "candidate_sources": sorted({existing.source, candidate.source}),
                        "reason": "multiple default models map the same alternate material differently",
                    }
                )
        if work_progress is not None:
            work_progress("analyze", source_index, source_count)

    added = list(material_candidates.values())
    retained = [
        mapping
        for mapping in plan.mappings
        if not (
            mapping.resource_type == RESOURCE_MODEL
            and mapping.source == mapping.target
        )
    ]
    mappings = sorted(
        [*retained, *added],
        key=lambda mapping: (mapping.resource_type, mapping.target, mapping.source),
    )
    stats = dict(plan.stats)
    stats["resource_overrides"] = len(mappings)
    stats["material_overrides"] = len(added)
    stats["model_overrides"] = sum(
        mapping.resource_type == RESOURCE_MODEL for mapping in mappings
    )
    stats["unique_source_models"] = len(
        {mapping.source for mapping in mappings if mapping.resource_type == RESOURCE_MODEL}
    )
    stats["unique_source_materials"] = len({mapping.source for mapping in added})
    stats["alternate_skin_material_models"] = resolved_models
    stats["alternate_skin_material_variants"] = len(added)
    stats["alternate_skin_material_unresolved"] = len(unresolved_material_targets)
    stats["alternate_skin_material_passthrough_models"] = passthrough_models
    # Every visible replacement carrying a non-default equipped skin index needs
    # enough material groups in the copied base model.  Redirecting alternate
    # material resources is complementary: it does not make a missing group in
    # the model's DATA block safe to select.
    stats["alternate_skin_group_patch_targets"] = sum(
        mapping.resource_type == RESOURCE_MODEL
        and mapping.required_material_groups > 1
        for mapping in mappings
    )
    stats["alternate_skin_models_skipped"] = len(preserved_model_targets)
    stats["mapping_conflicts"] = stats.get("mapping_conflicts", 0) + conflicts
    stats["unresolved"] = len(unresolved)
    for category in SUPPORTED_CATEGORIES:
        stats[f"category_{category}"] = sum(
            mapping.category == category for mapping in mappings
        )
    return Plan(mappings=mappings, unresolved=unresolved, stats=stats)


__all__ = ["MATERIAL_REFERENCE_PATTERN", "apply_model_skin_material_fallbacks"]
