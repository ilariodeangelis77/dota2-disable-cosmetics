"""Duplicate-key-preserving parser for Valve KeyValues 1 data."""

from __future__ import annotations

import re
from typing import Iterator, Optional, Union


class KVObject(list):
    """Duplicate-key-preserving Valve KeyValues object: ``[(key, value), ...]``."""

    def get_last(self, key: str, default=None):
        for candidate, value in reversed(self):
            if candidate == key:
                return value
        return default


KVValue = Union[str, KVObject]


_TOKEN_RE = re.compile(
    r'\s+|//[^\r\n]*|/\*.*?\*/|\{|\}|"(?:\\.|[^"\\])*"|[^\s{}"]+',
    re.DOTALL,
)
_CONDITION_RE = re.compile(r"^\[(?:!?\$?[A-Za-z0-9_]+)\]$")


def _decode_quoted(token: str) -> str:
    body = token[1:-1]
    out: list[str] = []
    index = 0
    while index < len(body):
        character = body[index]
        if character == "\\" and index + 1 < len(body):
            escaped = body[index + 1]
            translations = {"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\"}
            if escaped in translations:
                out.append(translations[escaped])
            else:
                # Preserve unknown escapes. KeyValues sometimes contains Windows paths.
                out.extend(("\\", escaped))
            index += 2
        else:
            out.append(character)
            index += 1
    return "".join(out)


class TokenStream:
    def __init__(self, text: str):
        self._iterator = self._tokens(text)

    @staticmethod
    def _tokens(text: str) -> Iterator[str]:
        for match in _TOKEN_RE.finditer(text):
            token = match.group(0)
            if not token or token.isspace() or token.startswith("//") or token.startswith("/*"):
                continue
            if _CONDITION_RE.fullmatch(token):
                # Shipped client schemas are already platform-resolved for the
                # fields consumed by this application.
                continue
            yield _decode_quoted(token) if token.startswith('"') else token

    def next(self) -> str:
        try:
            return next(self._iterator)
        except StopIteration as exc:
            raise ValueError("Unexpected end of KeyValues input") from exc

    def expect(self, expected: str) -> None:
        actual = self.next()
        if actual != expected:
            raise ValueError(f"Expected {expected!r}, got {actual!r}")


def parse_value(tokens: TokenStream) -> KVValue:
    token = tokens.next()
    if token != "{":
        return token
    result = KVObject()
    while True:
        key = tokens.next()
        if key == "}":
            return result
        result.append((key, parse_value(tokens)))


def skip_value(tokens: TokenStream) -> None:
    token = tokens.next()
    if token != "{":
        return
    depth = 1
    while depth:
        token = tokens.next()
        if token == "{":
            depth += 1
        elif token == "}":
            depth -= 1


def as_str(value: KVValue | None) -> Optional[str]:
    return value if isinstance(value, str) else None


def obj_to_simple_dict(obj: KVObject) -> dict[str, str]:
    return {key: value for key, value in obj if isinstance(value, str)}


__all__ = [
    "KVObject",
    "KVValue",
    "TokenStream",
    "as_str",
    "obj_to_simple_dict",
    "parse_value",
    "skip_value",
]
