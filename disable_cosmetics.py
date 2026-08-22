#!/usr/bin/env python3
"""Compatibility launcher for the modular :mod:`dota_disabler` package.

Existing scripts may continue importing this module. New code should import
``dota_disabler.public`` or the narrower owning module instead.
"""

from __future__ import annotations

from dota_disabler import public as _public
from dota_disabler.public import *


__all__ = _public.__all__


if __name__ == "__main__":
    raise SystemExit(main())
