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
    _debounce_pairs,
    _debounce_timestamps,
    _extract_by_scene,
    _finalize_frames,
    _parse_showinfo_pts,
)


SHOWINFO_FIXTURE = Path("tests/fixtures/showinfo_stderr.txt")
FRAME_EXTRACTOR_SRC = Path("src/extractor/frame_extractor.py")


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


# ===========================================================================
# Plan 01-02 additions: scene + hybrid extractors, PTS parsing, debounce,
# fail-fast scene_config validation, and the BLOCKER-2 negative fences.
# ===========================================================================

# ---------------------------------------------------------------------------
# _parse_showinfo_pts — pure regex helper
# ---------------------------------------------------------------------------
def test_parse_showinfo_pts_extracts_all_timestamps():
    """Fixture stderr contains 4 showinfo lines + 2 unrelated noise lines.
    Helper must pull only the 4 pts_time values, sorted ascending, as floats.
    """
    if not SHOWINFO_FIXTURE.exists():
        pytest.skip(f"{SHOWINFO_FIXTURE} not found")
    stderr = SHOWINFO_FIXTURE.read_text()
    assert _parse_showinfo_pts(stderr) == [6.0, 18.0, 29.666667, 40.0]


def test_parse_showinfo_pts_empty_stderr():
    """Empty / no-match input returns [] without raising."""
    assert _parse_showinfo_pts("") == []
    assert _parse_showinfo_pts("nothing to see here, no pts_time in sight") == []


# ---------------------------------------------------------------------------
# _debounce_timestamps + _debounce_pairs — pure functions, no fs side effects
# ---------------------------------------------------------------------------
def test_debounce_drops_close_frames():
    """1.0-second gap: 0.0 kept, 0.5 dropped, 1.5 kept, 1.7 dropped, 3.0 kept."""
    assert _debounce_timestamps([0.0, 0.5, 1.5, 1.7, 3.0], 1.0) == [0.0, 1.5, 3.0]


def test_debounce_min_gap_zero_is_noop():
    """min_gap == 0 (and any negative) returns the input unchanged."""
    ts = [0.0, 0.1, 0.2, 0.3]
    assert _debounce_timestamps(ts, 0) == ts
    assert _debounce_timestamps(ts, -1) == ts
    # And still a defensive copy — mutating the return must not touch input.
    out = _debounce_timestamps(ts, 0)
    out.append(9.9)
    assert ts == [0.0, 0.1, 0.2, 0.3]


def test_debounce_pairs_returns_survivor_pairs():
    """Debounce keeps the leading pair and any pair whose PTS is >= min_gap
    after the last-kept PTS. Path('b') at 0.5 is the dropped survivor.
    """
    pairs = [
        (Path("a"), 0.0),
        (Path("b"), 0.5),
        (Path("c"), 1.5),
        (Path("d"), 3.0),
    ]
    assert _debounce_pairs(pairs, 1.0) == [
        (Path("a"), 0.0),
        (Path("c"), 1.5),
        (Path("d"), 3.0),
    ]


def test_debounce_pairs_is_pure_no_filesystem_side_effects(tmp_path):
    """Even with real Path objects that exist on disk, _debounce_pairs must
    NOT unlink dropped files — caller does that (BLOCKER 3 responsibility).
    """
    files = []
    for i, name in enumerate(["a.png", "b.png", "c.png", "d.png"]):
        p = tmp_path / name
        p.write_bytes(b"\x89PNG")
        files.append(p)

    pairs = [(files[0], 0.0), (files[1], 0.5), (files[2], 1.5), (files[3], 3.0)]
    kept = _debounce_pairs(pairs, 1.0)

    # b.png did not survive the debounce
    assert {p.name for p, _ in kept} == {"a.png", "c.png", "d.png"}
    # ...but all four files still exist on disk. Pure function.
    for p in files:
        assert p.exists(), f"{p.name} was unlinked by _debounce_pairs — must be caller's job"


# ---------------------------------------------------------------------------
# _extract_by_scene — mocked ffmpeg + real tmp dir
# ---------------------------------------------------------------------------
def _fake_binaries(monkeypatch):
    """Patch shutil.which so extract_frames sees fake ffmpeg/ffprobe paths."""
    monkeypatch.setattr(
        "src.extractor.frame_extractor.shutil.which",
        lambda name: f"/fake/bin/{name}" if name in ("ffmpeg", "ffprobe") else None,
    )


def test_scene_mode_ffmpeg_command_shape(tmp_path, monkeypatch):
    """The ffmpeg command list assembled by _extract_by_scene must include
    the ``select='gt(scene,T)',showinfo`` filter with the exact threshold.
    """
    _fake_binaries(monkeypatch)
    fake_video = tmp_path / "in.mp4"
    fake_video.write_bytes(b"")
    out_dir = tmp_path / "out"

    captured_cmds: list[list[str]] = []

    def _side_effect(cmd, **kw):
        captured_cmds.append(cmd)
        binary = cmd[0]
        if "ffprobe" in binary:
            return MagicMock(returncode=0, stdout='{"streams":[{"duration":"30.0"}]}', stderr="")
        # ffmpeg: fabricate 1 tmp frame + a showinfo stderr line.
        tmp_extract = out_dir / ".tmp_extract"
        tmp_extract.mkdir(parents=True, exist_ok=True)
        (tmp_extract / "frame_0001.png").write_bytes(b"\x89PNG")
        return MagicMock(
            returncode=0,
            stdout="",
            stderr="[Parsed_showinfo_1 @ 0x0] n:0 pts_time:6.000000",
        )

    with patch("src.extractor.frame_extractor.subprocess.run", side_effect=_side_effect):
        extract_frames(
            str(fake_video), str(out_dir), 2,
            cfg={"extraction_mode": "scene", "scene_config": {"threshold": 0.3}},
        )

    ffmpeg_cmds = [c for c in captured_cmds if "ffmpeg" in c[0]]
    assert ffmpeg_cmds, "ffmpeg was never invoked in scene mode"
    joined = " ".join(ffmpeg_cmds[0])
    assert "select='gt(scene,0.3)',showinfo" in joined


def test_scene_mode_deletes_debounced_tmp_files(tmp_path, monkeypatch):
    """BLOCKER 3 semantic proof: files that lose the debounce must be
    unlinked before _finalize_frames runs so it sees a tmp dir whose
    contents match kept_ts 1:1.

    We pre-arrange 3 tmp frames with PTS [0.0, 0.5, 2.0] and min_gap=1.0
    so the middle one is the debounce loser. After _extract_by_scene
    completes, exactly 2 output PNGs should exist and no leftover tmp
    files should linger.
    """
    out_path = tmp_path / "out"
    tmp_dir = tmp_path / "tmp"
    out_path.mkdir()
    tmp_dir.mkdir()

    # Fabricate the 3 tmp frames that ffmpeg "would have" produced.
    for i in (1, 2, 3):
        (tmp_dir / f"frame_{i:04d}.png").write_bytes(b"\x89PNG")

        # Mock run_subprocess (the shared helper) to be a no-op that returns a
    # synthetic showinfo stderr. Patch the name in the extractor's namespace
    # because that's where it was imported into (see PEP `where you patch`).
    fake_stderr = (
        "[Parsed_showinfo_1 @ 0x0] pts_time:0.000000\n"
        "[Parsed_showinfo_1 @ 0x0] pts_time:0.500000\n"
        "[Parsed_showinfo_1 @ 0x0] pts_time:2.000000\n"
    )

    with patch(
        "src.extractor.frame_extractor.run_subprocess",
        return_value=fake_stderr,
    ):
        result = _extract_by_scene(
            video=tmp_path / "in.mp4",
            out_path=out_path,
            tmp_dir=tmp_dir,
            ffmpeg_bin="/fake/ffmpeg",
            duration=10.0,
            cfg={"scene_config": {"threshold": 0.3, "min_gap_seconds": 1.0}},
        )

    # Two frames should survive the 1.0-second debounce (0.0 and 2.0).
    assert len(result) == 2, f"expected 2 survivors, got {result}"
    output_files = sorted(p.name for p in out_path.glob("*.png"))
    assert len(output_files) == 2, output_files
    # ...and the debounced middle file (frame_0002.png) must NOT be lingering
    # in tmp_dir either — finalize + our unlink both did their job.
    assert list(tmp_dir.glob("frame_*.png")) == [], (
        "debounce loser was not unlinked from tmp_dir"
    )


# ---------------------------------------------------------------------------
# _extract_by_hybrid — semantic + negative BLOCKER 2 fences
# ---------------------------------------------------------------------------
def test_hybrid_mode_runs_two_ffmpeg_passes(tmp_path, monkeypatch):
    """BLOCKER 2 semantic proof: hybrid MUST invoke ffmpeg exactly twice,
    once with scene detection and once with fps=1/max_gap. NOT one single
    invocation with an eq(mod(t, N), 0) filter.
    """
    _fake_binaries(monkeypatch)
    fake_video = tmp_path / "in.mp4"
    fake_video.write_bytes(b"")
    out_dir = tmp_path / "out"

    ffmpeg_cmds: list[list[str]] = []

    def _subprocess_side_effect(cmd, **kw):
        binary = cmd[0]
        if "ffprobe" in binary:
            return MagicMock(returncode=0, stdout='{"streams":[{"duration":"60.0"}]}', stderr="")
        # ffmpeg branch. cmd is one of the two hybrid passes.
        ffmpeg_cmds.append(cmd)
        # Locate the tmp dir the extractor made (out_dir/.tmp_extract/_scene or _gap).
        # The last positional arg is the output pattern; parent of that is the scoped subdir.
        out_pattern = Path(cmd[-1])
        subdir = out_pattern.parent
        subdir.mkdir(parents=True, exist_ok=True)
        # Emit exactly one fake frame + one showinfo line per call so
        # counts match and no drift fallback kicks in.
        (subdir / "frame_0001.png").write_bytes(b"\x89PNG")
        pts = 5.0 if "_scene" in str(subdir) else 15.0
        return MagicMock(
            returncode=0,
            stdout="",
            stderr=f"[Parsed_showinfo_1 @ 0x0] pts_time:{pts:.6f}",
        )

    with patch(
        "src.extractor.frame_extractor.subprocess.run",
        side_effect=_subprocess_side_effect,
    ):
        extract_frames(
            str(fake_video), str(out_dir), 2,
            cfg={
                "extraction_mode": "hybrid",
                "scene_config": {"threshold": 0.3, "min_gap_seconds": 1.0, "max_gap_seconds": 10.0},
            },
        )

    assert len(ffmpeg_cmds) == 2, f"expected 2 ffmpeg passes, got {len(ffmpeg_cmds)}"
    pass1 = " ".join(ffmpeg_cmds[0])
    pass2 = " ".join(ffmpeg_cmds[1])
    assert "select='gt(scene," in pass1, f"scene pass missing scene filter: {pass1}"
    assert "fps=1/" in pass2, f"gap pass missing fps=1/ filter: {pass2}"


def test_hybrid_mode_does_not_use_eq_mod_filter(tmp_path, monkeypatch):
    """BLOCKER 2 negative fence: NO ffmpeg command assembled during a hybrid
    run may contain the broken modulo select filter.
    """
    _fake_binaries(monkeypatch)
    fake_video = tmp_path / "in.mp4"
    fake_video.write_bytes(b"")
    out_dir = tmp_path / "out"

    captured_ffmpeg_cmds: list[str] = []

    def _subprocess_side_effect(cmd, **kw):
        binary = cmd[0]
        if "ffprobe" in binary:
            return MagicMock(returncode=0, stdout='{"streams":[{"duration":"60.0"}]}', stderr="")
        captured_ffmpeg_cmds.append(" ".join(cmd))
        out_pattern = Path(cmd[-1])
        subdir = out_pattern.parent
        subdir.mkdir(parents=True, exist_ok=True)
        (subdir / "frame_0001.png").write_bytes(b"\x89PNG")
        return MagicMock(returncode=0, stdout="", stderr="[Parsed_showinfo_1 @ 0x0] pts_time:1.000000")

    with patch("src.extractor.frame_extractor.subprocess.run", side_effect=_subprocess_side_effect):
        extract_frames(
            str(fake_video), str(out_dir), 2,
            cfg={"extraction_mode": "hybrid", "scene_config": {"max_gap_seconds": 10.0}},
        )

    forbidden = "eq(mod(t,"
    offenders = [c for c in captured_ffmpeg_cmds if forbidden in c]
    assert not offenders, f"forbidden modulo filter appeared in: {offenders}"


def test_frame_extractor_source_has_no_eq_mod_filter():
    """Source-level regression fence for BLOCKER 2 — the broken filter must
    not sneak back into frame_extractor.py via a future edit or copy-paste.
    """
    src = FRAME_EXTRACTOR_SRC.read_text()
    forbidden = "eq(mod(t,"
    assert forbidden not in src, (
        f"forbidden filter substring {forbidden!r} was reintroduced into "
        f"{FRAME_EXTRACTOR_SRC}. Hybrid mode must use two ffmpeg passes, "
        f"not a single-pass modulo filter."
    )


# ---------------------------------------------------------------------------
# _validate_extraction_config — scene_config fail-fast
# ---------------------------------------------------------------------------
def test_invalid_scene_threshold_raises(tmp_path):
    """threshold > 1.0 must fail fast before any ffmpeg call."""
    fake_video = tmp_path / "in.mp4"
    fake_video.write_bytes(b"")

    with pytest.raises(FrameExtractionError) as excinfo:
        extract_frames(
            str(fake_video), str(tmp_path / "out"), 2,
            cfg={"extraction_mode": "scene", "scene_config": {"threshold": 2.0}},
        )
    msg = str(excinfo.value)
    assert "threshold" in msg
    assert "[0.0, 1.0]" in msg


def test_invalid_scene_negative_gap_raises(tmp_path):
    """min_gap_seconds < 0 must fail fast before any ffmpeg call."""
    fake_video = tmp_path / "in.mp4"
    fake_video.write_bytes(b"")

    with pytest.raises(FrameExtractionError) as excinfo:
        extract_frames(
            str(fake_video), str(tmp_path / "out"), 2,
            cfg={"extraction_mode": "scene", "scene_config": {"min_gap_seconds": -1}},
        )
    msg = str(excinfo.value)
    assert "min_gap_seconds" in msg
    assert ">= 0" in msg
