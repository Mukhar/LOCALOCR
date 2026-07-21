"""
tests/conftest.py
~~~~~~~~~~~~~~~~~
Ensures the project root (repo root) is on ``sys.path`` so tests can
``from src.extractor import extract_frames`` without a ``pip install -e .``
step. Also registers custom pytest markers.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "slow: end-to-end tests that shell out to real ffmpeg (kept in default run "
        "because the synthetic baseline video is small; deselect with -m 'not slow')",
    )
