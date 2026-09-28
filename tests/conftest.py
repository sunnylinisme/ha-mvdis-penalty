"""Test path setup."""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).parent.parent / "mvdis_penalty_backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "app"))
