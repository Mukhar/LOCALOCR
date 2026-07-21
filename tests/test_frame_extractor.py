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
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.extractor import extract_frames
from src.extractor.frame_extractor import (
    FrameExtractionError,
    _finalize_frames,
)


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


# ---------------------------------------------------------------------------
# _finalize_frames — pure helper, no ffmpeg needed
# ---------------------------------------------------------------------------
def test_finalize_frames_names_files_correctly(tmp_path):
    """Canonical frame_NNNN.png tmp files → frame_NNNN_XXmYYs.png outputs
    with correctly shaped dicts."""
    tmp_dir = tmp_path / "tmp"
    out_path = tmp_path / "out"
    tmp_dir.mkdir()
    out_path.mkdir()

    (tmp_dir / "frame_0001.png").write_bytes(b"\x89PNG-1")
    (tmp_dir / "frame_0002.png").write_bytes(b"\x89PNG-2")
    (tmp_dir / "frame_0003.png").write_bytes(b"\x89PNG-3")

    finalized = _finalize_frames(tmp_dir, out_path, [0.0, 2.0, 4.0])

    names = sorted(p.name for p in out_path.glob("*.png"))
    assert names == [
        "frame_0001_00m00s.png",
        "frame_0002_00m02s.png",
        "frame_0003_00m04s.png",
    ]

    assert len(finalized) == 3
    assert finalized[0] == {
        "frame_path": str(out_path / "frame_0001_00m00s.png"),
        "frame_name": "frame_0001_00m00s.png",
        "timestamp": "00m00s",
        "frame_number": 1,
    }
    # sorted by frame_number
    assert [d["frame_number"] for d in finalized] == [1, 2, 3]


def test_finalize_frames_preserves_frame_number_via_regex(tmp_path):
    """Out-of-band gaps in the tmp sequence must NOT renumber survivors:
    regex-based numbering keeps frame_0005 as 5 (not 1) even if 6 is missing.
    Guards against the ``i + 1`` regression called out in plan-check WARNING 7.
    """
    tmp_dir = tmp_path / "tmp"
    out_path = tmp_path / "out"
    tmp_dir.mkdir()
    out_path.mkdir()

    (tmp_dir / "frame_0005.png").write_bytes(b"a")
    (tmp_dir / "frame_0007.png").write_bytes(b"b")

    finalized = _finalize_frames(tmp_dir, out_path, [10.0, 14.0])

    names = sorted(p.name for p in out_path.glob("*.png"))
    assert names == [
        "frame_0005_00m10s.png",
        "frame_0007_00m14s.png",
    ]
    assert [d["frame_number"] for d in finalized] == [5, 7]


def test_finalize_frames_skips_out_of_band_files(tmp_path, caplog):
    """Stray non-matching files log a WARNING and are skipped, not fatal."""
    tmp_dir = tmp_path / "tmp"
    out_path = tmp_path / "out"
    tmp_dir.mkdir()
    out_path.mkdir()

    (tmp_dir / "frame_0001.png").write_bytes(b"ok")
    (tmp_dir / "frame_bogus.png").write_bytes(b"stray")

    with caplog.at_level(logging.WARNING, logger="src.extractor.frame_extractor"):
        finalized = _finalize_frames(tmp_dir, out_path, [0.0, 0.0])

    # Only the well-named file survives.
    assert [d["frame_number"] for d in finalized] == [1]
    assert any("Ignoring unexpected file" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# extract_frames dispatch — fully mocked subprocess stack, no real ffmpeg
# ---------------------------------------------------------------------------
def test_extraction_mode_defaults_to_interval(tmp_path, monkeypatch):
    """With no ``extraction_mode`` key, dispatch routes to
    ``_extract_by_interval`` which shells out to ffmpeg with a ``fps=1/N``
    filter. Fully mocked — no real ffmpeg process is spawned.
    """
    # (a) fake binaries — extract_frames calls shutil.which for ffmpeg/ffprobe
    monkeypatch.setattr(
        "src.extractor.frame_extractor.shutil.which",
        lambda name: f"/fake/bin/{name}" if name in ("ffmpeg", "ffprobe") else None,
    )

    # (b) fake input video (must exist + have a supported extension)
    fake_video = tmp_path / "in.mp4"
    fake_video.write_bytes(b"")

    out_dir = tmp_path / "out"

    # (c) both ffprobe and ffmpeg go through subprocess.run — split by argv[0].
    probe_result = MagicMock(
        returncode=0,
        stdout='{"streams":[{"duration":"10.0"}]}',
        stderr="",
    )

    def _subprocess_side_effect(cmd, **kw):
        binary = cmd[0]
        if "ffprobe" in binary:
            return probe_result
        # ffmpeg branch: fabricate one tmp frame so _finalize_frames has work
        tmp_extract = out_dir / ".tmp_extract"
        tmp_extract.mkdir(parents=True, exist_ok=True)
        (tmp_extract / "frame_0001.png").write_bytes(b"\x89PNG")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch(
        "src.extractor.frame_extractor.subprocess.run",
        side_effect=_subprocess_side_effect,
    ) as mocked:
        result = extract_frames(str(fake_video), str(out_dir), 2)

    # Grab only the ffmpeg call (the second subprocess.run) and verify the
    # interval filter is present — proves interval-mode strategy fired.
    ffmpeg_calls = [c for c in mocked.call_args_list if "ffmpeg" in c.args[0][0]]
    assert ffmpeg_calls, "ffmpeg was never invoked"
    cmd_str = " ".join(ffmpeg_calls[0].args[0])
    assert "fps=1/" in cmd_str

    assert len(result) == 1
    assert result[0]["frame_name"] == "frame_0001_00m00s.png"


def test_invalid_extraction_mode_raises(tmp_path):
    """D6: a bogus extraction_mode fails fast with a clear message.

    Does NOT need ffmpeg mocking — validation runs before any subprocess.
    """
    fake_video = tmp_path / "in.mp4"
    fake_video.write_bytes(b"")

    with pytest.raises(FrameExtractionError) as excinfo:
        extract_frames(
            str(fake_video),
            str(tmp_path / "out"),
            2,
            cfg={"extraction_mode": "bogus"},
        )

    msg = str(excinfo.value)
    assert "'bogus'" in msg
    assert "one of" in msg
