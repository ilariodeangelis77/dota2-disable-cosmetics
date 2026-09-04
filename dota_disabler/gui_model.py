"""Tk-free settings and presentation helpers for the desktop dashboard."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Optional

from . import gui_engine as engine
from .ui_i18n import N_, UI_LOCALE_AUTO, normalize_ui_locale


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

SETTINGS_FORMAT_VERSION = 2
SETTINGS_FILENAME = "ui-settings.json"

LANGUAGE_NAMES = {
    "brazilian": N_("Brazilian Portuguese"),
    "bulgarian": N_("Bulgarian"),
    "czech": N_("Czech"),
    "danish": N_("Danish"),
    "dutch": N_("Dutch"),
    "finnish": N_("Finnish"),
    "french": N_("French"),
    "german": N_("German"),
    "greek": N_("Greek"),
    "hungarian": N_("Hungarian"),
    "italian": N_("Italian"),
    "japanese": N_("Japanese"),
    "koreana": N_("Korean"),
    "latam": N_("Latin American Spanish"),
    "norwegian": N_("Norwegian"),
    "polish": N_("Polish"),
    "portuguese": N_("Portuguese"),
    "romanian": N_("Romanian"),
    "russian": N_("Russian"),
    "schinese": N_("Simplified Chinese"),
    "spanish": N_("Spanish"),
    "swedish": N_("Swedish"),
    "tchinese": N_("Traditional Chinese"),
    "thai": N_("Thai"),
    "turkish": N_("Turkish"),
    "ukrainian": N_("Ukrainian"),
    "vietnamese": N_("Vietnamese"),
}


def _identity(message: str) -> str:
    return message


def language_label(
    language: str,
    translate: Callable[[str], str] = _identity,
) -> str:
    name = translate(LANGUAGE_NAMES[language])
    if language == engine.DEFAULT_LANGUAGE:
        return translate("{language} — recommended").format(language=name)
    return name


def language_choices(
    translate: Callable[[str], str] = _identity,
) -> dict[str, str]:
    return {
        language_label(language, translate): language
        for language in sorted(
            engine.RECOGNIZED_LANGUAGES,
            key=lambda value: (value != engine.DEFAULT_LANGUAGE, LANGUAGE_NAMES[value]),
        )
    }


LANGUAGE_LABEL_TO_CODE = language_choices()
LANGUAGE_LABELS = tuple(LANGUAGE_LABEL_TO_CODE)


FEATURES = (
    {
        "key": "wearables_attachments",
        "categories": (
            engine.CATEGORY_STANDARD_WEARABLES,
            engine.CATEGORY_ADDITIONAL_WEARABLES,
        ),
        "title": N_("Wearables & attachments"),
        "description": N_("Restore equipped items and attachments."),
        "tag": N_("SUPPORTED"),
    },
    {
        "key": "hero_transformations",
        "categories": (engine.CATEGORY_SPECIAL_MODELS,),
        "title": N_("Hero transformations"),
        "description": N_("Restore transformations, pets & summons."),
        "tag": N_("SUPPORTED"),
    },
    {
        "key": "persona_models",
        "categories": (engine.CATEGORY_PERSONA_MODELS,),
        "title": N_("Personas"),
        "description": N_("Restore Persona models; still experimental."),
        "tag": N_("EXPERIMENTAL"),
    },
    {
        "key": "particle_effects",
        "categories": (engine.CATEGORY_PARTICLE_EFFECTS,),
        "title": N_("Particles & effects"),
        "description": N_("Restore default particles and effects."),
        "tag": N_("SUPPORTED"),
    },
)

OPERATION_NAMES = {
    "detect": N_("Detect"),
    "status": N_("Status"),
    "build": N_("Build"),
    "clean": N_("Clean"),
}


def settings_file() -> Path:
    return engine.application_root() / ".work" / SETTINGS_FILENAME


def _default_ui_settings() -> dict:
    return {
        "format_version": SETTINGS_FORMAT_VERSION,
        "dota_path": None,
        "enabled_categories": sorted(engine.DEFAULT_CATEGORIES),
        "ui_locale": UI_LOCALE_AUTO,
        "compatibility_language": engine.DEFAULT_LANGUAGE,
    }


def load_ui_settings(path: Optional[Path] = None) -> dict:
    default = _default_ui_settings()
    source = path or settings_file()
    if not source.is_file():
        return default
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default
    if not isinstance(payload, dict):
        return default
    format_version = payload.get("format_version")
    if format_version not in {1, SETTINGS_FORMAT_VERSION}:
        return default
    dota_path = payload.get("dota_path")
    categories = payload.get("enabled_categories")
    compatibility_language_value = payload.get(
        "language" if format_version == 1 else "compatibility_language",
        engine.DEFAULT_LANGUAGE,
    )
    ui_locale_value = (
        UI_LOCALE_AUTO
        if format_version == 1
        else payload.get("ui_locale", UI_LOCALE_AUTO)
    )
    if dota_path is not None and not isinstance(dota_path, str):
        dota_path = None
    if not isinstance(categories, list) or not all(
        isinstance(value, str) for value in categories
    ):
        categories = list(engine.DEFAULT_CATEGORIES)
    selected = sorted(set(categories).intersection(engine.SUPPORTED_CATEGORIES))
    try:
        compatibility_language = (
            engine.validate_language(compatibility_language_value)
            if isinstance(compatibility_language_value, str)
            else engine.DEFAULT_LANGUAGE
        )
    except ValueError:
        compatibility_language = engine.DEFAULT_LANGUAGE
    try:
        ui_locale = (
            normalize_ui_locale(ui_locale_value)
            if isinstance(ui_locale_value, str)
            else UI_LOCALE_AUTO
        )
    except ValueError:
        ui_locale = UI_LOCALE_AUTO
    return {
        "format_version": SETTINGS_FORMAT_VERSION,
        "dota_path": dota_path,
        "enabled_categories": selected,
        "ui_locale": ui_locale,
        "compatibility_language": compatibility_language,
    }


def save_ui_settings(
    dota_path: str,
    categories: set[str],
    path: Optional[Path] = None,
    *,
    compatibility_language: str = engine.DEFAULT_LANGUAGE,
    ui_locale: str = UI_LOCALE_AUTO,
    language: Optional[str] = None,
) -> None:
    # Preserve the old keyword for source callers while new code uses the
    # unambiguous compatibility-language name.
    if language is not None:
        compatibility_language = language
    engine.write_json(
        path or settings_file(),
        {
            "format_version": SETTINGS_FORMAT_VERSION,
            "dota_path": dota_path or None,
            "enabled_categories": sorted(categories),
            "ui_locale": normalize_ui_locale(ui_locale),
            "compatibility_language": engine.validate_language(
                compatibility_language
            ),
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
    translate: Callable[[str], str] = _identity,
) -> dict[str, str]:
    state = result.get("status", "unknown")
    presentations = {
        "current": (
            translate("CURRENT"),
            GREEN,
            translate("Installed Dota build matches the build used for these overrides"),
            translate("Rebuild Overrides"),
        ),
        "stale": (
            translate("UPDATE FOUND"),
            AMBER,
            translate("Installed Dota build changed since the last build"),
            translate("Rebuild for Installed Build"),
        ),
        "legacy": (
            translate("REBUILD REQUIRED"),
            RED,
            translate("Legacy loose overrides are no longer loaded by current Dota"),
            translate("Build VPK Overrides"),
        ),
        "broken": (
            translate("REPAIR REQUIRED"),
            RED,
            translate("The owned VPK is missing or failed its checksum check"),
            translate("Repair Overrides"),
        ),
        "not_built": (
            translate("NOT BUILT"),
            BLUE,
            translate("No owned disabler build marker was found"),
            translate("Build Overrides"),
        ),
        "unknown": (
            translate("CHECK NEEDED"),
            AMBER,
            translate("The previous run has no comparable Dota-build record"),
            translate("Rebuild Overrides"),
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
            translate("CHANGES PENDING"),
            AMBER,
            translate("Category selection changed; rebuild to apply it"),
            translate("Apply Selection"),
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
    "OPERATION_NAMES",
    "RED",
    "SETTINGS_FILENAME",
    "SETTINGS_FORMAT_VERSION",
    "SURFACE",
    "SURFACE_ALT",
    "SURFACE_HOVER",
    "TEXT",
    "TEXT_SOFT",
    "UI_LOCALE_AUTO",
    "engine",
    "language_choices",
    "language_label",
    "load_ui_settings",
    "resolve_initial_dota",
    "save_ui_settings",
    "settings_file",
    "status_matches_path",
    "status_presentation",
    "try_get_status",
]
