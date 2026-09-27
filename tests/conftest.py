"""Minimal Home Assistant stubs for parser-only unit tests."""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType


class _ConfigEntry:
    @classmethod
    def __class_getitem__(cls, item):
        return cls


homeassistant = ModuleType("homeassistant")
config_entries = ModuleType("homeassistant.config_entries")
core = ModuleType("homeassistant.core")
config_entries.ConfigEntry = _ConfigEntry
core.HomeAssistant = object
sys.modules.setdefault("homeassistant", homeassistant)
sys.modules.setdefault("homeassistant.config_entries", config_entries)
sys.modules.setdefault("homeassistant.core", core)

sys.path.insert(0, str(Path(__file__).parent.parent / "mvdis_penalty_backend" / "app"))
