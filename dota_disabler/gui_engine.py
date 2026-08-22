"""Narrow service surface consumed by the desktop adapters.

Keeping this facade independent of ``public`` and ``cli`` prevents the GUI
from closing a dependency cycle through the compatibility API.
"""

from .application import build_cosmetics
from .constants import (
    CATEGORY_ADDITIONAL_WEARABLES,
    CATEGORY_PARTICLE_EFFECTS,
    CATEGORY_PERSONA_MODELS,
    CATEGORY_SPECIAL_MODELS,
    CATEGORY_STANDARD_WEARABLES,
    DEFAULT_CATEGORIES,
    DEFAULT_LANGUAGE,
    RECOGNIZED_LANGUAGES,
    SUPPORTED_CATEGORIES,
)
from .deployment import clean_cosmetics, get_status, validate_language
from .domain import BuildOptions, BuildResult, CleanResult
from .errors import UnsafeOutputError
from .paths import application_root
from .reporting import write_json
from .version import VERSION
from .versioning import dota_version_label, find_dota_install


__all__ = [
    "CATEGORY_ADDITIONAL_WEARABLES",
    "CATEGORY_PARTICLE_EFFECTS",
    "CATEGORY_PERSONA_MODELS",
    "CATEGORY_SPECIAL_MODELS",
    "CATEGORY_STANDARD_WEARABLES",
    "DEFAULT_CATEGORIES",
    "DEFAULT_LANGUAGE",
    "RECOGNIZED_LANGUAGES",
    "SUPPORTED_CATEGORIES",
    "VERSION",
    "BuildOptions",
    "BuildResult",
    "CleanResult",
    "UnsafeOutputError",
    "application_root",
    "build_cosmetics",
    "clean_cosmetics",
    "dota_version_label",
    "find_dota_install",
    "get_status",
    "validate_language",
    "write_json",
]
