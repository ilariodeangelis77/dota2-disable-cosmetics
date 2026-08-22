"""Compatibility façade for the modular desktop dashboard.

New code should import :mod:`dota_disabler.gui_model` for Tk-free helpers or
:mod:`dota_disabler.gui` for the desktop application. This module preserves the
legacy import surface used by source callers and older tests.
"""

from __future__ import annotations

from dota_disabler import gui_model as _gui_model
from dota_disabler.gui import (
    DisablerApp,
    FlatButton,
    ToggleSwitch,
    desktop_work_area,
    run_gui,
)
from dota_disabler.gui_model import *


__all__ = [
    *_gui_model.__all__,
    "DisablerApp",
    "FlatButton",
    "ToggleSwitch",
    "desktop_work_area",
    "run_gui",
]


if __name__ == "__main__":
    raise SystemExit(run_gui())
