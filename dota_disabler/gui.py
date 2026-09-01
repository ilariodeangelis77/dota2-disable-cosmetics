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
from tkinter import filedialog, messagebox, ttk
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
    LANGUAGE_LABELS,
    LANGUAGE_LABEL_TO_CODE,
    LANGUAGE_NAMES,
    MUTED,
    RED,
    SETTINGS_FILENAME,
    SETTINGS_FORMAT_VERSION,
    SURFACE,
    SURFACE_ALT,
    SURFACE_HOVER,
    TEXT,
    TEXT_SOFT,
    language_label,
    load_ui_settings,
    resolve_initial_dota,
    save_ui_settings,
    settings_file,
    status_matches_path,
    status_presentation,
    try_get_status,
)


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
            font=("Segoe UI Semibold", 10),
            padx=15 if compact else 20,
            pady=7 if compact else 11,
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
    def __init__(self, root: tk.Tk, *, start_detection: bool = True):
        self.root = root
        self.events: queue.Queue[tuple] = queue.Queue()
        self.busy = False
        self.closing_requested = False
        self.settings = load_ui_settings()
        self.path_var = tk.StringVar(value=self.settings.get("dota_path") or "")
        self.language_var = tk.StringVar(value=language_label(self.settings["language"]))
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

    def _configure_root(self) -> None:
        self.root.title(f"Dota 2 Cosmetic Disabler {engine.VERSION}")
        self.root.configure(bg=BG)
        work_left, work_top, work_width, work_height = desktop_work_area(self.root)
        width = min(1280, max(900, work_width - 32))
        height = min(700, max(560, work_height - 64))
        self.root.geometry(f"{width}x{height}")
        self.root.minsize(min(1024, width), min(620, height))
        self.root.option_add("*Font", ("Segoe UI", 10))
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(
            "Dota.TCombobox",
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
        self.root.grid_rowconfigure(1, weight=0)
        self.root.grid_rowconfigure(2, weight=1)
        self._center_window(width, height, (work_left, work_top, work_width, work_height))

    def _center_window(self, width: int, height: int, work_area: tuple[int, int, int, int]) -> None:
        left, top, work_width, work_height = work_area
        x = left + max(0, (work_width - width) // 2)
        y = top + max(0, (work_height - height) // 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def _build_layout(self) -> None:
        self._build_header()
        content = tk.Frame(self.root, bg=BG)
        content.grid(row=1, column=0, sticky="ew", padx=24, pady=(8, 10))
        content.grid_columnconfigure(0, weight=5, uniform="main")
        content.grid_columnconfigure(1, weight=8, uniform="main")
        content.grid_rowconfigure(0, weight=0)
        self._build_installation_panel(content)
        self._build_features_panel(content)
        self._build_activity_panel()

    def _build_header(self) -> None:
        header = tk.Frame(self.root, bg=BG, height=82)
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 4))
        header.grid_columnconfigure(1, weight=1)

        logo = tk.Canvas(header, width=46, height=46, bg=BG, bd=0, highlightthickness=0)
        logo.grid(row=0, column=0, rowspan=2, sticky="w", padx=(0, 14))
        _rounded_rectangle(logo, 1, 1, 45, 45, 11, fill=ACCENT, outline=ACCENT)
        logo.create_text(23, 23, text="D2", fill=TEXT, font=("Segoe UI Semibold", 14))

        tk.Label(
            header,
            text="COSMETIC DISABLER",
            bg=BG,
            fg=TEXT,
            font=("Segoe UI Semibold", 17),
        ).grid(row=0, column=1, sticky="sw")
        tk.Label(
            header,
            text=f"Local model & effect overrides  ·  v{engine.VERSION}  ·  no injection",
            bg=BG,
            fg=MUTED,
            font=("Segoe UI", 9),
        ).grid(row=1, column=1, sticky="nw", pady=(2, 0))

        self.header_status = tk.Label(
            header,
            text="  DETECTING  ",
            bg=SURFACE_ALT,
            fg=TEXT_SOFT,
            font=("Segoe UI Semibold", 9),
            padx=9,
            pady=6,
        )
        self.header_status.grid(row=0, column=2, rowspan=2, sticky="e", padx=(12, 0))
        self.refresh_button = FlatButton(
            header,
            text="Refresh",
            command=self._refresh_status,
            compact=True,
        )
        self.refresh_button.grid(row=0, column=3, rowspan=2, sticky="e", padx=(10, 0))
        self.busy_controls.append(self.refresh_button)

    def _build_installation_panel(self, parent: tk.Frame) -> None:
        border, panel = _card(parent)
        border.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        panel.grid_columnconfigure(0, weight=1)

        tk.Label(panel, text="DOTA 2 INSTALLATION", bg=SURFACE, fg=MUTED, font=("Segoe UI Semibold", 9)).grid(
            row=0, column=0, sticky="w", padx=20, pady=(18, 4)
        )
        tk.Label(panel, text="Game folder", bg=SURFACE, fg=TEXT, font=("Segoe UI Semibold", 14)).grid(
            row=1, column=0, sticky="w", padx=20
        )

        path_frame = tk.Frame(panel, bg=SURFACE_ALT, highlightbackground=BORDER, highlightthickness=1)
        path_frame.grid(row=2, column=0, sticky="ew", padx=20, pady=(12, 8))
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
            font=("Segoe UI", 10),
        )
        self.path_entry.grid(row=0, column=0, sticky="ew", padx=12, pady=11)
        self.path_entry.bind("<Return>", lambda _event: self._refresh_status())
        self.path_entry.bind("<KeyRelease>", self._path_edited)
        self.busy_controls.append(self.path_entry)

        path_actions = tk.Frame(panel, bg=SURFACE)
        path_actions.grid(row=3, column=0, sticky="ew", padx=20)
        self.browse_button = FlatButton(path_actions, text="Browse", command=self._browse, compact=True)
        self.browse_button.pack(side="left")
        self.detect_button = FlatButton(path_actions, text="Auto-detect", command=self._auto_detect, compact=True)
        self.detect_button.pack(side="left", padx=(8, 0))
        self.path_source = tk.Label(path_actions, text="Waiting for detection", bg=SURFACE, fg=MUTED, font=("Segoe UI", 9))
        self.path_source.pack(side="right")
        self.busy_controls.extend((self.browse_button, self.detect_button))

        separator = tk.Frame(panel, bg=BORDER_SOFT, height=1)
        separator.grid(row=4, column=0, sticky="ew", padx=20, pady=18)

        versions = tk.Frame(panel, bg=SURFACE)
        versions.grid(row=5, column=0, sticky="ew", padx=20)
        versions.grid_columnconfigure(1, weight=1)
        tk.Label(versions, text="Installed Dota build", bg=SURFACE, fg=MUTED, font=("Segoe UI", 9)).grid(
            row=0, column=0, sticky="w"
        )
        self.current_version = tk.Label(
            versions, text="—", bg=SURFACE, fg=TEXT, font=("Segoe UI Semibold", 10), anchor="e"
        )
        self.current_version.grid(row=0, column=1, sticky="e")
        tk.Label(versions, text="Overrides built from", bg=SURFACE, fg=MUTED, font=("Segoe UI", 9)).grid(
            row=1, column=0, sticky="w", pady=(10, 0)
        )
        self.recorded_version = tk.Label(
            versions, text="—", bg=SURFACE, fg=TEXT_SOFT, font=("Segoe UI Semibold", 10), anchor="e"
        )
        self.recorded_version.grid(row=1, column=1, sticky="e", pady=(10, 0))
        tk.Label(versions, text="Mount language · English UI", bg=SURFACE, fg=MUTED, font=("Segoe UI", 9)).grid(
            row=2, column=0, sticky="w", pady=(10, 0)
        )
        self.language_combo = ttk.Combobox(
            versions,
            textvariable=self.language_var,
            values=LANGUAGE_LABELS,
            state="readonly",
            width=24,
            style="Dota.TCombobox",
            justify="right",
        )
        self.language_combo.grid(row=2, column=1, sticky="e", pady=(10, 0))
        self.language_combo.bind("<<ComboboxSelected>>", self._language_changed)
        self.busy_controls.append(self.language_combo)

        self.status_detail = tk.Label(
            panel,
            text="Looking for your Steam library…",
            bg=SURFACE_ALT,
            fg=TEXT_SOFT,
            anchor="w",
            justify="left",
            padx=12,
            pady=10,
            font=("Segoe UI", 9),
        )
        self.status_detail.grid(row=6, column=0, sticky="ew", padx=20, pady=(18, 12))

        action_row = tk.Frame(panel, bg=SURFACE)
        action_row.grid(row=7, column=0, sticky="ew", padx=20, pady=(0, 18))
        action_row.grid_columnconfigure(0, weight=1)
        self.build_button = FlatButton(action_row, text="Build Overrides", command=self._build, primary=True)
        self.build_button.grid(row=0, column=0, sticky="ew")
        self.clean_button = FlatButton(action_row, text="Remove Overrides", command=self._clean)
        self.clean_button.grid(row=0, column=1, padx=(9, 0))
        self.busy_controls.extend((self.build_button, self.clean_button))

    def _build_features_panel(self, parent: tk.Frame) -> None:
        border, panel = _card(parent)
        border.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_columnconfigure(1, weight=1)

        tk.Label(panel, text="COSMETICS TO DISABLE", bg=SURFACE, fg=MUTED, font=("Segoe UI Semibold", 9)).grid(
            row=0, column=0, sticky="w", padx=20, pady=(18, 4)
        )
        presets = tk.Frame(panel, bg=SURFACE)
        presets.grid(row=0, column=1, sticky="e", padx=20, pady=(12, 0))
        select_all = FlatButton(
            presets,
            text="Select all",
            command=lambda: self._set_all_categories(True),
            compact=True,
        )
        select_all.pack(side="left")
        clear = FlatButton(
            presets,
            text="Clear",
            command=lambda: self._set_all_categories(False),
            compact=True,
        )
        clear.pack(side="left", padx=(6, 0))
        self.busy_controls.extend((select_all, clear))
        tk.Label(
            panel,
            text="Restore selected categories to their defaults on the next build",
            bg=SURFACE,
            fg=TEXT,
            font=("Segoe UI Semibold", 14),
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=20)

        supported = tk.Frame(panel, bg=SURFACE)
        supported.grid(row=2, column=0, columnspan=2, sticky="ew", padx=14, pady=(10, 8))
        feature_columns = 2
        for column in range(feature_columns):
            supported.grid_columnconfigure(column, weight=1, uniform="feature")
        for index, feature in enumerate(FEATURES):
            row, column = divmod(index, feature_columns)
            supported.grid_rowconfigure(row, weight=1, uniform="feature")
            self._build_feature_card(supported, feature, row, column)

        tk.Label(
            panel,
            text=(
                "Categories follow schema rules—not cosmetic names. Sounds, icons, animations, "
                "couriers, and world cosmetics remain unchanged."
            ),
            bg=SURFACE,
            fg=MUTED,
            font=("Segoe UI", 8),
            anchor="w",
            justify="left",
            wraplength=520,
        ).grid(row=3, column=0, columnspan=2, sticky="ew", padx=20, pady=(5, 14))

    def _build_feature_card(self, parent: tk.Frame, feature: dict, row: int, column: int) -> None:
        card = tk.Frame(parent, bg=SURFACE_ALT, highlightbackground=BORDER_SOFT, highlightthickness=1)
        card.grid(row=row, column=column, sticky="nsew", padx=5, pady=5)
        card.grid_columnconfigure(0, weight=1)
        tk.Label(
            card,
            text=feature["tag"],
            bg=SURFACE_ALT,
            fg=AMBER if feature["tag"] == "EXPERIMENTAL" else GREEN,
            font=("Segoe UI Semibold", 7),
        ).grid(row=0, column=0, sticky="w", padx=11, pady=(8, 2))
        toggle = ToggleSwitch(
            card,
            self.category_vars[feature["key"]],
            self._category_changed,
        )
        toggle.grid(row=0, column=1, sticky="e", padx=9, pady=(5, 0))
        self.toggle_controls.append(toggle)
        tk.Label(
            card,
            text=feature["title"],
            bg=SURFACE_ALT,
            fg=TEXT,
            font=("Segoe UI Semibold", 10),
            justify="left",
            wraplength=230,
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=12, pady=(1, 1))
        tk.Label(
            card,
            text=feature["description"],
            bg=SURFACE_ALT,
            fg=TEXT_SOFT,
            justify="left",
            anchor="nw",
            wraplength=230,
            font=("Segoe UI", 9),
        ).grid(row=2, column=0, columnspan=2, sticky="ew", padx=12, pady=(1, 9))

    def _build_activity_panel(self) -> None:
        border, panel = _card(self.root)
        border.grid(row=2, column=0, sticky="nsew", padx=24, pady=(0, 18))
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(2, weight=1)
        self.activity_panel = panel

        activity_header = tk.Frame(panel, bg=SURFACE)
        activity_header.grid(row=0, column=0, sticky="ew", padx=16, pady=(10, 5))
        activity_header.grid_columnconfigure(0, weight=1)
        self.activity_title = tk.Label(
            activity_header, text="ACTIVITY", bg=SURFACE, fg=MUTED, font=("Segoe UI Semibold", 9)
        )
        self.activity_title.grid(row=0, column=0, sticky="w")
        self.open_report_button = FlatButton(
            activity_header, text="Open report", command=self._open_report, compact=True
        )
        self.progress_label = tk.Label(
            activity_header,
            text="",
            bg=SURFACE,
            fg=TEXT_SOFT,
            font=("Segoe UI Semibold", 9),
        )
        self.progress_label.grid(row=0, column=1, padx=(8, 4))
        self.open_report_button.grid(row=0, column=2, padx=(8, 0))
        self.open_output_button = FlatButton(
            activity_header, text="Open output", command=self._open_output, compact=True
        )
        self.open_output_button.grid(row=0, column=3, padx=(8, 0))
        self.copy_launch_button = FlatButton(
            activity_header, text="Copy launch option", command=self._copy_launch_option, compact=True
        )
        self.copy_launch_button.grid(row=0, column=4, padx=(8, 0))

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
            font=("Cascadia Mono", 8),
        )
        self.log.grid(row=2, column=0, sticky="nsew", padx=16, pady=(6, 7))
        tk.Label(
            panel,
            text=(
                "Unofficial overrides  ·  Steam › Dota 2 › Properties › Launch Options  ·  "
                "Test in Demo Hero first  ·  No anti-cheat guarantee"
            ),
            bg=SURFACE,
            fg=MUTED,
            font=("Segoe UI", 8),
            anchor="w",
        ).grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 9))
        self._append_log("Ready. Dota will be detected automatically.")

    def _selected_categories(self) -> set[str]:
        return {
            category
            for feature in FEATURES
            if self.category_vars[feature["key"]].get()
            for category in feature["categories"]
        }

    def _selected_language(self) -> str:
        return LANGUAGE_LABEL_TO_CODE.get(self.language_var.get(), engine.DEFAULT_LANGUAGE)

    def _category_changed(self) -> None:
        self._save_preferences()
        selected = sum(variable.get() for variable in self.category_vars.values())
        self._append_log(f"Selected {selected} of {len(FEATURES)} supported replacement categories.")
        if self.last_status is not None and status_matches_path(self.last_status, self.path_var.get()):
            self._apply_status(self.last_status)

    def _language_changed(self, _event=None) -> None:
        if self.busy:
            return
        language = self._selected_language()
        self.last_status = None
        self._save_preferences()
        self.header_status.configure(text="  CHECKING MOUNT  ", fg=BLUE, bg=SURFACE_ALT)
        self.status_detail.configure(
            text=f"Checking the {language} compatibility mount; rebuild to apply a new selection",
            fg=BLUE,
        )
        self._append_log(
            f"Compatibility language changed to {language}. The interface will remain English after building."
        )
        if self.path_var.get().strip():
            self._refresh_status()

    def _path_edited(self, _event=None) -> None:
        self.last_status = None
        self.header_status.configure(text="  CHECK PATH  ", fg=AMBER, bg=SURFACE_ALT)
        self.status_detail.configure(text="Folder changed; refresh to validate it", fg=AMBER)
        self.path_source.configure(text="Edited · press Enter or Refresh", fg=AMBER)

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
                language=self._selected_language(),
            )
        except OSError as exc:
            self._append_log(f"Could not save UI settings: {exc}", error=True)

    def _detect_on_startup(self) -> None:
        saved = self.settings.get("dota_path")
        language = self._selected_language()

        def operation() -> tuple[Path, Optional[dict], Optional[Exception]]:
            dota = resolve_initial_dota(saved)
            status, error = try_get_status(dota, language)
            return dota, status, error

        self._run_worker("detect", operation, "Detecting Dota 2 installation…")

    def _auto_detect(self) -> None:
        language = self._selected_language()

        def operation() -> tuple[Path, Optional[dict], Optional[Exception]]:
            dota = engine.find_dota_install(None)
            status, error = try_get_status(dota, language)
            return dota, status, error

        self._run_worker("detect", operation, "Searching Steam libraries…")

    def _browse(self) -> None:
        selected = filedialog.askdirectory(
            parent=self.root,
            title="Choose the 'dota 2 beta' folder",
            initialdir=self.path_var.get().strip() or None,
            mustexist=True,
        )
        if not selected:
            return
        self.path_var.set(selected)
        self.path_source.configure(text="Selected manually", fg=BLUE)
        self._save_preferences()
        self._refresh_status()

    def _refresh_status(self) -> None:
        path = self.path_var.get().strip() or None
        language = self._selected_language()

        def operation() -> dict:
            return engine.get_status(path, language)

        self._run_worker("status", operation, "Checking installed Dota build and generated overrides…")

    def _build(self) -> None:
        categories = self._selected_categories()
        if not categories:
            messagebox.showwarning(
                "Choose a category",
                "Select at least one supported model category before building.",
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

        self._run_worker("build", operation, "Building model and effect overrides for the installed Dota build…")

    def _clean(self) -> None:
        if not messagebox.askyesno(
            "Remove generated overrides?",
            (
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

        self._run_worker("clean", operation, "Removing owned cosmetic overrides…")

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
                self.events.put(("progress", 95, "Finalizing operation"))
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
                        self._set_progress(100, "Operation complete")
                        self._worker_succeeded(event[1], event[2])
                    elif event[0] == "error":
                        self._worker_failed(event[1], event[2])
                except Exception as exc:
                    self._set_busy(False)
                    self.header_status.configure(text="  UI ERROR  ", fg=RED, bg="#321c22")
                    self.status_detail.configure(text=str(exc), fg=RED)
                    self._append_log(f"Could not update the dashboard: {exc}", error=True)
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
            self.path_source.configure(text="Detected automatically", fg=GREEN)
            self._save_preferences()
            self._append_log(f"Found Dota 2 at {dota}")
            if status_error is not None:
                self._show_status_error(
                    status_error,
                    "Dota was found, but the existing override folder could not be validated",
                )
            elif status is not None:
                self._apply_status(status)
        elif kind == "status":
            self._apply_status(result)  # type: ignore[arg-type]
            self.path_source.configure(text="Folder verified", fg=GREEN)
            self._save_preferences()
            self._append_log("Patch status refreshed.")
        elif kind == "build":
            self.last_result, status, status_error = result  # type: ignore[misc]
            self.path_var.set(str(self.last_result.dota))
            self._append_log(f"Build complete: {self.last_result.copied} model/effect resource overrides.")
            if status_error is not None:
                self._show_status_error(status_error, "Build completed, but status refresh failed")
            elif status is not None:
                self._apply_status(status)
        elif kind == "clean":
            clean_result, status, status_error = result  # type: ignore[misc]
            self._append_log(f"Cleanup complete: {clean_result.removed} owned files removed.")
            self._append_log(
                f"Remove '-language {self._selected_language()}' from Steam launch options if you no longer need it."
            )
            if status_error is not None:
                self._show_status_error(status_error, "Cleanup completed, but status refresh failed")
            elif status is not None:
                self._apply_status(status)

    def _worker_failed(self, kind: str, error: Exception) -> None:
        self._set_busy(False)
        if kind in {"detect", "status"}:
            self.last_status = None
        self.header_status.configure(text="  ACTION NEEDED  ", fg=RED, bg="#321c22")
        self.status_detail.configure(text=str(error), fg=RED)
        self._append_log(f"{kind.capitalize()} failed: {error}", error=True)
        if kind == "detect":
            self.path_source.configure(text="Not found · choose Browse", fg=RED)
            return
        if isinstance(error, engine.UnsafeOutputError):
            title = "Unowned file protection"
            detail = (
                f"{error}\n\nThe tool stopped without adopting or force-cleaning the folder. "
                "Review the path or close this window."
            )
        else:
            title = f"{kind.capitalize()} failed"
            detail = str(error)
        messagebox.showerror(title, detail, parent=self.root)

    def _show_status_error(self, error: Exception, context: str) -> None:
        self.last_status = None
        self.header_status.configure(text="  ACTION NEEDED  ", fg=RED, bg="#321c22")
        self.status_detail.configure(text=f"{context}: {error}", fg=RED)
        self.path_source.configure(text="Dota folder verified", fg=GREEN)
        self._append_log(f"{context}: {error}", error=True)

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
            self.activity_title.configure(text=message.upper(), fg=TEXT_SOFT)
            self.progress.configure(value=0.0)
            self.progress_label.configure(text="0.0%")
        else:
            self.activity_title.configure(text="ACTIVITY", fg=MUTED)

    def _set_progress(self, percent: float, message: str = "") -> None:
        percent = max(0.0, min(100.0, percent))
        current = float(self.progress["value"])
        if self.busy and percent < current:
            return
        self.progress.configure(value=percent)
        label = "100%" if percent >= 100.0 else f"{percent:.1f}%"
        self.progress_label.configure(text=label)
        if self.busy and message:
            self.activity_title.configure(text=message.upper(), fg=TEXT_SOFT)

    def _apply_status(self, result: dict) -> None:
        self.last_status = result
        view = status_presentation(result, self._selected_categories())
        language = result.get("language")
        detail = view["detail"]
        if isinstance(language, str) and language in engine.RECOGNIZED_LANGUAGES:
            detail += f"  ·  -language {language}"
        self.header_status.configure(
            text=f"  {view['badge']}  ",
            fg=view["color"],
            bg=SURFACE_ALT,
        )
        self.status_detail.configure(text=detail, fg=view["color"])
        self.build_button.set_text(view["action"])
        self.current_version.configure(text=engine.dota_version_label(result.get("current_dota_version")))
        self.recorded_version.configure(text=engine.dota_version_label(result.get("recorded_dota_version")))

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
        path = self.last_result.report if self.last_result else engine.application_root() / ".work/model-plan.json"
        self._open_path(path)

    def _open_output(self) -> None:
        if self.path_var.get().strip():
            path = Path(self.path_var.get().strip()) / "game" / f"dota_{self._selected_language()}"
        elif self.last_result:
            path = self.last_result.output_root
        else:
            messagebox.showinfo(
                "Not available yet",
                "Choose a Dota 2 folder or build overrides first.",
                parent=self.root,
            )
            return
        self._open_path(path)

    def _open_path(self, path: Path) -> None:
        if not path.exists():
            messagebox.showinfo("Not available yet", f"This path does not exist yet:\n{path}", parent=self.root)
            return
        try:
            if os.name == "nt":
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except OSError as exc:
            messagebox.showerror("Could not open path", str(exc), parent=self.root)

    def _copy_launch_option(self) -> None:
        option = f"-language {self._selected_language()}"
        self.root.clipboard_clear()
        self.root.clipboard_append(option)
        self._append_log(f"Copied Steam launch option: {option}")

    def _on_close(self) -> None:
        if self.busy:
            messagebox.showwarning(
                "Operation in progress",
                "Keep this window open until the current operation finishes so deployment cannot be interrupted.",
                parent=self.root,
            )
            return
        self._save_preferences()
        self.root.destroy()


def run_gui(*, smoke_test: bool = False) -> int:
    root = tk.Tk()
    if smoke_test:
        root.withdraw()
    app = DisablerApp(root, start_detection=not smoke_test)
    if smoke_test:
        root.update_idletasks()
        required = (app.path_entry, app.language_combo, app.build_button, app.clean_button, app.log)
        if not all(widget.winfo_exists() for widget in required):
            raise RuntimeError("GUI smoke test could not construct the essential controls.")
        if root.grid_rowconfigure(1)["weight"] != 0 or root.grid_rowconfigure(2)["weight"] != 1:
            raise RuntimeError("GUI resize ownership is not assigned to the activity panel.")
        if app.activity_panel.grid_rowconfigure(2)["weight"] != 1:
            raise RuntimeError("GUI log row is not configured to absorb additional height.")
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
