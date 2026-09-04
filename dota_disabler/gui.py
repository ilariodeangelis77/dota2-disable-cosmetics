"""Native desktop dashboard for the Dota 2 Cosmetic Disabler.

The module deliberately creates no global Tk window so importing it is safe for
the CLI, tests, and PyInstaller analysis.
"""

from __future__ import annotations

import ctypes
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, font as tkfont, messagebox, ttk
from typing import Callable, Optional

from . import gui_engine as engine
from .gui_model import (
    ACCENT,
    ACCENT_HOVER,
    AMBER,
    BG,
    BLUE,
    BORDER,
    BORDER_SOFT,
    FEATURES,
    GREEN,
    LANGUAGE_NAMES,
    MUTED,
    OPERATION_NAMES,
    RED,
    SETTINGS_FILENAME,
    SURFACE,
    SURFACE_ALT,
    SURFACE_HOVER,
    TEXT,
    TEXT_SOFT,
    language_choices,
    language_label,
    load_ui_settings,
    resolve_initial_dota,
    save_ui_settings,
    settings_file,
    status_matches_path,
    status_presentation,
    try_get_status,
)
from .ui_i18n import (
    UI_LOCALE_AUTO,
    UI_LOCALE_NAMES,
    UiTranslator,
    available_ui_locales,
    load_ui_translator,
    normalize_ui_locale,
)


FONT_BODY = "DotaBody"
FONT_BODY_SMALL = "DotaBodySmall"
FONT_BOLD = "DotaBold"
FONT_BOLD_SMALL = "DotaBoldSmall"
FONT_CAPTION = "DotaCaption"
FONT_TAG = "DotaTag"
FONT_TITLE = "DotaTitle"
FONT_LOGO = "DotaLogo"
FONT_LOG = "DotaLog"

_CJK_FONT_CANDIDATES = {
    "zh-Hans": (
        "Microsoft YaHei UI",
        "Microsoft YaHei",
        "PingFang SC",
        "Noto Sans CJK SC",
        "WenQuanYi Zen Hei",
    ),
    "zh-Hant": (
        "Microsoft JhengHei UI",
        "Microsoft JhengHei",
        "PingFang TC",
        "Noto Sans CJK TC",
        "Heiti TC",
    ),
}


def preferred_ui_font_family(root: tk.Misc, ui_locale: str) -> str:
    """Choose an installed UI font with native glyph coverage for the locale."""
    default_family = str(
        tkfont.nametofont("TkDefaultFont", root=root).actual("family")
    )
    candidates = _CJK_FONT_CANDIDATES.get(ui_locale)
    if not candidates:
        return default_family
    installed = {family.casefold(): family for family in tkfont.families(root)}
    for candidate in candidates:
        if candidate.casefold() in installed:
            return installed[candidate.casefold()]
    return default_family


def configure_dashboard_fonts(
    root: tk.Misc,
    ui_locale: str,
    retained_fonts: Optional[dict[str, tkfont.Font]] = None,
) -> tuple[dict[str, str], dict[str, tkfont.Font]]:
    """Create and retain the dashboard fonts, returning families and objects."""
    ui_family = preferred_ui_font_family(root, ui_locale)
    installed = {family.casefold(): family for family in tkfont.families(root)}
    emphasis_family = installed.get(
        f"{ui_family} Semibold".casefold(),
        ui_family,
    )
    emphasis_weight = "normal" if emphasis_family != ui_family else "bold"
    fixed_family = str(tkfont.nametofont("TkFixedFont", root=root).actual("family"))
    if ui_locale in _CJK_FONT_CANDIDATES:
        emphasis_family = ui_family
        emphasis_weight = "bold"
        fixed_family = ui_family
    specifications = {
        FONT_BODY: (ui_family, 10, "normal"),
        FONT_BODY_SMALL: (ui_family, 9, "normal"),
        FONT_BOLD: (emphasis_family, 10, emphasis_weight),
        FONT_BOLD_SMALL: (emphasis_family, 9, emphasis_weight),
        FONT_CAPTION: (emphasis_family, 8, emphasis_weight),
        FONT_TAG: (emphasis_family, 7, emphasis_weight),
        FONT_TITLE: (emphasis_family, 17, emphasis_weight),
        FONT_LOGO: (emphasis_family, 14, emphasis_weight),
        FONT_LOG: (fixed_family, 9, "normal"),
    }
    existing = set(tkfont.names(root))
    configured_fonts: dict[str, tkfont.Font] = {}
    for name, (family, size, weight) in specifications.items():
        configured = (retained_fonts or {}).get(name)
        if configured is None:
            configured = tkfont.Font(root=root, name=name, exists=name in existing)
        configured.configure(family=family, size=size, weight=weight)
        configured_fonts[name] = configured
    families = {
        "ui": ui_family,
        "emphasis": emphasis_family,
        "log": fixed_family,
    }
    return families, configured_fonts


def desktop_work_area(root: tk.Tk) -> tuple[int, int, int, int]:
    """Return the usable desktop rectangle, excluding the Windows taskbar when available."""
    if os.name == "nt":
        class Rect(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        rect = Rect()
        try:
            if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
                return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
        except (AttributeError, OSError):
            pass
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


def _rounded_rectangle(canvas: tk.Canvas, x1: int, y1: int, x2: int, y2: int, radius: int, **kwargs) -> None:
    canvas.create_rectangle(x1 + radius, y1, x2 - radius, y2, **kwargs)
    canvas.create_rectangle(x1, y1 + radius, x2, y2 - radius, **kwargs)
    canvas.create_oval(x1, y1, x1 + radius * 2, y1 + radius * 2, **kwargs)
    canvas.create_oval(x2 - radius * 2, y1, x2, y1 + radius * 2, **kwargs)
    canvas.create_oval(x1, y2 - radius * 2, x1 + radius * 2, y2, **kwargs)
    canvas.create_oval(x2 - radius * 2, y2 - radius * 2, x2, y2, **kwargs)


class ToggleSwitch(tk.Canvas):
    def __init__(self, parent: tk.Misc, variable: tk.BooleanVar, command: Callable[[], None]):
        super().__init__(
            parent,
            width=46,
            height=26,
            bg=SURFACE_ALT,
            bd=0,
            highlightthickness=0,
            takefocus=1,
        )
        self.variable = variable
        self.command = command
        self.enabled = True
        self.focused = False
        self.configure(cursor="hand2")
        self.bind("<Button-1>", self._toggle)
        self.bind("<space>", self._toggle)
        self.bind("<Return>", self._toggle)
        self.bind("<FocusIn>", self._focus_changed)
        self.bind("<FocusOut>", self._focus_changed)
        self.variable.trace_add("write", lambda *_: self.redraw())
        self.redraw()

    def _toggle(self, _event=None) -> str:
        if self.enabled:
            self.focus_set()
            self.variable.set(not self.variable.get())
            self.command()
        return "break"

    def _focus_changed(self, event: tk.Event) -> None:
        self.focused = event.type == tk.EventType.FocusIn
        self.redraw()

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow", takefocus=1 if enabled else 0)
        self.redraw()

    def redraw(self) -> None:
        self.delete("all")
        on = self.variable.get()
        track = ACCENT if on and self.enabled else BORDER if self.enabled else BORDER_SOFT
        knob = TEXT if self.enabled else MUTED
        _rounded_rectangle(self, 1, 2, 45, 24, 11, fill=track, outline=track)
        center = 33 if on else 13
        self.create_oval(center - 8, 5, center + 8, 21, fill=knob, outline=knob)
        if self.focused and self.enabled:
            self.create_rectangle(0, 0, 45, 25, outline=BLUE, width=1)


class FlatButton(tk.Button):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        text: str,
        command: Callable[[], None],
        primary: bool = False,
        compact: bool = False,
    ):
        self.primary = primary
        background = ACCENT if primary else SURFACE_ALT
        hover = ACCENT_HOVER if primary else SURFACE_HOVER
        super().__init__(
            parent,
            text=text,
            command=command,
            bg=background,
            fg=TEXT,
            activebackground=hover,
            activeforeground=TEXT,
            disabledforeground=MUTED,
            relief="flat",
            bd=0,
            cursor="hand2",
            font=FONT_BOLD,
            padx=15 if compact else 20,
            pady=5 if compact else 11,
            highlightthickness=0,
        )
        self._normal_bg = background
        self._hover_bg = hover
        self.bind("<Enter>", lambda _event: self._hover(True))
        self.bind("<Leave>", lambda _event: self._hover(False))

    def _hover(self, entered: bool) -> None:
        if str(self["state"]) == "normal":
            self.configure(bg=self._hover_bg if entered else self._normal_bg)

    def set_text(self, value: str) -> None:
        self.configure(text=value)


def _card(parent: tk.Misc) -> tuple[tk.Frame, tk.Frame]:
    border = tk.Frame(parent, bg=BORDER, bd=0)
    inner = tk.Frame(border, bg=SURFACE)
    inner.pack(fill="both", expand=True, padx=1, pady=1)
    return border, inner


class DisablerApp:
    def __init__(
        self,
        root: tk.Tk,
        *,
        start_detection: bool = True,
        ui_locale_override: Optional[str] = None,
    ):
        self.root = root
        self.events: queue.Queue[tuple] = queue.Queue()
        self.busy = False
        self.closing_requested = False
        self._applying_ui_locale = False
        self._text_bindings: dict[tk.Widget, Callable[[], str]] = {}
        self.settings = load_ui_settings()
        self.ui_locale = (
            normalize_ui_locale(ui_locale_override)
            if ui_locale_override is not None
            else self.settings["ui_locale"]
        )
        self.translator = load_ui_translator(self.ui_locale)
        self.compatibility_language_choices = language_choices(self._tr)
        self.ui_locale_choices = self._build_ui_locale_choices()
        self.path_var = tk.StringVar(value=self.settings.get("dota_path") or "")
        self.language_var = tk.StringVar(
            value=language_label(
                self.settings["compatibility_language"],
                self._tr,
            )
        )
        self.ui_locale_var = tk.StringVar(
            value=next(
                label
                for label, locale_code in self.ui_locale_choices.items()
                if locale_code == self.ui_locale
            )
        )
        enabled_categories = set(self.settings["enabled_categories"])
        self.category_vars = {
            feature["key"]: tk.BooleanVar(
                # Grouped UI choices preserve any previously enabled internal
                # category and become atomic the next time settings are saved.
                value=any(
                    category in enabled_categories
                    for category in feature["categories"]
                )
            )
            for feature in FEATURES
        }
        self.toggle_controls: list[ToggleSwitch] = []
        self.busy_controls: list[tk.Widget] = []
        self.last_result: Optional[engine.BuildResult] = None
        self.last_status: Optional[dict] = None
        self._configure_root()
        self._build_layout()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(80, self._drain_events)
        if start_detection:
            self.root.after(150, self._detect_on_startup)

    def _tr(self, message: str) -> str:
        return self.translator.gettext(message)

    def _tr_marked(self, message: str) -> str:
        """Translate text already marked with N_ at its declaration site."""
        return self.translator.gettext(message)

    def _set_bound_text(
        self,
        widget: tk.Widget,
        render: Callable[[], str],
    ) -> None:
        self._text_bindings[widget] = render
        widget.configure(text=render())

    def _set_translated_text(
        self,
        widget: tk.Widget,
        message: str,
        *,
        prefix: str = "",
        suffix: str = "",
        **format_values: object,
    ) -> None:
        values = dict(format_values)

        def render() -> str:
            translated = self._tr(message).format(**values)
            return f"{prefix}{translated}{suffix}"

        self._set_bound_text(widget, render)

    def _set_literal_text(self, widget: tk.Widget, text: object) -> None:
        self._text_bindings.pop(widget, None)
        widget.configure(text=str(text))

    def _set_marked_text(self, widget: tk.Widget, message: str) -> None:
        """Bind text whose source literal is already declared through N_."""
        self._set_translated_text(widget, message)

    def _refresh_bound_text(self) -> None:
        for widget, render in tuple(self._text_bindings.items()):
            if widget.winfo_exists():
                widget.configure(text=render())

    def _build_ui_locale_choices(self) -> dict[str, str]:
        available = available_ui_locales()
        resolved_name = UI_LOCALE_NAMES.get(
            self.translator.locale,
            self.translator.locale,
        )
        choices = {
            self._tr("System default ({language})").format(language=resolved_name): (
                UI_LOCALE_AUTO
            )
        }
        choices.update(
            {
                UI_LOCALE_NAMES.get(locale_code, locale_code): locale_code
                for locale_code in available
            }
        )
        if self.ui_locale != UI_LOCALE_AUTO and self.ui_locale not in choices.values():
            self.ui_locale = self.translator.locale
        return choices

    def _configure_root(self) -> None:
        self.root.title(f"Dota 2 Cosmetic Disabler {engine.VERSION}")
        self.root.configure(bg=BG)
        self.font_families, self.fonts = configure_dashboard_fonts(
            self.root,
            self.translator.locale,
            getattr(self, "fonts", None),
        )
        work_left, work_top, work_width, work_height = desktop_work_area(self.root)
        width = min(1280, max(900, work_width - 32))
        height = min(700, max(560, work_height - 64))
        self.root.geometry(f"{width}x{height}")
        self.root.minsize(min(1024, width), min(620, height))
        self.root.option_add("*Font", FONT_BODY)
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(
            "Dota.TCombobox",
            font=FONT_BODY,
            fieldbackground=SURFACE_ALT,
            background=SURFACE_ALT,
            foreground=TEXT,
            bordercolor=BORDER,
            lightcolor=BORDER,
            darkcolor=BORDER,
            arrowcolor=TEXT_SOFT,
            padding=6,
        )
        style.map(
            "Dota.TCombobox",
            fieldbackground=[("readonly", SURFACE_ALT), ("disabled", SURFACE)],
            foreground=[("readonly", TEXT), ("disabled", MUTED)],
            selectbackground=[("readonly", SURFACE_ALT)],
            selectforeground=[("readonly", TEXT)],
        )
        style.configure(
            "Dota.Horizontal.TProgressbar",
            troughcolor=SURFACE_ALT,
            background=ACCENT,
            bordercolor=SURFACE_ALT,
            lightcolor=ACCENT,
            darkcolor=ACCENT,
            thickness=4,
        )
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(1, weight=1)
        self._center_window(width, height, (work_left, work_top, work_width, work_height))

    def _center_window(self, width: int, height: int, work_area: tuple[int, int, int, int]) -> None:
        left, top, work_width, work_height = work_area
        x = left + max(0, (work_width - width) // 2)
        y = top + max(0, (work_height - height) // 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def _build_layout(self) -> None:
        self._build_header()
        workspace = tk.Frame(self.root, bg=BG)
        workspace.grid(row=1, column=0, sticky="nsew", padx=24, pady=(8, 18))
        # Let each pane keep its requested minimum before distributing spare
        # width. A uniform group forces the 5:7 ratio even on narrow desktops
        # and can compress the controls below their content width.
        workspace.grid_columnconfigure(0, weight=5)
        workspace.grid_columnconfigure(1, weight=7)
        workspace.grid_rowconfigure(0, weight=1)
        self.workspace = workspace

        controls = tk.Frame(workspace, bg=BG)
        controls.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        controls.grid_columnconfigure(0, weight=1)
        controls.grid_rowconfigure(1, weight=1)
        self.controls_column = controls

        self._build_installation_panel(controls)
        self._build_features_panel(controls)
        self._build_action_bar(controls)
        self._build_activity_panel(workspace)

    def _build_header(self) -> None:
        header = tk.Frame(self.root, bg=BG, height=82)
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 4))
        header.grid_columnconfigure(1, weight=1)
        self.header = header

        logo = tk.Canvas(header, width=46, height=46, bg=BG, bd=0, highlightthickness=0)
        logo.grid(row=0, column=0, rowspan=2, sticky="w", padx=(0, 14))
        _rounded_rectangle(logo, 1, 1, 45, 45, 11, fill=ACCENT, outline=ACCENT)
        logo.create_text(23, 23, text="D2", fill=TEXT, font=FONT_LOGO)

        self.header_title = tk.Label(
            header,
            text="",
            bg=BG,
            fg=TEXT,
            font=FONT_TITLE,
        )
        self.header_title.grid(row=0, column=1, sticky="sw")
        self._set_translated_text(self.header_title, "COSMETIC DISABLER")
        self.header_subtitle = tk.Label(
            header,
            text="",
            bg=BG,
            fg=MUTED,
            font=FONT_BODY_SMALL,
        )
        self.header_subtitle.grid(
            row=1,
            column=1,
            columnspan=4,
            sticky="nw",
            pady=(2, 0),
        )
        self._set_translated_text(
            self.header_subtitle,
            "Local model & effect overrides  ·  v{version}  ·  no injection",
            version=engine.VERSION,
        )

        self.header_status = tk.Label(
            header,
            text="",
            bg=SURFACE_ALT,
            fg=TEXT_SOFT,
            font=FONT_BOLD_SMALL,
            padx=9,
            pady=6,
        )
        self.header_status.grid(row=0, column=3, sticky="e", padx=(10, 0))
        self._set_translated_text(
            self.header_status,
            "DETECTING",
            prefix="  ",
            suffix="  ",
        )
        self.refresh_button = FlatButton(
            header,
            text="",
            command=self._refresh_status,
            compact=True,
        )
        self.refresh_button.grid(row=0, column=4, sticky="e", padx=(10, 0))
        self._set_translated_text(self.refresh_button, "Refresh")

        self.ui_locale_frame = tk.Frame(header, bg=BG)
        self.ui_locale_frame.grid(
            row=0,
            column=2,
            sticky="e",
            padx=(12, 0),
        )
        self.ui_locale_label = tk.Label(
            self.ui_locale_frame,
            text="",
            bg=BG,
            fg=MUTED,
            font=FONT_CAPTION,
        )
        self.ui_locale_label.grid(row=0, column=0, sticky="e", padx=(0, 7))
        self._set_translated_text(self.ui_locale_label, "GUI language")
        self.ui_locale_combo = ttk.Combobox(
            self.ui_locale_frame,
            textvariable=self.ui_locale_var,
            values=tuple(self.ui_locale_choices),
            state="readonly",
            width=24,
            style="Dota.TCombobox",
        )
        self.ui_locale_combo.grid(row=0, column=1, sticky="e")
        self.ui_locale_combo.bind("<<ComboboxSelected>>", self._ui_locale_changed)
        self.busy_controls.extend((self.refresh_button, self.ui_locale_combo))

    def _build_installation_panel(self, parent: tk.Frame) -> None:
        border, panel = _card(parent)
        border.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        panel.grid_columnconfigure(0, weight=1)
        self.installation_card = border

        self.installation_heading = tk.Label(
            panel,
            text="",
            bg=SURFACE,
            fg=MUTED,
            font=FONT_BOLD_SMALL,
        )
        self.installation_heading.grid(
            row=0,
            column=0,
            sticky="w",
            padx=18,
            pady=(11, 6),
        )
        self._set_translated_text(
            self.installation_heading,
            "DOTA 2 INSTALLATION & BUILD STATUS",
        )

        path_frame = tk.Frame(panel, bg=SURFACE_ALT, highlightbackground=BORDER, highlightthickness=1)
        path_frame.grid(row=1, column=0, sticky="ew", padx=18)
        path_frame.grid_columnconfigure(0, weight=1)
        self.path_entry = tk.Entry(
            path_frame,
            textvariable=self.path_var,
            bg=SURFACE_ALT,
            fg=TEXT,
            insertbackground=TEXT,
            selectbackground=ACCENT,
            relief="flat",
            bd=0,
            font=FONT_BODY,
        )
        self.path_entry.grid(row=0, column=0, sticky="ew", padx=11, pady=7)
        self.path_entry.bind("<Return>", lambda _event: self._refresh_status())
        self.path_entry.bind("<KeyRelease>", self._path_edited)
        self.busy_controls.append(self.path_entry)

        path_actions = tk.Frame(panel, bg=SURFACE)
        path_actions.grid(row=2, column=0, sticky="ew", padx=18, pady=(6, 0))
        self.browse_button = FlatButton(
            path_actions,
            text="",
            command=self._browse,
            compact=True,
        )
        self.browse_button.pack(side="left")
        self._set_translated_text(self.browse_button, "Browse")
        self.detect_button = FlatButton(
            path_actions,
            text="",
            command=self._auto_detect,
            compact=True,
        )
        self.detect_button.pack(side="left", padx=(8, 0))
        self._set_translated_text(self.detect_button, "Auto-detect")
        self.path_source = tk.Label(
            path_actions,
            text="",
            bg=SURFACE,
            fg=MUTED,
            font=FONT_BODY_SMALL,
        )
        self.path_source.pack(side="right")
        self._set_translated_text(self.path_source, "Waiting for detection")
        self.busy_controls.extend((self.browse_button, self.detect_button))

        language_row = tk.Frame(panel, bg=SURFACE)
        language_row.grid(row=3, column=0, sticky="ew", padx=18, pady=(7, 0))
        language_row.grid_columnconfigure(0, weight=1)
        self.compatibility_language_label = tk.Label(
            language_row,
            text="",
            bg=SURFACE,
            fg=MUTED,
            font=FONT_BODY_SMALL,
        )
        self.compatibility_language_label.grid(row=0, column=0, sticky="w")
        self._set_translated_text(
            self.compatibility_language_label,
            "Dota compatibility mount",
        )
        self.language_combo = ttk.Combobox(
            language_row,
            textvariable=self.language_var,
            values=tuple(self.compatibility_language_choices),
            state="readonly",
            width=28,
            style="Dota.TCombobox",
            justify="left",
        )
        self.language_combo.grid(row=1, column=0, sticky="ew", pady=(3, 0))
        self.language_combo.bind("<<ComboboxSelected>>", self._language_changed)
        self.busy_controls.append(self.language_combo)

        versions = tk.Frame(panel, bg=SURFACE)
        versions.grid(row=4, column=0, sticky="ew", padx=18, pady=(8, 0))
        versions.grid_columnconfigure(0, weight=1)
        versions.grid_columnconfigure(1, weight=1)
        installed = tk.Frame(versions, bg=SURFACE)
        installed.grid(row=0, column=0, sticky="w")
        self.installed_version_heading = tk.Label(
            installed,
            text="",
            bg=SURFACE,
            fg=MUTED,
            font=FONT_CAPTION,
            justify="left",
            wraplength=190,
        )
        self.installed_version_heading.grid(
            row=0, column=0, sticky="w"
        )
        self._set_translated_text(
            self.installed_version_heading,
            "INSTALLED DOTA BUILD",
        )
        self.current_version = tk.Label(
            installed,
            text="—",
            bg=SURFACE,
            fg=TEXT,
            font=FONT_BOLD_SMALL,
            anchor="w",
        )
        self.current_version.grid(row=1, column=0, sticky="w", pady=(2, 0))
        recorded = tk.Frame(versions, bg=SURFACE)
        recorded.grid(row=0, column=1, sticky="e")
        self.recorded_version_heading = tk.Label(
            recorded,
            text="",
            bg=SURFACE,
            fg=MUTED,
            font=FONT_CAPTION,
            justify="right",
            wraplength=190,
        )
        self.recorded_version_heading.grid(
            row=0, column=0, sticky="e"
        )
        self._set_translated_text(
            self.recorded_version_heading,
            "OVERRIDES BUILT FROM",
        )
        self.recorded_version = tk.Label(
            recorded,
            text="—",
            bg=SURFACE,
            fg=TEXT_SOFT,
            font=FONT_BOLD_SMALL,
            anchor="e",
        )
        self.recorded_version.grid(row=1, column=0, sticky="e", pady=(2, 0))

        self.status_detail = tk.Label(
            panel,
            text="",
            bg=SURFACE_ALT,
            fg=TEXT_SOFT,
            anchor="w",
            justify="left",
            padx=12,
            pady=5,
            font=FONT_BODY_SMALL,
            wraplength=360,
        )
        self.status_detail.grid(row=5, column=0, sticky="ew", padx=18, pady=(8, 10))
        self._set_translated_text(
            self.status_detail,
            "Looking for your Steam library…",
        )

    def _build_features_panel(self, parent: tk.Frame) -> None:
        border, panel = _card(parent)
        border.grid(row=1, column=0, sticky="nsew", pady=(0, 8))
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_columnconfigure(1, weight=1)
        panel.grid_rowconfigure(1, weight=1)
        self.features_card = border

        self.features_heading = tk.Label(
            panel,
            text="",
            bg=SURFACE,
            fg=MUTED,
            font=FONT_BOLD_SMALL,
        )
        self.features_heading.grid(
            row=0, column=0, sticky="w", padx=16, pady=(7, 3)
        )
        self._set_translated_text(self.features_heading, "COSMETICS TO DISABLE")
        presets = tk.Frame(panel, bg=SURFACE)
        presets.grid(row=0, column=1, sticky="e", padx=16, pady=(3, 1))
        self.select_all_button = FlatButton(
            presets,
            text="",
            command=lambda: self._set_all_categories(True),
            compact=True,
        )
        self.select_all_button.pack(side="left")
        self._set_translated_text(self.select_all_button, "Select all")
        self.clear_button = FlatButton(
            presets,
            text="",
            command=lambda: self._set_all_categories(False),
            compact=True,
        )
        self.clear_button.pack(side="left", padx=(6, 0))
        self._set_translated_text(self.clear_button, "Clear")
        self.busy_controls.extend((self.select_all_button, self.clear_button))
        supported = tk.Frame(panel, bg=SURFACE)
        supported.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=11, pady=(0, 5))
        feature_columns = 2
        for column in range(feature_columns):
            supported.grid_columnconfigure(column, weight=1, uniform="feature")
        for index, feature in enumerate(FEATURES):
            row, column = divmod(index, feature_columns)
            supported.grid_rowconfigure(row, weight=1, uniform="feature")
            self._build_feature_card(supported, feature, row, column)

    def _build_feature_card(self, parent: tk.Frame, feature: dict, row: int, column: int) -> None:
        card = tk.Frame(parent, bg=SURFACE_ALT, highlightbackground=BORDER_SOFT, highlightthickness=1)
        card.grid(row=row, column=column, sticky="nsew", padx=4, pady=2)
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(1, weight=1)
        feature_tag = tk.Label(
            card,
            text="",
            bg=SURFACE_ALT,
            fg=AMBER if feature["tag"] == "EXPERIMENTAL" else GREEN,
            font=FONT_TAG,
        )
        feature_tag.grid(row=0, column=0, sticky="w", padx=10, pady=(3, 0))
        self._set_marked_text(feature_tag, feature["tag"])
        toggle = ToggleSwitch(
            card,
            self.category_vars[feature["key"]],
            self._category_changed,
        )
        toggle.grid(row=0, column=1, sticky="e", padx=8, pady=(1, 0))
        self.toggle_controls.append(toggle)
        feature_title = tk.Label(
            card,
            text="",
            bg=SURFACE_ALT,
            fg=TEXT,
            font=FONT_BOLD,
            justify="left",
            wraplength=190,
        )
        feature_title.grid(
            row=1,
            column=0,
            columnspan=2,
            sticky="w",
            padx=10,
            pady=(0, 5),
        )
        self._set_marked_text(feature_title, feature["title"])

    def _build_action_bar(self, parent: tk.Frame) -> None:
        border, panel = _card(parent)
        border.grid(row=2, column=0, sticky="ew")
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_columnconfigure(1, weight=1)
        self.action_card = border

        self.action_summary = tk.Label(
            panel,
            text="",
            bg=SURFACE,
            fg=TEXT_SOFT,
            font=FONT_BODY_SMALL,
            anchor="w",
            justify="left",
            wraplength=360,
        )
        self.action_summary.grid(row=0, column=0, columnspan=2, sticky="ew", padx=14, pady=(7, 3))

        self.clean_button = FlatButton(
            panel,
            text="",
            command=self._clean,
            compact=True,
        )
        self.clean_button.grid(row=1, column=0, sticky="ew", padx=(14, 4), pady=(0, 8))
        self._set_translated_text(self.clean_button, "Remove owned overrides")
        self.build_button = FlatButton(
            panel,
            text="",
            command=self._build,
            primary=True,
            compact=True,
        )
        self.build_button.grid(row=1, column=1, sticky="ew", padx=(4, 14), pady=(0, 8))
        self._set_translated_text(self.build_button, "Build Overrides")
        self.busy_controls.extend((self.clean_button, self.build_button))
        self._update_action_summary()

    def _build_activity_panel(self, parent: tk.Frame) -> None:
        border, panel = _card(parent)
        border.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(2, weight=1)
        self.activity_card = border
        self.activity_panel = panel

        activity_header = tk.Frame(panel, bg=SURFACE)
        activity_header.grid(row=0, column=0, sticky="ew", padx=16, pady=(10, 6))
        activity_header.grid_columnconfigure(0, weight=1)
        self.activity_title = tk.Label(
            activity_header,
            text="",
            bg=SURFACE,
            fg=TEXT_SOFT,
            font=FONT_BOLD,
        )
        self.activity_title.grid(row=0, column=0, sticky="w")
        self._set_translated_text(self.activity_title, "Current activity")
        self.progress_label = tk.Label(
            activity_header,
            text="",
            bg=SURFACE,
            fg=TEXT_SOFT,
            font=FONT_BOLD_SMALL,
        )
        self.progress_label.grid(row=0, column=1, padx=(8, 4))
        self.result_actions = tk.Frame(activity_header, bg=SURFACE)
        self.result_actions.grid(
            row=1,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(6, 0),
        )
        self.open_report_button = FlatButton(
            self.result_actions,
            text="",
            command=self._open_report,
            compact=True,
        )
        self.open_report_button.pack(side="left")
        self._set_translated_text(self.open_report_button, "Report")
        self.open_output_button = FlatButton(
            self.result_actions,
            text="",
            command=self._open_output,
            compact=True,
        )
        self.open_output_button.pack(side="left", padx=(8, 0))
        self._set_translated_text(self.open_output_button, "Output")
        self.copy_launch_button = FlatButton(
            self.result_actions,
            text="",
            command=self._copy_launch_option,
            compact=True,
        )
        self.copy_launch_button.pack(side="left", padx=(8, 0))
        self._set_translated_text(
            self.copy_launch_button,
            "Copy launch option",
        )
        self.busy_controls.extend(
            (
                self.open_report_button,
                self.open_output_button,
                self.copy_launch_button,
            )
        )
        self.result_actions.grid_remove()

        self.progress = ttk.Progressbar(
            panel,
            mode="determinate",
            maximum=100,
            value=0,
            style="Dota.Horizontal.TProgressbar",
        )
        self.progress.grid(row=1, column=0, sticky="ew", padx=16)

        self.log = tk.Text(
            panel,
            height=2,
            width=1,
            bg="#0d131b",
            fg=TEXT_SOFT,
            insertbackground=TEXT,
            selectbackground=ACCENT,
            relief="flat",
            bd=0,
            padx=11,
            pady=8,
            wrap="word",
            state="disabled",
            font=FONT_LOG,
        )
        self.log.grid(row=2, column=0, sticky="nsew", padx=16, pady=(8, 12))
        self._append_log(
            self._tr(
                "Safety: unofficial overrides; test in Demo Hero first; "
                "no anti-cheat guarantee."
            )
        )
        self._append_log(self._tr("Ready. Dota will be detected automatically."))

    def _selected_categories(self) -> set[str]:
        return {
            category
            for feature in FEATURES
            if self.category_vars[feature["key"]].get()
            for category in feature["categories"]
        }

    def _selected_language(self) -> str:
        return self.compatibility_language_choices.get(
            self.language_var.get(),
            engine.DEFAULT_LANGUAGE,
        )

    def _activate_ui_locale(
        self,
        preference: str,
        translator: UiTranslator,
        compatibility_language: str,
    ) -> None:
        self.ui_locale = preference
        self.translator = translator
        self.font_families, self.fonts = configure_dashboard_fonts(
            self.root,
            self.translator.locale,
            self.fonts,
        )
        self.compatibility_language_choices = language_choices(self._tr)
        self.language_combo.configure(
            values=tuple(self.compatibility_language_choices)
        )
        self.language_var.set(
            language_label(compatibility_language, self._tr)
        )
        self.ui_locale_choices = self._build_ui_locale_choices()
        self.ui_locale_combo.configure(values=tuple(self.ui_locale_choices))
        self.ui_locale_var.set(
            next(
                label
                for label, locale_code in self.ui_locale_choices.items()
                if locale_code == self.ui_locale
            )
        )
        self._refresh_bound_text()
        self._update_action_summary()
        if self.last_status is not None:
            self._apply_status(self.last_status)
        self.root.update_idletasks()

    def _apply_ui_locale(self, selected: str, *, persist: bool = True) -> bool:
        if self.busy or self._applying_ui_locale or selected == self.ui_locale:
            return False
        normalized = normalize_ui_locale(selected)
        available = available_ui_locales()
        if normalized != UI_LOCALE_AUTO and normalized not in available:
            raise ValueError(f"GUI locale is not packaged: {normalized}")

        compatibility_language = self._selected_language()
        previous_preference = self.ui_locale
        previous_translator = self.translator
        next_translator = load_ui_translator(normalized)
        self._applying_ui_locale = True
        try:
            self._activate_ui_locale(
                normalized,
                next_translator,
                compatibility_language,
            )
            if persist:
                self._save_preferences()
        except Exception:
            self._activate_ui_locale(
                previous_preference,
                previous_translator,
                compatibility_language,
            )
            raise
        finally:
            self._applying_ui_locale = False
        return True

    def _ui_locale_changed(self, _event=None) -> None:
        if self.busy or self._applying_ui_locale:
            return
        selected = self.ui_locale_choices.get(self.ui_locale_var.get(), UI_LOCALE_AUTO)
        try:
            self._apply_ui_locale(selected)
        except Exception as exc:
            messagebox.showerror(
                self._tr("UI ERROR"),
                str(exc),
                parent=self.root,
            )

    def _update_action_summary(self) -> None:
        selected = sum(variable.get() for variable in self.category_vars.values())
        if selected:
            selection = self.translator.ngettext(
                "{selected} of {total} category selected",
                "{selected} of {total} categories selected",
                selected,
            ).format(selected=selected, total=len(FEATURES))
            language = self._tr_marked(LANGUAGE_NAMES[self._selected_language()])
            text = self._tr(
                "{selection}  ·  {language} compatibility mount"
            ).format(
                selection=selection,
                language=language,
            )
            color = TEXT_SOFT
        else:
            text = self._tr("Choose at least one cosmetic category before building")
            color = AMBER
        self.action_summary.configure(text=text, fg=color)

    def _category_changed(self) -> None:
        self._save_preferences()
        selected = sum(variable.get() for variable in self.category_vars.values())
        self._append_log(
            self.translator.ngettext(
                "Selected {selected} of {total} replacement category.",
                "Selected {selected} of {total} replacement categories.",
                selected,
            ).format(selected=selected, total=len(FEATURES))
        )
        self._update_action_summary()
        if self.last_status is not None and status_matches_path(self.last_status, self.path_var.get()):
            self._apply_status(self.last_status)

    def _language_changed(self, _event=None) -> None:
        if self.busy:
            return
        language = self._selected_language()
        self.last_status = None
        self._set_translated_text(self.build_button, "Build Overrides")
        self._save_preferences()
        self._update_action_summary()
        self._set_result_actions_visible(False)
        self.header_status.configure(fg=BLUE, bg=SURFACE_ALT)
        self._set_translated_text(
            self.header_status,
            "CHECKING MOUNT",
            prefix="  ",
            suffix="  ",
        )
        self.status_detail.configure(fg=BLUE)
        self._set_translated_text(
            self.status_detail,
            "Checking the {language} compatibility mount; rebuild to apply a new selection",
            language=language,
        )
        self._append_log(
            self._tr(
                "Compatibility mount changed to {language}. "
                "Dota's interface will remain English after building."
            ).format(language=language)
        )
        if self.path_var.get().strip():
            self._refresh_status()

    def _path_edited(self, _event=None) -> None:
        self.last_status = None
        self._set_translated_text(self.build_button, "Build Overrides")
        self._set_result_actions_visible(False)
        self.header_status.configure(fg=AMBER, bg=SURFACE_ALT)
        self._set_translated_text(
            self.header_status,
            "CHECK PATH",
            prefix="  ",
            suffix="  ",
        )
        self.status_detail.configure(fg=AMBER)
        self._set_translated_text(
            self.status_detail,
            "Folder changed; refresh to validate it",
        )
        self.path_source.configure(fg=AMBER)
        self._set_translated_text(
            self.path_source,
            "Edited · press Enter or Refresh",
        )

    def _set_all_categories(self, selected: bool) -> None:
        if self.busy:
            return
        for variable in self.category_vars.values():
            variable.set(selected)
        self._category_changed()

    def _save_preferences(self) -> None:
        try:
            save_ui_settings(
                self.path_var.get().strip(),
                self._selected_categories(),
                compatibility_language=self._selected_language(),
                ui_locale=self.ui_locale,
            )
        except OSError as exc:
            self._append_log(
                self._tr("Could not save UI settings: {error}").format(error=exc),
                error=True,
            )

    def _detect_on_startup(self) -> None:
        saved = self.settings.get("dota_path")
        language = self._selected_language()

        def operation() -> tuple[Path, Optional[dict], Optional[Exception]]:
            dota = resolve_initial_dota(saved)
            status, error = try_get_status(dota, language)
            return dota, status, error

        self._run_worker(
            "detect",
            operation,
            self._tr("Detecting Dota 2 installation…"),
        )

    def _auto_detect(self) -> None:
        language = self._selected_language()

        def operation() -> tuple[Path, Optional[dict], Optional[Exception]]:
            dota = engine.find_dota_install(None)
            status, error = try_get_status(dota, language)
            return dota, status, error

        self._run_worker(
            "detect",
            operation,
            self._tr("Searching Steam libraries…"),
        )

    def _browse(self) -> None:
        selected = filedialog.askdirectory(
            parent=self.root,
            title=self._tr("Choose the 'dota 2 beta' folder"),
            initialdir=self.path_var.get().strip() or None,
            mustexist=True,
        )
        if not selected:
            return
        self.path_var.set(selected)
        self.path_source.configure(fg=BLUE)
        self._set_translated_text(self.path_source, "Selected manually")
        self._save_preferences()
        self._refresh_status()

    def _refresh_status(self) -> None:
        path = self.path_var.get().strip() or None
        language = self._selected_language()

        def operation() -> dict:
            return engine.get_status(path, language)

        self._run_worker(
            "status",
            operation,
            self._tr("Checking installed Dota build and generated overrides…"),
        )

    def _build(self) -> None:
        categories = self._selected_categories()
        if not categories:
            messagebox.showwarning(
                self._tr("Choose a category"),
                self._tr("Select at least one replacement category before building."),
                parent=self.root,
            )
            return
        path = self.path_var.get().strip() or None
        language = self._selected_language()
        self._save_preferences()
        options = engine.BuildOptions(
            dota=path,
            language=language,
            enabled_categories=frozenset(categories),
        )

        def operation() -> tuple[engine.BuildResult, Optional[dict], Optional[Exception]]:
            result = engine.build_cosmetics(
                options,
                progress=lambda message: self.events.put(("log", message, False)),
                progress_update=lambda percent, message: self.events.put(
                    ("progress", percent, message)
                ),
                warning=lambda message: self.events.put(("log", message, True)),
            )
            status, error = try_get_status(result.dota, language)
            return result, status, error

        self._run_worker(
            "build",
            operation,
            self._tr(
                "Building model and effect overrides for the installed Dota build…"
            ),
        )

    def _clean(self) -> None:
        if not messagebox.askyesno(
            self._tr("Remove generated overrides?"),
            self._tr(
                "Only files listed in this tool's ownership marker will be removed. Untracked files are "
                "preserved. The Steam launch option is not changed."
            ),
            icon="warning",
            parent=self.root,
        ):
            return
        path = self.path_var.get().strip() or None
        language = self._selected_language()

        def operation() -> tuple[engine.CleanResult, Optional[dict], Optional[Exception]]:
            result = engine.clean_cosmetics(
                path,
                language,
                progress=lambda message: self.events.put(("log", message, False)),
            )
            status, error = try_get_status(result.dota, language)
            return result, status, error

        self._run_worker(
            "clean",
            operation,
            self._tr("Removing owned cosmetic overrides…"),
        )

    def _run_worker(self, kind: str, operation: Callable[[], object], message: str) -> None:
        if self.busy:
            return
        self._set_busy(True, message)

        def worker() -> None:
            try:
                result = operation()
            except Exception as exc:  # marshalled to the Tk thread below
                self.events.put(("error", kind, exc))
            else:
                self.events.put(("progress", 95, self._tr("Finalizing operation")))
                self.events.put(("success", kind, result))

        threading.Thread(target=worker, name=f"disabler-{kind}", daemon=True).start()

    def _drain_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                try:
                    if event[0] == "log":
                        self._append_log(str(event[1]), error=bool(event[2]))
                    elif event[0] == "progress":
                        self._set_progress(float(event[1]), str(event[2]))
                    elif event[0] == "success":
                        self._set_progress(100, self._tr("Operation complete"))
                        self._worker_succeeded(event[1], event[2])
                    elif event[0] == "error":
                        self._worker_failed(event[1], event[2])
                except Exception as exc:
                    self._set_busy(False)
                    self.header_status.configure(fg=RED, bg="#321c22")
                    self._set_translated_text(
                        self.header_status,
                        "UI ERROR",
                        prefix="  ",
                        suffix="  ",
                    )
                    self.status_detail.configure(fg=RED)
                    self._set_literal_text(self.status_detail, exc)
                    self._append_log(
                        self._tr("Could not update the dashboard: {error}").format(
                            error=exc
                        ),
                        error=True,
                    )
        except queue.Empty:
            pass
        finally:
            try:
                if self.root.winfo_exists():
                    self.root.after(80, self._drain_events)
            except tk.TclError:
                pass

    def _worker_succeeded(self, kind: str, result: object) -> None:
        self._set_busy(False)
        if kind == "detect":
            dota, status, status_error = result  # type: ignore[misc]
            self.path_var.set(str(dota))
            self.path_source.configure(fg=GREEN)
            self._set_translated_text(self.path_source, "Detected automatically")
            self._save_preferences()
            self._append_log(
                self._tr("Found Dota 2 at {path}").format(path=dota)
            )
            if status_error is not None:
                self._show_status_error(
                    status_error,
                    "Dota was found, but the existing override folder could not be validated",
                )
            elif status is not None:
                self._apply_status(status)
        elif kind == "status":
            self._apply_status(result)  # type: ignore[arg-type]
            self.path_source.configure(fg=GREEN)
            self._set_translated_text(self.path_source, "Folder verified")
            self._save_preferences()
            self._append_log(self._tr("Patch status refreshed."))
        elif kind == "build":
            self.last_result, status, status_error = result  # type: ignore[misc]
            self._set_result_actions_visible(True)
            self.path_var.set(str(self.last_result.dota))
            self._append_log(
                self._tr(
                    "Build complete: {count} model/effect resource overrides."
                ).format(count=self.last_result.copied)
            )
            if status_error is not None:
                self._show_status_error(
                    status_error,
                    "Build completed, but status refresh failed",
                )
            elif status is not None:
                self._apply_status(status)
        elif kind == "clean":
            clean_result, status, status_error = result  # type: ignore[misc]
            self._set_result_actions_visible(False)
            self._append_log(
                self._tr("Cleanup complete: {count} owned files removed.").format(
                    count=clean_result.removed
                )
            )
            self._append_log(
                self._tr(
                    "Remove '-language {language}' from Steam launch options if you no longer need it."
                ).format(language=self._selected_language())
            )
            if status_error is not None:
                self._show_status_error(
                    status_error,
                    "Cleanup completed, but status refresh failed",
                )
            elif status is not None:
                self._apply_status(status)

    def _worker_failed(self, kind: str, error: Exception) -> None:
        self._set_busy(False)
        if kind in {"detect", "status"}:
            self.last_status = None
            self._set_translated_text(self.build_button, "Build Overrides")
        self.header_status.configure(fg=RED, bg="#321c22")
        self._set_translated_text(
            self.header_status,
            "ACTION NEEDED",
            prefix="  ",
            suffix="  ",
        )
        self.status_detail.configure(fg=RED)
        self._set_literal_text(self.status_detail, error)
        self._append_log(
            self._tr("{operation} failed: {error}").format(
                operation=self._tr_marked(
                    OPERATION_NAMES.get(kind, kind.capitalize())
                ),
                error=error,
            ),
            error=True,
        )
        if kind == "detect":
            self.path_source.configure(fg=RED)
            self._set_translated_text(
                self.path_source,
                "Not found · choose Browse",
            )
            return
        if isinstance(error, engine.UnsafeOutputError):
            title = self._tr("Unowned file protection")
            detail = self._tr(
                "{error}\n\nThe tool stopped without adopting or force-cleaning the folder. "
                "Review the path or close this window."
            ).format(error=error)
        else:
            title = self._tr("{operation} failed").format(
                operation=self._tr_marked(
                    OPERATION_NAMES.get(kind, kind.capitalize())
                )
            )
            detail = str(error)
        messagebox.showerror(title, detail, parent=self.root)

    def _show_status_error(self, error: Exception, context_message: str) -> None:
        self.last_status = None
        self._set_translated_text(self.build_button, "Build Overrides")
        self.header_status.configure(fg=RED, bg="#321c22")
        self._set_translated_text(
            self.header_status,
            "ACTION NEEDED",
            prefix="  ",
            suffix="  ",
        )
        self.status_detail.configure(fg=RED)

        def render_detail() -> str:
            return self._tr("{context}: {error}").format(
                context=self._tr(context_message),
                error=error,
            )

        self._set_bound_text(self.status_detail, render_detail)
        self.path_source.configure(fg=GREEN)
        self._set_translated_text(
            self.path_source,
            "Dota folder verified",
        )
        self._append_log(render_detail(), error=True)

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self.busy = busy
        for control in self.busy_controls:
            try:
                if isinstance(control, ttk.Combobox):
                    control.configure(state="disabled" if busy else "readonly")
                else:
                    control.configure(state="disabled" if busy else "normal")
            except tk.TclError:
                pass
        for toggle in self.toggle_controls:
            toggle.set_enabled(not busy)
        if busy:
            self.activity_title.configure(fg=TEXT_SOFT)
            self._set_literal_text(self.activity_title, message)
            self.progress.configure(value=0.0)
            self.progress_label.configure(text="0.0%")
        else:
            self.activity_title.configure(fg=TEXT_SOFT)
            self._set_translated_text(self.activity_title, "Current activity")

    def _set_progress(self, percent: float, message: str = "") -> None:
        percent = max(0.0, min(100.0, percent))
        current = float(self.progress["value"])
        if self.busy and percent < current:
            return
        self.progress.configure(value=percent)
        label = "100%" if percent >= 100.0 else f"{percent:.1f}%"
        self.progress_label.configure(text=label)
        if self.busy and message:
            self.activity_title.configure(fg=TEXT_SOFT)
            self._set_literal_text(self.activity_title, message)

    def _apply_status(self, result: dict) -> None:
        self.last_status = result
        view = status_presentation(
            result,
            self._selected_categories(),
            self._tr,
        )
        language = result.get("language")
        detail = view["detail"]
        if isinstance(language, str) and language in engine.RECOGNIZED_LANGUAGES:
            detail += f"  ·  -language {language}"
        self.header_status.configure(fg=view["color"], bg=SURFACE_ALT)
        self._set_literal_text(self.header_status, f"  {view['badge']}  ")
        self.status_detail.configure(fg=view["color"])
        self._set_literal_text(self.status_detail, detail)
        self._set_literal_text(self.build_button, view["action"])
        self._set_result_actions_visible(result.get("status") != "not_built")
        self.current_version.configure(text=engine.dota_version_label(result.get("current_dota_version")))
        self.recorded_version.configure(text=engine.dota_version_label(result.get("recorded_dota_version")))

    def _set_result_actions_visible(self, visible: bool) -> None:
        if visible:
            self.result_actions.grid()
        else:
            self.result_actions.grid_remove()

    def _append_log(self, message: str, *, error: bool = False) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        lines = str(message).splitlines() or [""]
        self.log.configure(state="normal")
        self.log.tag_configure("error", foreground=RED)
        for line in lines:
            tag = "error" if error else ""
            self.log.insert("end", f"[{timestamp}] {line}\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _open_report(self) -> None:
        path = (
            self.last_result.report
            if self.last_result
            else engine.application_root() / ".work/model-plan.json"
        )
        self._open_path(path)

    def _open_output(self) -> None:
        if self.path_var.get().strip():
            path = Path(self.path_var.get().strip()) / "game" / f"dota_{self._selected_language()}"
        elif self.last_result:
            path = self.last_result.output_root
        else:
            messagebox.showinfo(
                self._tr("Not available yet"),
                self._tr("Choose a Dota 2 folder or build overrides first."),
                parent=self.root,
            )
            return
        self._open_path(path)

    def _open_path(self, path: Path) -> None:
        if not path.exists():
            messagebox.showinfo(
                self._tr("Not available yet"),
                self._tr("This path does not exist yet:\n{path}").format(path=path),
                parent=self.root,
            )
            return
        try:
            if os.name == "nt":
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except OSError as exc:
            messagebox.showerror(
                self._tr("Could not open path"),
                str(exc),
                parent=self.root,
            )

    def _copy_launch_option(self) -> None:
        option = f"-language {self._selected_language()}"
        self.root.clipboard_clear()
        self.root.clipboard_append(option)
        self._append_log(
            self._tr("Copied Steam launch option: {option}").format(option=option)
        )

    def _on_close(self) -> None:
        if self.busy:
            messagebox.showwarning(
                self._tr("Operation in progress"),
                self._tr(
                    "Keep this window open until the current operation finishes "
                    "so deployment cannot be interrupted."
                ),
                parent=self.root,
            )
            return
        self._save_preferences()
        self.root.destroy()


def _assert_localized_layout(app: DisablerApp) -> None:
    """Reject translated controls whose requested text width was compressed."""
    missing_fonts = set(app.fonts) - set(tkfont.names(app.root))
    if missing_fonts:
        raise RuntimeError(
            "GUI named fonts were released too early: "
            f"{', '.join(sorted(missing_fonts))}"
        )
    for name, container in (
        ("header", app.header),
        ("controls column", app.controls_column),
    ):
        requested_width = container.winfo_reqwidth()
        allocated_width = container.winfo_width()
        if requested_width > allocated_width + 2:
            raise RuntimeError(
                f"GUI {name} overflows for locale {app.translator.locale}: "
                f"requested {requested_width}px, allocated {allocated_width}px"
            )
    clipped: list[str] = []
    pending = list(app.root.winfo_children())
    while pending:
        widget = pending.pop()
        pending.extend(widget.winfo_children())
        if widget.winfo_class() not in {"Button", "Label", "TCombobox"}:
            continue
        if not widget.winfo_ismapped():
            continue
        if widget.winfo_reqwidth() <= widget.winfo_width() + 2:
            continue
        try:
            label = str(widget.cget("text"))
        except tk.TclError:
            label = widget.winfo_class()
        clipped.append(label or widget.winfo_class())
    if clipped:
        raise RuntimeError(
            "GUI text is clipped for locale "
            f"{app.translator.locale}: {', '.join(clipped)}"
        )


def _assert_live_locale_switches(app: DisablerApp) -> None:
    """Exercise every packaged locale pair without mutating saved settings."""
    preferences = (UI_LOCALE_AUTO, *available_ui_locales())
    path = app.path_var.get()
    compatibility_language = app._selected_language()
    categories = app._selected_categories()
    log_contents = app.log.get("1.0", "end-1c")
    status = {
        "status": "current",
        "enabled_categories": sorted(categories),
        "language": compatibility_language,
        "dota_path": path,
    }
    app._apply_status(status)

    for source_preference in preferences:
        for target_preference in preferences:
            app._apply_ui_locale(source_preference, persist=False)
            app._apply_ui_locale(target_preference, persist=False)
            app.root.update_idletasks()
            expected_translator = load_ui_translator(target_preference)
            expected_status = status_presentation(
                status,
                categories,
                expected_translator.gettext,
            )
            if app.ui_locale != target_preference:
                raise RuntimeError("GUI did not retain the selected locale preference.")
            if app.translator.locale != expected_translator.locale:
                raise RuntimeError("GUI did not activate the selected locale instantly.")
            if str(app.refresh_button.cget("text")) != expected_translator.gettext(
                "Refresh"
            ):
                raise RuntimeError("GUI static controls were not retranslated.")
            if expected_status["badge"] not in str(app.header_status.cget("text")):
                raise RuntimeError("GUI status presentation was not retranslated.")
            if str(app.build_button.cget("text")) != expected_status["action"]:
                raise RuntimeError("GUI status action was not retranslated.")
            if (
                app.path_var.get() != path
                or app._selected_language() != compatibility_language
                or app._selected_categories() != categories
            ):
                raise RuntimeError("GUI state changed while switching locale.")
            if app.log.get("1.0", "end-1c") != log_contents:
                raise RuntimeError("GUI rewrote historical log entries during translation.")
            if not app.result_actions.grid_info():
                raise RuntimeError("GUI result actions disappeared during translation.")
            if app.ui_locale_var.get() not in app.ui_locale_choices:
                raise RuntimeError("GUI locale selector lost its translated selection.")
            _assert_localized_layout(app)

    blocked_target = next(
        preference for preference in preferences if preference != app.ui_locale
    )
    active_preference = app.ui_locale
    app._set_busy(True, "Smoke test operation")
    if app._apply_ui_locale(blocked_target, persist=False):
        raise RuntimeError("GUI changed locale during an active operation.")
    if app.ui_locale != active_preference:
        raise RuntimeError("GUI locale changed despite the busy-state guard.")
    app._set_busy(False)

    callback_target = next(
        preference for preference in preferences if preference != app.ui_locale
    )
    callback_label = next(
        label
        for label, preference in app.ui_locale_choices.items()
        if preference == callback_target
    )
    saved_preferences: list[tuple[str, str]] = []
    original_save_preferences = app._save_preferences
    try:
        app._save_preferences = lambda: saved_preferences.append(
            (app.ui_locale, app._selected_language())
        )
        app.ui_locale_var.set(callback_label)
        app._ui_locale_changed()
    finally:
        app._save_preferences = original_save_preferences
    if saved_preferences != [(callback_target, compatibility_language)]:
        raise RuntimeError("GUI locale callback did not persist the switched state.")
    if app.translator.locale != load_ui_translator(callback_target).locale:
        raise RuntimeError("GUI locale callback did not update the interface immediately.")

    rollback_target = next(
        preference for preference in preferences if preference != app.ui_locale
    )
    rollback_preference = app.ui_locale
    rollback_translator = app.translator.locale
    rollback_refresh_text = str(app.refresh_button.cget("text"))
    original_save_preferences = app._save_preferences
    try:
        def fail_save_preferences() -> None:
            raise RuntimeError("Expected smoke-test settings failure")

        app._save_preferences = fail_save_preferences
        try:
            app._apply_ui_locale(rollback_target)
        except RuntimeError as exc:
            if str(exc) != "Expected smoke-test settings failure":
                raise
        else:
            raise RuntimeError("GUI locale switch ignored a settings failure.")
    finally:
        app._save_preferences = original_save_preferences
    if (
        app.ui_locale != rollback_preference
        or app.translator.locale != rollback_translator
        or str(app.refresh_button.cget("text")) != rollback_refresh_text
    ):
        raise RuntimeError("GUI locale switch did not roll back atomically.")


def run_gui(*, smoke_test: bool = False) -> int:
    smoke_locale = None
    if smoke_test:
        configured_smoke_locale = os.environ.get(
            "DOTA2_COSMETIC_DISABLER_TEST_UI_LOCALE"
        )
        if configured_smoke_locale:
            smoke_locale = normalize_ui_locale(configured_smoke_locale)
            if smoke_locale not in available_ui_locales():
                raise RuntimeError(
                    f"GUI smoke-test locale is not packaged: {smoke_locale}"
                )
    root = tk.Tk()
    if smoke_test:
        root.withdraw()
    app = DisablerApp(
        root,
        start_detection=not smoke_test,
        ui_locale_override=smoke_locale,
    )
    if smoke_test:
        # Exercise the supported minimum width. Some hosted desktops clamp an
        # off-screen 1280px window to 1024px even when a larger geometry is
        # requested, which previously exposed real pane compression here.
        root.geometry("1024x700+10000+10000")
        root.deiconify()
        root.update()
        if smoke_locale is not None and app.translator.locale != smoke_locale:
            raise RuntimeError(
                "GUI did not activate the requested smoke-test locale: "
                f"{smoke_locale}"
            )
        catalog_sentinels = {
            "ru": "Обновить",
            "zh-Hans": "刷新",
            "zh-Hant": "重新整理",
        }
        for locale_code, expected_refresh in catalog_sentinels.items():
            catalog = load_ui_translator(locale_code)
            if (
                catalog.locale != locale_code
                or catalog.gettext("Refresh") != expected_refresh
            ):
                raise RuntimeError(
                    f"GUI translation catalog is unavailable: {locale_code}"
                )
        required = (
            app.path_entry,
            app.language_combo,
            app.ui_locale_combo,
            app.action_summary,
            app.build_button,
            app.clean_button,
            app.log,
        )
        if not all(widget.winfo_exists() for widget in required):
            raise RuntimeError("GUI smoke test could not construct the essential controls.")
        if root.grid_rowconfigure(1)["weight"] != 1:
            raise RuntimeError("GUI workspace does not absorb available window height.")
        if (
            app.ui_locale_frame.grid_info().get("column") != 2
            or app.header_status.grid_info().get("column") != 3
            or app.refresh_button.grid_info().get("column") != 4
        ):
            raise RuntimeError("GUI language, status, and refresh controls are out of order.")
        header_control_centers = (
            app.ui_locale_combo.winfo_rooty() + app.ui_locale_combo.winfo_height() // 2,
            app.header_status.winfo_rooty() + app.header_status.winfo_height() // 2,
            app.refresh_button.winfo_rooty() + app.refresh_button.winfo_height() // 2,
        )
        if max(header_control_centers) - min(header_control_centers) > 2:
            raise RuntimeError("GUI header controls do not share a vertical centerline.")
        locale_values = root.tk.splitlist(app.ui_locale_combo.cget("values"))
        if app.ui_locale_var.get() not in locale_values:
            raise RuntimeError("GUI language selector does not contain its saved selection.")
        expected_locale_codes = {UI_LOCALE_AUTO, *available_ui_locales()}
        if set(app.ui_locale_choices.values()) != expected_locale_codes:
            raise RuntimeError("GUI language selector does not list every shipped catalog.")
        if (
            app.workspace.grid_columnconfigure(0)["weight"] != 5
            or app.workspace.grid_columnconfigure(1)["weight"] != 7
        ):
            raise RuntimeError("GUI workspace columns do not preserve the controls/log split.")
        if (
            app.installation_card.grid_info().get("row") != 0
            or app.features_card.grid_info().get("row") != 1
            or app.action_card.grid_info().get("row") != 2
            or app.activity_card.grid_info().get("column") != 1
        ):
            raise RuntimeError("GUI controls are not stacked beside the activity log.")
        if app.activity_panel.grid_rowconfigure(2)["weight"] != 1:
            raise RuntimeError("GUI log row is not configured to absorb additional height.")
        selected_count = sum(variable.get() for variable in app.category_vars.values())
        summary_text = str(app.action_summary.cget("text"))
        if (selected_count and str(selected_count) not in summary_text) or not summary_text:
            raise RuntimeError("GUI build summary did not reflect the selected categories.")
        if not app.log.grid_info():
            raise RuntimeError("GUI activity log was not persistently displayed.")
        if app.result_actions.grid_info():
            raise RuntimeError("GUI result actions appeared without a usable result.")
        app._set_result_actions_visible(True)
        if not app.result_actions.grid_info():
            raise RuntimeError("GUI result actions could not be revealed contextually.")
        root.update_idletasks()
        _assert_localized_layout(app)
        app._set_busy(True, "Packing files")
        if str(app.ui_locale_combo.cget("state")) != "disabled":
            raise RuntimeError("GUI language selector stayed enabled during an operation.")
        app._set_progress(42.5, "Packing files")
        if str(app.activity_title.cget("text")) != "Packing files":
            raise RuntimeError("GUI activity copy was unexpectedly transformed.")
        if str(app.progress_label.cget("text")) != "42.5%":
            raise RuntimeError("GUI progress label did not preserve granular progress.")
        app._set_busy(False)
        if str(app.ui_locale_combo.cget("state")) != "readonly":
            raise RuntimeError("GUI language selector did not return to readonly state.")
        _assert_live_locale_switches(app)
        root.withdraw()
        root.destroy()
        return 0
    root.mainloop()
    return 0


__all__ = [
    "DisablerApp",
    "FlatButton",
    "ToggleSwitch",
    "desktop_work_area",
    "run_gui",
]
