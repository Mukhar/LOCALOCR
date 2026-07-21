"""
tests/test_frame_extractor.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Regression + unit tests for the strategy-pattern refactor in Phase 1
Plan 01-01.

Task 5 (this file's first landing) contributes the D2 backward-compat
fence: ``test_interval_mode_byte_identical_to_baseline`` compares the
current build's output against a sha256 manifest recorded from the
pre-refactor build. Task 6 grows the file with pure-helper tests and
dispatch tests.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.extractor import extract_frames


BASELINE_MANIFEST = Path("tests/fixtures/interval_baseline_manifest.json")


def _diff(actual: dict, expected: dict) -> str:
    """Small, human-scannable diff for byte-manifest mismatches."""
    lines = []
    all_keys = sorted(set(actual) | set(expected))
    for k in all_keys:
        a = actual.get(k)
        e = expected.get(k)
        if a != e:
            lines.append(f"  {k}: actual={a} expected={e}")
    return "\n".join(lines) or "  (no per-file diff — length mismatch?)"


@pytest.mark.slow
def test_interval_mode_byte_identical_to_baseline(tmp_path):
    """D2: interval-mode output must be byte-identical to the pre-Phase-1 build.

    Shells out to real ffmpeg. Skips cleanly if the baseline manifest or its
    source video is missing (dev machines / CI stripped of large fixtures).
    """
    if not BASELINE_MANIFEST.exists():
        pytest.skip(f"Baseline manifest {BASELINE_MANIFEST} not found")

    manifest = json.loads(BASELINE_MANIFEST.read_text())
    video = manifest["video"]
    interval = manifest.get("interval_seconds", 2)

    if not Path(video).exists():
        pytest.skip(f"Baseline video {video!r} not in repo")

    out = tmp_path / "frames"
    # No cfg — replicate the v1.0 call shape exactly.
    extract_frames(video, str(out), interval_seconds=interval)

    actual = {}
    for p in sorted(out.glob("*.png")):
        actual[p.name] = {
            "size": p.stat().st_size,
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
        }

    expected = manifest["frames"]
    assert actual == expected, (
        "Byte-level regression vs baseline manifest. Diffs:\n"
        + _diff(actual, expected)
    )
