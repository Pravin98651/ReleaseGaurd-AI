"""Pytest configuration ensuring demo-app/backend is importable as `app`."""

from __future__ import annotations

import sys
from pathlib import Path

DEMO_BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(DEMO_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(DEMO_BACKEND_DIR))
