"""Resolve schema-file #base inheritance before the specialized readers run."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

from .keyvalues import KVObject, TokenStream, parse_value, skip_value


MAX_SCHEMA_FILES = 4096
MAX_BASE_DEPTH = 64


def schema_root(path: Path) -> Path:
    """Use the extracted resource root, or the folder of a standalone schema."""
    path = path.resolve()
    for parent in path.parents:
        if parent.name.lower() == "scripts":
            return parent.parent
    return path.parent


def resolve_base_path(path: Path, reference: str, root: Path) -> Path:
    """Resolve a relative include without allowing access outside the schema tree."""
    # VPK extraction canonicalizes resource filenames to lowercase on every OS.
    relative = reference.replace("\\", "/").lower()
    if not relative or relative.startswith("/") or ":" in relative:
        raise ValueError(f"Unsafe #base path {reference!r} in {path}")
    destination = (path.parent / relative).resolve()
    if not destination.is_relative_to(root.resolve()) or destination.suffix.lower() != ".txt":
        raise ValueError(f"Unsafe #base path {reference!r} in {path}")
    return destination


def _base_reference(tokens: TokenStream, path: Path, root: Path) -> Path:
    reference = tokens.next()
    if reference in {"{", "}"}:
        raise ValueError(f"Expected a filename after #base in {path}")
    return resolve_base_path(path, reference, root)


def schema_base_files(path: Path, root: Path) -> list[Path]:
    """Find top-level dependencies without materializing large economy schemas."""
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    if "#base" not in text.lower():
        return []
    tokens = TokenStream(text)
    references: list[Path] = []
    while (key := tokens.next_optional()) is not None:
        if key.lower() == "#base":
            references.append(_base_reference(tokens, path, root))
        else:
            skip_value(tokens)
    return references


def _merge_base(local: KVObject, base: KVObject) -> None:
    # Valve's RecursiveMergeKeyValues keeps local values and recursively fills
    # missing keys, matching the first occurrence while retaining local duplicates.
    first_values = {}
    for key, value in local:
        first_values.setdefault(key, value)
    for key, value in base:
        if key not in first_values:
            local.append((key, value))
            first_values[key] = value
        elif isinstance(first_values[key], KVObject) and isinstance(value, KVObject):
            _merge_base(first_values[key], value)


def _load_root(
    path: Path,
    expected_root: str,
    root: Path,
    stack: tuple[Path, ...],
) -> KVObject:
    if path in stack:
        chain = " -> ".join(str(entry) for entry in (*stack, path))
        raise ValueError(f"Cyclic schema #base reference: {chain}")
    if len(stack) >= MAX_BASE_DEPTH:
        raise ValueError(f"Schema #base nesting exceeds {MAX_BASE_DEPTH} files: {path}")
    if not path.is_file():
        parent = stack[-1] if stack else path
        raise FileNotFoundError(f"Required schema #base file is missing: {path} (referenced by {parent})")
    tokens = TokenStream(path.read_text(encoding="utf-8-sig", errors="replace"))
    local = KVObject()
    found_root = False
    bases: list[Path] = []
    while (key := tokens.next_optional()) is not None:
        if key.lower() == "#base":
            bases.append(_base_reference(tokens, path, root))
            continue
        if key != expected_root:
            raise ValueError(f"Expected {expected_root} root, got {key!r} in {path}")
        if found_root:
            raise ValueError(f"Duplicate {expected_root} root in {path}")
        value = parse_value(tokens)
        if not isinstance(value, KVObject):
            raise ValueError(f"Expected an object for {expected_root} in {path}")
        local = value
        found_root = True
    if not found_root and not bases:
        raise ValueError(f"Expected {expected_root} root in {path}")
    for base in bases:
        _merge_base(local, _load_root(base, expected_root, root, (*stack, path)))
    return local


def _object_tokens(value: KVObject) -> Iterator[str]:
    yield "{"
    for key, child in value:
        yield key
        if isinstance(child, KVObject):
            yield from _object_tokens(child)
        else:
            yield child
    yield "}"


def schema_tokens(path: Path, expected_root: str) -> TokenStream:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    if "#base" not in text.lower():
        # Keep the existing streaming reader for the usual large items schema.
        return TokenStream(text)
    value = _load_root(path.resolve(), expected_root, schema_root(path), ())

    def expanded_tokens() -> Iterator[str]:
        yield expected_root
        yield from _object_tokens(value)

    return TokenStream.from_tokens(expanded_tokens())
