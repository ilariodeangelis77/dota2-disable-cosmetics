"""Minimal KeyValues patching for the retired bodygroup schema-overlay path."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Optional

from ..constants import ITEMS_SCHEMA_RESOURCE
from ..domain import Plan, ProgressCallback
from ..errors import GeneratorError
from ..keyvalues import _CONDITION_RE, _TOKEN_RE, _decode_quoted
from ..paths import path_under
from ..resources import compiled_model_path


def _iter_kv_token_spans(
    text: str,
    start: int = 0,
    end: Optional[int] = None,
) -> Iterator[tuple[str, int, int]]:
    """Yield parsed KeyValues tokens while retaining their source spans."""

    for match in _TOKEN_RE.finditer(text, start, len(text) if end is None else end):
        raw = match.group(0)
        if not raw or raw.isspace() or raw.startswith("//") or raw.startswith("/*"):
            continue
        if _CONDITION_RE.fullmatch(raw):
            continue
        yield (
            _decode_quoted(raw) if raw.startswith('"') else raw,
            match.start(),
            match.end(),
        )


def _consume_kv_span_value(
    tokens: Iterator[tuple[str, int, int]],
    first: tuple[str, int, int],
) -> int:
    if first[0] != "{":
        return first[2]
    depth = 1
    for token in tokens:
        if token[0] == "{":
            depth += 1
        elif token[0] == "}":
            depth -= 1
            if depth == 0:
                return token[2]
    raise ValueError("Unexpected end of KeyValues object")


def _find_items_game_item_spans(
    text: str,
    item_ids: set[str],
) -> dict[str, tuple[int, int]]:
    """Locate direct item objects without rewriting or reserializing the live schema."""

    tokens = iter(_iter_kv_token_spans(text))
    try:
        root = next(tokens)
        opening = next(tokens)
    except StopIteration as exc:
        raise ValueError("items_game.txt is empty") from exc
    if root[0] != "items_game" or opening[0] != "{":
        raise ValueError("Unexpected items_game.txt root")

    found: dict[str, tuple[int, int]] = {}
    for key in tokens:
        if key[0] == "}":
            break
        try:
            first = next(tokens)
        except StopIteration as exc:
            raise ValueError("Unexpected end of items_game.txt") from exc
        if key[0] != "items":
            _consume_kv_span_value(tokens, first)
            continue
        if first[0] != "{":
            raise ValueError("items_game.txt items entry is not an object")
        for item_key in tokens:
            if item_key[0] == "}":
                break
            try:
                item_first = next(tokens)
            except StopIteration as exc:
                raise ValueError("Unexpected end of items_game.txt items object") from exc
            item_end = _consume_kv_span_value(tokens, item_first)
            if item_key[0] in item_ids:
                if item_first[0] != "{":
                    raise ValueError(f"Item {item_key[0]} is not a KeyValues object")
                found[item_key[0]] = (item_first[1], item_end)
                if len(found) == len(item_ids):
                    return found
        break

    missing = sorted(item_ids.difference(found))
    raise ValueError(
        f"Could not locate bodygroup item(s) in items_game.txt: {', '.join(missing)}"
    )


def _bodygroup_value_spans(text: str, start: int, end: int) -> list[tuple[int, int]]:
    tokens = iter(_iter_kv_token_spans(text, start, end))
    try:
        opening = next(tokens)
    except StopIteration as exc:
        raise ValueError("Empty item object") from exc
    if opening[0] != "{":
        raise ValueError("Item span does not start with a KeyValues object")

    def scan_object() -> list[tuple[int, int]]:
        direct: list[tuple[str, tuple[str, int, int]]] = []
        nested_changes: list[tuple[int, int]] = []
        for key in tokens:
            if key[0] == "}":
                is_bodygroup = any(
                    name == "type" and value[0] == "bodygroup_visibility"
                    for name, value in direct
                )
                if is_bodygroup:
                    values = [
                        (value[1], value[2])
                        for name, value in direct
                        if name == "value" and value[0] == "1"
                    ]
                    if not values:
                        raise ValueError(
                            "bodygroup_visibility rule does not contain value 1"
                        )
                    nested_changes.extend(values)
                return nested_changes
            try:
                value = next(tokens)
            except StopIteration as exc:
                raise ValueError("Unexpected end of item object") from exc
            if value[0] == "{":
                nested_changes.extend(scan_object())
            else:
                direct.append((key[0], value))
        raise ValueError("Unexpected end of item object")

    return scan_object()


def neutralize_item_bodygroups(
    schema_text: str,
    item_ids: Iterable[str],
) -> tuple[str, dict[str, int]]:
    """Reset cosmetic-only bodygroup hides for integrated default hero slots."""

    selected = {str(item_id) for item_id in item_ids}
    if not selected:
        return schema_text, {}
    item_spans = _find_items_game_item_spans(schema_text, selected)
    replacements: list[tuple[int, int, str]] = []
    counts: dict[str, int] = {}
    for item_id in sorted(selected):
        start, end = item_spans[item_id]
        value_spans = _bodygroup_value_spans(schema_text, start, end)
        if not value_spans:
            raise ValueError(f"Item {item_id} has no bodygroup_visibility value to reset")
        counts[item_id] = len(value_spans)
        for value_start, value_end in value_spans:
            raw = schema_text[value_start:value_end]
            replacements.append(
                (value_start, value_end, '"0"' if raw.startswith('"') else "0")
            )

    patched = schema_text
    for value_start, value_end, replacement in sorted(replacements, reverse=True):
        patched = patched[:value_start] + replacement + patched[value_end:]
    return patched, counts


def stage_bodygroup_schema_overlay(
    items_schema: Optional[Path],
    plan: Plan,
    staging: Path,
    *,
    staged_resources: Optional[set[str]] = None,
    progress: ProgressCallback = print,
) -> list[str]:
    reset_item_ids = sorted(
        {
            mapping.item_id
            for mapping in plan.mappings
            if mapping.neutralize_bodygroup
            and mapping.item_id
            and (
                staged_resources is None
                or compiled_model_path(mapping.target) in staged_resources
            )
        }
    )
    if not reset_item_ids:
        return []
    if items_schema is None or not items_schema.is_file():
        raise GeneratorError(
            "An extracted items_game.txt is required to restore integrated bodygroup cosmetics."
        )
    source_bytes = items_schema.read_bytes()
    had_bom = source_bytes.startswith(b"\xef\xbb\xbf")
    try:
        source_text = source_bytes.decode("utf-8-sig")
        patched_text, counts = neutralize_item_bodygroups(source_text, reset_item_ids)
    except (UnicodeDecodeError, ValueError) as exc:
        raise GeneratorError(f"Could not prepare the bodygroup-safe item schema: {exc}") from exc
    destination = path_under(staging, ITEMS_SCHEMA_RESOURCE)
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = patched_text.encode("utf-8")
    destination.write_bytes((b"\xef\xbb\xbf" if had_bom else b"") + encoded)
    progress(
        f"Reset {sum(counts.values())} bodygroup rule(s) for "
        f"{len(reset_item_ids)} integrated-slot cosmetic item(s)."
    )
    return [ITEMS_SCHEMA_RESOURCE]


__all__ = ["neutralize_item_bodygroups", "stage_bodygroup_schema_overlay"]
