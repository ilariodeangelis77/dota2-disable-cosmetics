"""Tk-free settings and presentation helpers for the desktop dashboard."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from . import gui_engine as engine


BG = "#0b0f15"
SURFACE = "#121923"
SURFACE_ALT = "#182230"
SURFACE_HOVER = "#202d3d"
BORDER = "#263446"
BORDER_SOFT = "#1e2a38"
TEXT = "#edf3f8"
TEXT_SOFT = "#b0bdca"
MUTED = "#7f8d9e"
ACCENT = "#d85648"
ACCENT_HOVER = "#e76556"
GREEN = "#45cf92"
AMBER = "#f2b84b"
BLUE = "#5da9e9"
RED = "#ee6a68"

SETTINGS_FORMAT_VERSION = 1
SETTINGS_FILENAME = "ui-settings.json"

LANGUAGE_NAMES = {
    "brazilian": "Brazilian Portuguese",
    "bulgarian": "Bulgarian",
    "czech": "Czech",
    "danish": "Danish",
    "dutch": "Dutch",
    "finnish": "Finnish",
    "french": "French",
    "german": "German",
    "greek": "Greek",
    "hungarian": "Hungarian",
    "italian": "Italian",
    "japanese": "Japanese",
    "koreana": "Korean",
    "latam": "Latin American Spanish",
    "norwegian": "Norwegian",
    "polish": "Polish",
    "portuguese": "Portuguese",
    "romanian": "Romanian",
    "russian": "Russian",
    "schinese": "Simplified Chinese",
    "spanish": "Spanish",
    "swedish": "Swedish",
    "tchinese": "Traditional Chinese",
    "thai": "Thai",
    "turkish": "Turkish",
    "ukrainian": "Ukrainian",
    "vietnamese": "Vietnamese",
}


def language_label(language: str) -> str:
    name = LANGUAGE_NAMES[language]
    return f"{name} — recommended" if language == engine.DEFAULT_LANGUAGE else name


LANGUAGE_LABEL_TO_CODE = {
    language_label(language): language
    for language in sorted(
        engine.RECOGNIZED_LANGUAGES,
        key=lambda value: (value != engine.DEFAULT_LANGUAGE, LANGUAGE_NAMES[value]),
    )
}
LANGUAGE_LABELS = tuple(LANGUAGE_LABEL_TO_CODE)


FEATURES = (
    {
        "category": engine.CATEGORY_STANDARD_WEARABLES,
        "title": "Standard wearables",
        "description": "Ordinary model fields and styles; some Arcana pieces can use these rules.",
        "tag": "SUPPORTED",
    },
    {
        "category": engine.CATEGORY_SPECIAL_MODELS,
        "title": "Hero & model swaps",
        "description": "Schema-defined transformations, including many—but not all—Arcana structures.",
        "tag": "SUPPORTED",
    },
    {
        "category": engine.CATEGORY_PERSONA_MODELS,
        "title": "Persona models",
        "description": "Conservatively restore or hide models attached to Persona slots.",
        "tag": "SUPPORTED",
    },
    {
        "category": engine.CATEGORY_ADDITIONAL_WEARABLES,
        "title": "Standalone attachments",
        "description": "Extra attachments on ordinary items; Persona/special extras follow their parent.",
        "tag": "SUPPORTED",
    },
    {
        "category": engine.CATEGORY_PARTICLE_EFFECTS,
        "title": "Particles & effects",
        "description": "Restore mapped defaults and hide additive effects in cosmetic namespaces.",
        "tag": "SUPPORTED",
    },
)

PLANNED_FEATURES = (
    ("Animation & audio", "Default activities, voices, and cosmetic sounds."),
    ("Icons & UI", "Portraits, abilities, and presentation assets."),
    ("Couriers & world", "Auxiliary units and optional map cosmetics."),
)


def settings_file() -> Path:
    return engine.application_root() / ".work" / SETTINGS_FILENAME


def load_ui_settings(path: Optional[Path] = None) -> dict:
    default = {
        "format_version": SETTINGS_FORMAT_VERSION,
        "dota_path": None,
        "enabled_categories": sorted(engine.DEFAULT_CATEGORIES),
        "language": engine.DEFAULT_LANGUAGE,
    }
    source = path or settings_file()
    if not source.is_file():
        return default
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default
    if not isinstance(payload, dict) or payload.get("format_version") != SETTINGS_FORMAT_VERSION:
        return default
    dota_path = payload.get("dota_path")
    categories = payload.get("enabled_categories")
    language_value = payload.get("language", engine.DEFAULT_LANGUAGE)
    if dota_path is not None and not isinstance(dota_path, str):
        dota_path = None
    if not isinstance(categories, list) or not all(
        isinstance(value, str) for value in categories
    ):
        categories = list(engine.DEFAULT_CATEGORIES)
    selected = sorted(set(categories).intersection(engine.SUPPORTED_CATEGORIES))
    try:
        language = (
            engine.validate_language(language_value)
            if isinstance(language_value, str)
            else engine.DEFAULT_LANGUAGE
        )
    except ValueError:
        language = engine.DEFAULT_LANGUAGE
    return {
        "format_version": SETTINGS_FORMAT_VERSION,
        "dota_path": dota_path,
        "enabled_categories": selected,
        "language": language,
    }


def save_ui_settings(
    dota_path: str,
    categories: set[str],
    path: Optional[Path] = None,
    *,
    language: str = engine.DEFAULT_LANGUAGE,
) -> None:
    engine.write_json(
        path or settings_file(),
        {
            "format_version": SETTINGS_FORMAT_VERSION,
            "dota_path": dota_path or None,
            "enabled_categories": sorted(categories),
            "language": engine.validate_language(language),
        },
    )


def resolve_initial_dota(saved_path: Optional[str]) -> Path:
    if saved_path:
        try:
            return engine.find_dota_install(saved_path)
        except (OSError, ValueError):
            pass
    return engine.find_dota_install(None)


def try_get_status(
    dota: Path,
    language: str = engine.DEFAULT_LANGUAGE,
) -> tuple[Optional[dict], Optional[Exception]]:
    try:
        return engine.get_status(str(dota), language), None
    except Exception as exc:
        return None, exc


def status_matches_path(result: dict, dota_path: str) -> bool:
    recorded_path = result.get("dota_path")
    if not isinstance(recorded_path, str) or not dota_path.strip():
        return False
    return os.path.normcase(os.path.abspath(recorded_path)) == os.path.normcase(
        os.path.abspath(dota_path.strip())
    )


def status_presentation(
    result: dict,
    selected_categories: Optional[set[str]] = None,
) -> dict[str, str]:
    state = result.get("status", "unknown")
    presentations = {
        "current": (
            "CURRENT",
            GREEN,
            "Installed Dota build matches the build used for these overrides",
            "Rebuild Overrides",
        ),
        "stale": (
            "UPDATE FOUND",
            AMBER,
            "Installed Dota build changed since the last build",
            "Rebuild for Installed Build",
        ),
        "legacy": (
            "REBUILD REQUIRED",
            RED,
            "Legacy loose overrides are no longer loaded by current Dota",
            "Build VPK Overrides",
        ),
        "broken": (
            "REPAIR REQUIRED",
            RED,
            "The owned VPK is missing or failed its checksum check",
            "Repair Overrides",
        ),
        "not_built": (
            "NOT BUILT",
            BLUE,
            "No owned disabler build marker was found",
            "Build Overrides",
        ),
        "unknown": (
            "CHECK NEEDED",
            AMBER,
            "The previous run has no comparable Dota-build record",
            "Rebuild Overrides",
        ),
    }
    badge, color, detail, action = presentations.get(state, presentations["unknown"])
    recorded_categories = result.get("enabled_categories")
    if (
        state == "current"
        and selected_categories is not None
        and isinstance(recorded_categories, list)
        and all(isinstance(category, str) for category in recorded_categories)
        and set(recorded_categories) != selected_categories
    ):
        badge, color, detail, action = (
            "CHANGES PENDING",
            AMBER,
            "Category selection changed; rebuild to apply it",
            "Apply Selection",
        )
    return {"badge": badge, "color": color, "detail": detail, "action": action}


__all__ = [
    "ACCENT",
    "ACCENT_HOVER",
    "AMBER",
    "BG",
    "BLUE",
    "BORDER",
    "BORDER_SOFT",
    "FEATURES",
    "GREEN",
    "LANGUAGE_LABELS",
    "LANGUAGE_LABEL_TO_CODE",
    "LANGUAGE_NAMES",
    "MUTED",
    "PLANNED_FEATURES",
    "RED",
    "SETTINGS_FILENAME",
    "SETTINGS_FORMAT_VERSION",
    "SURFACE",
    "SURFACE_ALT",
    "SURFACE_HOVER",
    "TEXT",
    "TEXT_SOFT",
    "engine",
    "language_label",
    "load_ui_settings",
    "resolve_initial_dota",
    "save_ui_settings",
    "settings_file",
    "status_matches_path",
    "status_presentation",
    "try_get_status",
]
