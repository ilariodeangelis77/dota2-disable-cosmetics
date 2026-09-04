"""Tests for the patcher UI's locale selection and gettext boundary."""

from __future__ import annotations

import ast
import gettext
import os
import string
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dota_disabler.ui_i18n import (
    UI_LOCALE_AUTO,
    available_ui_locales,
    load_ui_translator,
    normalize_ui_locale,
    resolve_ui_locale,
    system_ui_locale,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCALIZABLE_SOURCES = (
    PROJECT_ROOT / "dota_disabler/gui.py",
    PROJECT_ROOT / "dota_disabler/gui_model.py",
)


def shipped_ui_locales() -> tuple[str, ...]:
    locales_root = PROJECT_ROOT / "dota_disabler/locales"
    return tuple(
        sorted(
            child.name.replace("_", "-")
            for child in locales_root.iterdir()
            if child.is_dir() and (child / "LC_MESSAGES/ui.po").is_file()
        )
    )


def source_message_ids() -> set[str]:
    messages: set[str] = set()
    for path in LOCALIZABLE_SOURCES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            name = (
                function.attr
                if isinstance(function, ast.Attribute)
                else function.id if isinstance(function, ast.Name) else None
            )
            argument_index = {
                "_tr": 0,
                "translate": 0,
                "N_": 0,
                "ngettext": 0,
                "_set_translated_text": 1,
                "_show_status_error": 1,
            }.get(name)
            if argument_index is None:
                continue
            for argument in node.args[argument_index : argument_index + 1]:
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                    messages.add(argument.value)
    return messages


def template_message_ids() -> set[str]:
    """Read msgids from the POT with only Python's string-literal parser."""
    lines = (PROJECT_ROOT / "dota_disabler/locales/ui.pot").read_text(
        encoding="utf-8"
    ).splitlines()
    messages: set[str] = set()
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.startswith("msgid "):
            index += 1
            continue
        parts = [ast.literal_eval(line.removeprefix("msgid "))]
        index += 1
        while index < len(lines) and lines[index].startswith('"'):
            parts.append(ast.literal_eval(lines[index]))
            index += 1
        message = "".join(parts)
        if message:
            messages.add(message)
    return messages


def catalog_translations(ui_locale: str) -> dict[str, list[str]]:
    directory = PROJECT_ROOT / "dota_disabler/locales"
    language = ui_locale.replace("-", "_")
    with (directory / language / "LC_MESSAGES/ui.mo").open("rb") as stream:
        translation = gettext.GNUTranslations(stream)
    messages: dict[str, list[str]] = {}
    for key, value in translation._catalog.items():
        if not key:
            continue
        message_id = key[0] if isinstance(key, tuple) else key
        messages.setdefault(message_id, []).append(value)
    return messages


def format_fields(message: str) -> set[str]:
    return {
        field_name
        for _literal, field_name, _format_spec, _conversion in string.Formatter().parse(
            message
        )
        if field_name is not None
    }


class UiLocalizationTests(unittest.TestCase):
    def test_translation_template_matches_every_marked_source_string(self):
        self.assertEqual(template_message_ids(), source_message_ids())

    def test_locale_normalization_handles_os_and_chinese_names(self):
        self.assertEqual(normalize_ui_locale("ru_RU.UTF-8"), "ru-RU")
        self.assertEqual(normalize_ui_locale("zh_CN"), "zh-Hans")
        self.assertEqual(normalize_ui_locale("zh-TW"), "zh-Hant")
        self.assertEqual(normalize_ui_locale("C"), "en")
        self.assertEqual(normalize_ui_locale("AUTO"), UI_LOCALE_AUTO)
        with self.assertRaises(ValueError):
            normalize_ui_locale("../ru")

    def test_resolution_uses_exact_then_language_then_english_fallback(self):
        available = {"en", "ru", "zh-Hans"}
        self.assertEqual(
            resolve_ui_locale(
                UI_LOCALE_AUTO,
                available=available,
                detected_locale="ru_RU",
            ),
            "ru",
        )
        self.assertEqual(
            resolve_ui_locale("zh_CN", available=available),
            "zh-Hans",
        )
        self.assertEqual(resolve_ui_locale("de-DE", available=available), "en")

    def test_catalog_discovery_requires_a_compiled_catalog(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            russian = root / "ru" / "LC_MESSAGES"
            russian.mkdir(parents=True)
            (russian / "ui.po").write_text("", encoding="utf-8")
            self.assertEqual(available_ui_locales(root), ("en",))
            (russian / "ui.mo").write_bytes(b"")
            self.assertEqual(available_ui_locales(root), ("en", "ru"))

    def test_system_locale_prefers_environment_and_normalizes_it(self):
        with mock.patch.dict(os.environ, {"LANG": "zh_TW.UTF-8"}, clear=True):
            with mock.patch(
                "dota_disabler.ui_i18n.locale.getlocale",
                return_value=(None, None),
            ):
                self.assertEqual(system_ui_locale(), "zh-Hant")

    def test_english_translator_is_an_identity_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            translator = load_ui_translator("en", catalog_root=Path(temporary))
            self.assertEqual(translator.locale, "en")
            self.assertEqual(translator.gettext("Build Overrides"), "Build Overrides")
            self.assertEqual(
                translator.ngettext("one category", "many categories", 2),
                "many categories",
            )

    def test_shipped_catalogs_are_complete_and_preserve_placeholders(self):
        expected = source_message_ids()
        self.assertGreater(len(expected), 100)
        for ui_locale in shipped_ui_locales():
            with self.subTest(ui_locale=ui_locale):
                translations = catalog_translations(ui_locale)
                self.assertEqual(set(translations), expected)
                for message_id, localized_values in translations.items():
                    self.assertTrue(all(localized_values))
                    for localized in localized_values:
                        self.assertEqual(
                            format_fields(localized),
                            format_fields(message_id),
                            message_id,
                        )

    def test_shipped_catalogs_load_and_russian_plural_rules_are_used(self):
        self.assertTrue(set(shipped_ui_locales()).issubset(available_ui_locales()))
        expected_refresh = {
            "ru": "Обновить",
            "zh-Hans": "刷新",
            "zh-Hant": "重新整理",
        }
        singular = "{selected} of {total} category selected"
        plural = "{selected} of {total} categories selected"
        for ui_locale, expected in expected_refresh.items():
            with self.subTest(ui_locale=ui_locale):
                translator = load_ui_translator(ui_locale)
                self.assertEqual(translator.locale, ui_locale)
                self.assertEqual(translator.gettext("Refresh"), expected)

        russian = load_ui_translator("ru")
        self.assertTrue(russian.ngettext(singular, plural, 1).startswith("Выбрана"))
        self.assertTrue(russian.ngettext(singular, plural, 2).startswith("Выбраны"))
        self.assertTrue(russian.ngettext(singular, plural, 5).startswith("Выбрано"))


if __name__ == "__main__":
    unittest.main()
