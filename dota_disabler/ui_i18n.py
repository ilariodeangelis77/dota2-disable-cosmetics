"""Tk-free localization helpers for the desktop dashboard.

The Dota compatibility mount is deliberately unrelated to the locale handled
here.  This module only controls text displayed by the patcher's own UI.
"""

from __future__ import annotations

import gettext
import locale
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional


UI_LOCALE_AUTO = "auto"
UI_LOCALE_ENGLISH = "en"
UI_GETTEXT_DOMAIN = "ui"

# Autonyms keep the language picker understandable even before its surrounding
# controls have been translated. More names are added as reviewed catalogs ship.
UI_LOCALE_NAMES = {
    UI_LOCALE_AUTO: "System default",
    UI_LOCALE_ENGLISH: "English",
    "ru": "Русский",
    "zh-Hans": "简体中文",
    "zh-Hant": "繁體中文",
}

_SAFE_LOCALE = re.compile(r"^[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})*$")
_LOCALE_ALIASES = {
    "c": UI_LOCALE_ENGLISH,
    "posix": UI_LOCALE_ENGLISH,
    "zh": "zh-Hans",
    "zh-cn": "zh-Hans",
    "zh-sg": "zh-Hans",
    "zh-chs": "zh-Hans",
    "zh-tw": "zh-Hant",
    "zh-hk": "zh-Hant",
    "zh-mo": "zh-Hant",
    "zh-cht": "zh-Hant",
}


def N_(message: str) -> str:
    """Mark deferred UI text for catalog extraction without translating it."""
    return message


def locales_directory() -> Path:
    """Return the source or PyInstaller-extracted gettext catalog directory."""
    return Path(__file__).resolve().parent / "locales"


def normalize_ui_locale(value: str) -> str:
    """Normalize an OS or settings locale to the BCP-47-style form we store."""
    if not isinstance(value, str):
        raise ValueError("UI locale must be a string")
    candidate = value.strip()
    if candidate.lower() == UI_LOCALE_AUTO:
        return UI_LOCALE_AUTO
    candidate = candidate.split(".", 1)[0].split("@", 1)[0]
    if candidate.casefold() in {"c", "posix"}:
        return UI_LOCALE_ENGLISH
    if not _SAFE_LOCALE.fullmatch(candidate):
        raise ValueError(f"Invalid UI locale: {value!r}")

    parts = candidate.replace("_", "-").split("-")
    language = parts[0].lower()
    normalized_parts = [language]
    for part in parts[1:]:
        if len(part) == 4 and part.isalpha():
            normalized_parts.append(part.title())
        elif len(part) == 2 and part.isalpha():
            normalized_parts.append(part.upper())
        else:
            normalized_parts.append(part)
    normalized = "-".join(normalized_parts)
    return _LOCALE_ALIASES.get(normalized.casefold(), normalized)


def system_ui_locale() -> str:
    """Return the normalized locale preferred by the current operating system."""
    candidates: list[str] = []
    for variable in ("LC_ALL", "LC_MESSAGES", "LANGUAGE", "LANG"):
        value = os.environ.get(variable)
        if value:
            candidates.extend(part for part in value.split(":") if part)
    try:
        configured, _encoding = locale.getlocale()
    except (TypeError, ValueError):
        configured = None
    if configured:
        candidates.append(configured)
    for candidate in candidates:
        try:
            normalized = normalize_ui_locale(candidate)
        except ValueError:
            continue
        if normalized != UI_LOCALE_AUTO:
            return normalized
    return UI_LOCALE_ENGLISH


def _gettext_locale_name(ui_locale: str) -> str:
    return ui_locale.replace("-", "_")


def available_ui_locales(catalog_root: Optional[Path] = None) -> tuple[str, ...]:
    """Return locales with a compiled catalog, always including English."""
    root = catalog_root or locales_directory()
    found = {UI_LOCALE_ENGLISH}
    if root.is_dir():
        for child in root.iterdir():
            catalog = child / "LC_MESSAGES" / f"{UI_GETTEXT_DOMAIN}.mo"
            if not child.is_dir() or not catalog.is_file():
                continue
            try:
                found.add(normalize_ui_locale(child.name))
            except ValueError:
                continue
    return tuple(
        sorted(
            found,
            key=lambda value: (
                value != UI_LOCALE_ENGLISH,
                UI_LOCALE_NAMES.get(value, value).casefold(),
            ),
        )
    )


def resolve_ui_locale(
    preference: str,
    *,
    available: Optional[Iterable[str]] = None,
    detected_locale: Optional[str] = None,
) -> str:
    """Resolve a saved preference to an available catalog with English fallback."""
    normalized_available = {
        normalize_ui_locale(value)
        for value in (available if available is not None else available_ui_locales())
        if value != UI_LOCALE_AUTO
    }
    normalized_available.add(UI_LOCALE_ENGLISH)
    normalized_preference = normalize_ui_locale(preference)
    requested = (
        normalize_ui_locale(detected_locale or system_ui_locale())
        if normalized_preference == UI_LOCALE_AUTO
        else normalized_preference
    )
    if requested in normalized_available:
        return requested
    language = requested.split("-", 1)[0]
    if language in normalized_available:
        return language
    return UI_LOCALE_ENGLISH


@dataclass(frozen=True)
class UiTranslator:
    """Small explicit wrapper around gettext without process-global state."""

    locale: str
    translation: gettext.NullTranslations

    def gettext(self, message: str) -> str:
        return self.translation.gettext(message)

    def pgettext(self, context: str, message: str) -> str:
        return self.translation.pgettext(context, message)

    def ngettext(self, singular: str, plural: str, count: int) -> str:
        return self.translation.ngettext(singular, plural, count)


def load_ui_translator(
    preference: str = UI_LOCALE_AUTO,
    *,
    catalog_root: Optional[Path] = None,
    detected_locale: Optional[str] = None,
) -> UiTranslator:
    """Load the selected catalog, falling back safely to English message IDs."""
    root = catalog_root or locales_directory()
    resolved = resolve_ui_locale(
        preference,
        available=available_ui_locales(root),
        detected_locale=detected_locale,
    )
    if resolved == UI_LOCALE_ENGLISH:
        translation: gettext.NullTranslations = gettext.NullTranslations()
    else:
        translation = gettext.translation(
            UI_GETTEXT_DOMAIN,
            localedir=str(root),
            languages=[_gettext_locale_name(resolved)],
            fallback=True,
        )
    return UiTranslator(resolved, translation)


__all__ = [
    "N_",
    "UI_GETTEXT_DOMAIN",
    "UI_LOCALE_AUTO",
    "UI_LOCALE_ENGLISH",
    "UI_LOCALE_NAMES",
    "UiTranslator",
    "available_ui_locales",
    "load_ui_translator",
    "locales_directory",
    "normalize_ui_locale",
    "resolve_ui_locale",
    "system_ui_locale",
]
