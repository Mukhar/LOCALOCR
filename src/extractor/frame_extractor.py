"""
frame_extractor.py
~~~~~~~~~~~~~~~~~~
Extract frames from an MP4 video at a configurable interval using ffmpeg.

Output naming convention:
    frame_NNNN_XXmYYs.png
    e.g. frame_0003_00m06s.png  — 3rd frame at 6 s into the video
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from pathlib import Path

from src.common.subprocess_utils import (
    BinaryNotFoundError,
    SubprocessError,
    require_binary,
    run_subprocess,
)

logger = logging.getLogger(__name__)


class FrameExtractionError(Exception):
    """Raised when frame extraction cannot be completed."""


def _probe_video(video_path: Path, ffprobe_bin: str) -> float:
    cmd = [
        ffprobe_bin,
        "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        "-select_streams", "v:0",
        str(video_path),
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired as exc:
        raise FrameExtractionError(
            f"ffprobe timed out reading {str(video_path)!r}"
        ) from exc
    except OSError as exc:
        raise FrameExtractionError(f"Failed to launch ffprobe: {exc}") from exc

    if result.returncode != 0:
        raise FrameExtractionError(
            f"ffprobe could not read {str(video_path)!r} — file may be corrupted.\n"
            f"stderr: {result.stderr.strip()}"
        )

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise FrameExtractionError(
            f"Unexpected ffprobe output for {str(video_path)!r}"
        ) from exc

    streams = data.get("streams", [])
    if not streams:
        raise FrameExtractionError(f"No video stream found in {str(video_path)!r}")

    stream = streams[0]
    raw = stream.get("duration")
    if raw:
        try:
            d = float(raw)
            if d > 0:
                return d
        except ValueError:
            pass

    tag = stream.get("tags", {}).get("DURATION", "")
    if tag:
        parts = tag.split(":")
        if len(parts) == 3:
            try:
                d = float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
                if d > 0:
                    return d
            except ValueError:
                pass

    raise FrameExtractionError(
        f"Could not determine duration for {str(video_path)!r}."
    )


def _validate_inputs(video_path: str, interval_seconds: int) -> Path:
    if not isinstance(interval_seconds, int) or interval_seconds < 1:
        raise ValueError(
            f"interval_seconds must be a positive integer, got {interval_seconds!r}"
        )

    path = Path(video_path).resolve()

    if not path.exists():
        raise FrameExtractionError(f"Video file not found: {str(path)!r}")

    if not path.is_file():
        raise FrameExtractionError(f"Path is not a regular file: {str(path)!r}")

    SUPPORTED = {".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v"}
    if path.suffix.lower() not in SUPPORTED:
        raise FrameExtractionError(
            f"Unsupported file extension {path.suffix!r}. "
            f"Accepted formats: {', '.join(sorted(SUPPORTED))}"
        )

    return path


def _format_timestamp(total_seconds: int) -> str:
    minutes, seconds = divmod(total_seconds, 60)
    return f"{minutes:02d}m{seconds:02d}s"


# _run_ffmpeg + _require_binary previously lived here; migrated to
# src.common.subprocess_utils in plan 02-01. Call sites now use
# ``require_binary`` and ``run_subprocess`` directly; the top-level
# :func:`extract_frames` translates :class:`BinaryNotFoundError` /
# :class:`SubprocessError` into :class:`FrameExtractionError` at the
# public boundary so external callers see one unchanged exception type.


# Sequence-number regex for the ffmpeg tmp filenames (frame_NNNN.png).
# Kept module-level so _finalize_frames doesn't recompile on every call.
_SEQ_RE = re.compile(r"frame_(\d+)\.png$")

# PTS-time regex for parsing ffmpeg's `showinfo` filter stderr. Matches only
# the numeric field — e.g. from "... pts_time:29.666667 ..." captures
# "29.666667". Kept module-level for the same recompilation reason.
_PTS_RE = re.compile(r"pts_time:(\d+\.?\d*)")


def _parse_showinfo_pts(stderr: str) -> list[float]:
    """Extract PTS timestamps (seconds) from ffmpeg ``showinfo`` stderr.

    ffmpeg emits one ``[Parsed_showinfo_N @ ...] ... pts_time:X.XXXXXX ...``
    line per selected frame. We pluck the ``pts_time`` value out of each and
    return them sorted ascending. Unrelated stderr lines (codec warnings,
    stream headers) are ignored by the regex.

    Empty / no-match input returns ``[]``.
    """
    return sorted(float(m.group(1)) for m in _PTS_RE.finditer(stderr))


def _debounce_timestamps(ts: list[float], min_gap: float) -> list[float]:
    """Drop timestamps within ``min_gap`` seconds of the previously kept one.

    Pure function — no filesystem interaction. Preserves the first element
    unconditionally; each subsequent element is kept iff it is at least
    ``min_gap`` seconds after the last-kept element. ``min_gap <= 0`` is a
    no-op (returns a defensive copy).
    """
    if min_gap <= 0 or not ts:
        return list(ts)
    kept = [ts[0]]
    for t in ts[1:]:
        if t - kept[-1] >= min_gap:
            kept.append(t)
    return kept


def _debounce_pairs(
    pairs: list,
    min_gap: float,
) -> list:
    """Debounce ``(Path, pts_seconds)`` pairs by PTS gap.

    Sorts by PTS, then walks the sequence keeping the first pair and any
    subsequent pair whose PTS is at least ``min_gap`` after the last-kept
    PTS. Returns survivors in PTS order.

    Pure function — caller is responsible for unlinking any files NOT in
    the returned list. This keeps the helper testable without a temp dir
    and lets scene/hybrid extractors own their tmp-cleanup policy.
    """
    if min_gap <= 0 or not pairs:
        return list(pairs)
    ordered = sorted(pairs, key=lambda fp: fp[1])
    kept = [ordered[0]]
    for f, t in ordered[1:]:
        if t - kept[-1][1] >= min_gap:
            kept.append((f, t))
    return kept


def _finalize_frames(
    tmp_dir: Path,
    out_path: Path,
    timestamps: list[float],
) -> list[dict]:
    """
    Rename ffmpeg's ``frame_NNNN.png`` tmp files into the final
    ``frame_NNNN_XXmYYs.png`` naming contract and return per-frame dicts.

    Parameters
    ----------
    tmp_dir : Path
        Directory holding ffmpeg's numbered tmp frames. Iterated in sorted
        order so ``timestamps[i]`` aligns with the i-th kept frame.
    out_path : Path
        Destination directory for the final renamed frames.
    timestamps : list[float]
        One timestamp per tmp frame (seconds from start of video). Must be
        the same length as the sorted tmp-file list; short lists silently
        cause an IndexError, which is a caller bug.

    Notes
    -----
    * Frame numbering is derived from the ``NNNN`` capture in the tmp
      filename via ``_SEQ_RE`` — NOT from the iteration index. This
      preserves the current warn-and-skip semantics for out-of-band files
      (a stray file logs a warning and is skipped without renumbering the
      rest).
    * Timestamp is rounded to the nearest integer second, matching the
      pre-refactor formatting.
    * Purely a naming/rename step. No config access, no ffmpeg calls.
    """
    finalized: list[dict] = []
    for i, tmp_file in enumerate(sorted(tmp_dir.glob("frame_*.png"))):
        m = _SEQ_RE.search(tmp_file.name)
        if not m:
            logger.warning("Ignoring unexpected file: %s", tmp_file.name)
            continue

        frame_number = int(m.group(1))
        ts_seconds = int(round(timestamps[i]))
        frame_name = f"frame_{frame_number:04d}_{_format_timestamp(ts_seconds)}.png"
        final_path = out_path / frame_name

        try:
            tmp_file.rename(final_path)
        except OSError as exc:
            raise FrameExtractionError(
                f"Failed to move frame file: {exc}"
            ) from exc

        finalized.append({
            "frame_path": str(final_path),
            "frame_name": frame_name,
            "timestamp": _format_timestamp(ts_seconds),
            "frame_number": frame_number,
        })
        logger.debug("Saved %s", frame_name)

    finalized.sort(key=lambda e: e["frame_number"])
    return finalized


def _extract_by_interval(
    video: Path,
    out_path: Path,
    tmp_dir: Path,
    ffmpeg_bin: str,
    duration: float,
    cfg: dict,
) -> list[dict]:
    """
    Fixed-fps extraction strategy — the original v1.0 behavior.

    Samples one frame every ``cfg['frame_interval_seconds']`` seconds via
    ``-vf fps=1/N``. Each kept frame's timestamp is synthesized as
    ``i * interval`` (matches the pre-refactor formula
    ``(frame_number - 1) * interval`` for canonical tmp names).
    """
    interval = int(cfg.get("frame_interval_seconds", 2))
    tmp_pattern = str(tmp_dir / "frame_%04d.png")
    expected_frames = max(1, int(duration / interval))

    cmd = [
        ffmpeg_bin,
        "-hide_banner",
        "-loglevel", "error",
        "-i", str(video),
        "-vf", f"fps=1/{interval}",
        "-vsync", "vfr",
        tmp_pattern,
    ]

    logger.debug("ffmpeg command: %s", " ".join(cmd))

    dur_min = int(duration) // 60
    dur_sec = int(duration) % 60
    logger.info(
        "Extracting ~%d frames from %dm%ds video (this may take a while)...",
        expected_frames, dur_min, dur_sec,
    )

    timeout = max(300, int(duration) * 3)
    run_subprocess(cmd, str(video), timeout)

    tmp_frames = sorted(tmp_dir.glob("frame_*.png"))

    if not tmp_frames:
        raise FrameExtractionError(
            f"ffmpeg completed but wrote no frames for {str(video)!r}."
        )

    logger.info("Renaming %d extracted frame(s)\u2026", len(tmp_frames))

    # Interval mode: the i-th kept frame corresponds to i * interval seconds
    # from the start of the video. Matches the pre-refactor formula
    # `(frame_number - 1) * interval` for canonical `frame_NNNN.png` names.
    timestamps = [i * interval for i in range(len(tmp_frames))]
    return _finalize_frames(tmp_dir, out_path, timestamps)


def _extract_by_scene(
    video: Path,
    out_path: Path,
    tmp_dir: Path,
    ffmpeg_bin: str,
    duration: float,
    cfg: dict,
) -> list[dict]:
    """
    Scene-change extraction strategy.

    Uses ffmpeg's ``select='gt(scene,T)'`` filter to keep only frames whose
    inter-frame scene-change score exceeds ``threshold``. The ``showinfo``
    filter is chained so ffmpeg logs each kept frame's PTS to stderr; we
    parse those PTS values and use them (real timestamps — D5) to name
    output files. Close-together scene fires are debounced by
    ``min_gap_seconds`` and losing tmp files are unlinked so
    ``_finalize_frames`` sees a directory whose contents match ``kept_ts`` 1:1.

    scene_config keys read here:
      - threshold        (float in [0.0, 1.0]; default 0.3)
      - min_gap_seconds  (float >= 0;         default 1.0)
    """
    scene_cfg = cfg.get("scene_config", {}) or {}
    threshold = float(scene_cfg.get("threshold", 0.3))
    min_gap = float(scene_cfg.get("min_gap_seconds", 1.0))

    tmp_pattern = str(tmp_dir / "frame_%04d.png")
    cmd = [
        ffmpeg_bin, "-hide_banner", "-loglevel", "info",
        "-i", str(video),
        "-vf", f"select='gt(scene,{threshold})',showinfo",
        "-vsync", "vfr",
        tmp_pattern,
    ]
    timeout = max(300, int(duration) * 3)
    stderr = run_subprocess(cmd, str(video), timeout, capture_stderr=True)

    raw_ts = _parse_showinfo_pts(stderr or "")
    tmp_frames = sorted(tmp_dir.glob("frame_*.png"))

    if not tmp_frames:
        raise FrameExtractionError(
            f"ffmpeg scene mode wrote no frames for {str(video)!r} "
            f"(threshold={threshold} may be too high)."
        )

    # ffmpeg writes one file per selected frame; PTS list SHOULD match. If NOT,
    # we have real parser/container drift — fall back to synthetic timestamps
    # with a WARNING. This preserves D5 as "real PTS unless proven impossible".
    if len(tmp_frames) != len(raw_ts):
        logger.warning(
            "Scene extraction: file count %d != PTS count %d BEFORE debounce "
            "\u2014 parser or container drift, using synthetic timestamps as fallback",
            len(tmp_frames), len(raw_ts),
        )
        interval = int(cfg.get("frame_interval_seconds", 2))
        synthetic = [i * interval for i in range(len(tmp_frames))]
        return _finalize_frames(tmp_dir, out_path, synthetic)

    # Debounce (file, pts) pairs together; unlink losers so _finalize_frames
    # sees exactly the surviving frames (BLOCKER 3 fix).
    all_pairs = list(zip(tmp_frames, raw_ts))
    kept_pairs = _debounce_pairs(all_pairs, min_gap)
    kept_files = {f for f, _ in kept_pairs}
    for tmp_file, _ in all_pairs:
        if tmp_file not in kept_files:
            tmp_file.unlink(missing_ok=True)

    kept_ts = [p for _, p in kept_pairs]
    return _finalize_frames(tmp_dir, out_path, kept_ts)


def _extract_by_hybrid(
    video: Path,
    out_path: Path,
    tmp_dir: Path,
    ffmpeg_bin: str,
    duration: float,
    cfg: dict,
) -> list[dict]:
    """
    Hybrid extraction — two ffmpeg passes merged by PTS.

    Pass A: scene detection (``select='gt(scene,T)'``) into ``tmp_dir/_scene``.
    Pass B: fixed interval tick (``fps=1/max_gap``) into ``tmp_dir/_gap``.
    Both passes emit ``showinfo``; PTS values drive the merge.

    Combined ``(file, pts)`` pairs are debounced by ``min_gap_seconds``. Scene
    entries are placed first in the concat so a stable sort (as used by
    ``_debounce_pairs``) prefers a scene frame over a gap frame at identical
    PTS. Survivors are renamed into ``tmp_dir`` as canonical
    ``frame_NNNN.png`` so the pre-existing ``_finalize_frames`` contract holds.

    Fixes BLOCKER 2 — the previous single-pass modulo-based select filter
    was semantically broken (floats almost never hit exact modulo
    boundaries, so the fallback tick effectively never fired). This module
    MUST NOT contain that filter anywhere;
    ``test_frame_extractor_source_has_no_eq_mod_filter`` fences it.

    scene_config keys read here:
      - threshold        (float in [0.0, 1.0]; default 0.3)
      - min_gap_seconds  (float >= 0;         default 1.0)
      - max_gap_seconds  (float > 0;          default 10.0)
    """
    scene_cfg = cfg.get("scene_config", {}) or {}
    threshold = float(scene_cfg.get("threshold", 0.3))
    min_gap = float(scene_cfg.get("min_gap_seconds", 1.0))
    max_gap = float(scene_cfg.get("max_gap_seconds", 10.0))

    # Pass A: scene detection into a scoped subdir
    scene_dir = tmp_dir / "_scene"
    scene_dir.mkdir(exist_ok=True)
    scene_cmd = [
        ffmpeg_bin, "-hide_banner", "-loglevel", "info",
        "-i", str(video),
        "-vf", f"select='gt(scene,{threshold})',showinfo",
        "-vsync", "vfr",
        str(scene_dir / "frame_%04d.png"),
    ]
    scene_stderr = run_subprocess(
        scene_cmd, str(video),
        max(300, int(duration) * 3),
        capture_stderr=True,
    ) or ""
    scene_pts = _parse_showinfo_pts(scene_stderr)
    scene_files = sorted(scene_dir.glob("frame_*.png"))

    # Pass B: fixed-interval tick at max_gap into another scoped subdir
    gap_dir = tmp_dir / "_gap"
    gap_dir.mkdir(exist_ok=True)
    gap_cmd = [
        ffmpeg_bin, "-hide_banner", "-loglevel", "info",
        "-i", str(video),
        "-vf", f"fps=1/{max_gap},showinfo",
        "-vsync", "vfr",
        str(gap_dir / "frame_%04d.png"),
    ]
    gap_stderr = run_subprocess(
        gap_cmd, str(video),
        max(300, int(duration) * 3),
        capture_stderr=True,
    ) or ""
    gap_pts = _parse_showinfo_pts(gap_stderr)
    gap_files = sorted(gap_dir.glob("frame_*.png"))

    # Symmetric drift guard — mirrors _extract_by_scene's D5 fallback rigor.
    # If either pass has file/PTS drift, log a warning and trim to the
    # shorter list so `zip` doesn't silently discard anything.
    if len(scene_files) != len(scene_pts):
        logger.warning(
            "Hybrid scene pass drift: %d files vs %d PTS \u2014 trimming",
            len(scene_files), len(scene_pts),
        )
        n = min(len(scene_files), len(scene_pts))
        scene_files, scene_pts = scene_files[:n], scene_pts[:n]
    if len(gap_files) != len(gap_pts):
        logger.warning(
            "Hybrid gap pass drift: %d files vs %d PTS \u2014 trimming",
            len(gap_files), len(gap_pts),
        )
        n = min(len(gap_files), len(gap_pts))
        gap_files, gap_pts = gap_files[:n], gap_pts[:n]

    # Merge — debounce combined (file, pts) list. Scene entries first so the
    # stable sort inside _debounce_pairs prefers a scene frame at equal PTS.
    combined = list(zip(scene_files, scene_pts)) + list(zip(gap_files, gap_pts))

    if not combined:
        raise FrameExtractionError(
            f"ffmpeg hybrid mode produced no frames for {str(video)!r} "
            f"(threshold={threshold}, max_gap={max_gap})."
        )

    kept = _debounce_pairs(combined, min_gap)
    kept_files_set = {f for f, _ in kept}

    # Move survivors into tmp_dir with fresh sequential names so the existing
    # _finalize_frames contract (frame_NNNN.png inputs) holds.
    final_ts: list[float] = []
    for i, (src, pts) in enumerate(kept, start=1):
        dst = tmp_dir / f"frame_{i:04d}.png"
        src.rename(dst)
        final_ts.append(pts)

    # Delete losers and clean up scoped subdirs.
    for f in scene_files + gap_files:
        if f.exists() and f not in kept_files_set:
            f.unlink(missing_ok=True)
    for d in (scene_dir, gap_dir):
        try:
            d.rmdir()
        except OSError:
            pass

    return _finalize_frames(tmp_dir, out_path, final_ts)


# Extraction-mode dispatch table. Adding a new mode is: (1) write a strategy
# function with the same
# ``(video, out_path, tmp_dir, ffmpeg_bin, duration, cfg) -> list[dict]``
# signature, (2) register it here.
_EXTRACTORS = {
    "interval": _extract_by_interval,
    "scene":    _extract_by_scene,
    "hybrid":   _extract_by_hybrid,
}


def _validate_extraction_config(cfg: dict) -> str:
    """
    Resolve and validate ``cfg['extraction_mode']``, returning the
    normalized (lower-cased) mode name.

    Fails fast per D6 when the mode is not a registered extractor. For
    ``scene`` and ``hybrid`` modes, ``scene_config`` bounds are also
    checked so ffmpeg is never launched with a nonsensical threshold
    or a negative gap.

    scene_config bounds:
      - threshold        in [0.0, 1.0]        (scene + hybrid)
      - min_gap_seconds  >= 0                 (scene + hybrid)
      - max_gap_seconds  > 0                  (hybrid only)
    """
    mode = str(cfg.get("extraction_mode", "interval")).lower()
    if mode not in _EXTRACTORS:
        raise FrameExtractionError(
            f"extraction_mode {mode!r} invalid. Must be one of: {sorted(_EXTRACTORS)}"
        )

    if mode in ("scene", "hybrid"):
        scene_cfg = cfg.get("scene_config") or {}
        if not isinstance(scene_cfg, dict):
            raise FrameExtractionError(
                f"scene_config must be a dict when extraction_mode={mode!r}, "
                f"got {type(scene_cfg).__name__}"
            )

        if "threshold" in scene_cfg:
            try:
                threshold = float(scene_cfg["threshold"])
            except (TypeError, ValueError) as exc:
                raise FrameExtractionError(
                    f"scene_config.threshold must be numeric, "
                    f"got {scene_cfg['threshold']!r}"
                ) from exc
            if not 0.0 <= threshold <= 1.0:
                raise FrameExtractionError(
                    f"scene_config.threshold must be in [0.0, 1.0], "
                    f"got {threshold!r}"
                )

        if "min_gap_seconds" in scene_cfg:
            try:
                min_gap = float(scene_cfg["min_gap_seconds"])
            except (TypeError, ValueError) as exc:
                raise FrameExtractionError(
                    f"scene_config.min_gap_seconds must be numeric, "
                    f"got {scene_cfg['min_gap_seconds']!r}"
                ) from exc
            if min_gap < 0:
                raise FrameExtractionError(
                    f"scene_config.min_gap_seconds must be >= 0, got {min_gap!r}"
                )

        if mode == "hybrid" and "max_gap_seconds" in scene_cfg:
            try:
                max_gap = float(scene_cfg["max_gap_seconds"])
            except (TypeError, ValueError) as exc:
                raise FrameExtractionError(
                    f"scene_config.max_gap_seconds must be numeric, "
                    f"got {scene_cfg['max_gap_seconds']!r}"
                ) from exc
            if max_gap <= 0:
                raise FrameExtractionError(
                    f"scene_config.max_gap_seconds must be > 0, got {max_gap!r}"
                )

    return mode


def extract_frames(
    video_path: str,
    output_dir: str,
    interval_seconds: int = 2,
    cfg: dict | None = None,
) -> list:
    """Public entry point. Wraps :func:`_extract_frames_impl` in a
    boundary translator so that :class:`BinaryNotFoundError` and
    :class:`SubprocessError` raised by the shared
    ``src.common.subprocess_utils`` helpers surface as
    :class:`FrameExtractionError` — preserving the pre-Phase-2 public
    exception surface for every downstream caller.

    See :func:`_extract_frames_impl` for the full docstring.
    """
    try:
        return _extract_frames_impl(video_path, output_dir, interval_seconds, cfg)
    except (BinaryNotFoundError, SubprocessError) as exc:
        raise FrameExtractionError(str(exc)) from exc


def _extract_frames_impl(
    video_path: str,
    output_dir: str,
    interval_seconds: int = 2,
    cfg: dict | None = None,
) -> list:
    """
    Extract PNG frames from a video using the configured extraction mode.

    Parameters
    ----------
    video_path, output_dir, interval_seconds
        Same meaning as pre-v1.1. ``interval_seconds`` (positional) is
        reconciled into ``cfg['frame_interval_seconds']`` so strategy
        helpers can read a single source of truth.
    cfg
        Optional full pipeline config dict. When ``None`` or empty, behavior
        defaults to ``extraction_mode='interval'`` — byte-identical to the
        pre-v1.1 build (the D2 backward-compat contract).

    Returns list of dicts with: frame_path, frame_name, timestamp, frame_number
    """
    # Copy so we don't mutate the caller's dict.
    cfg = dict(cfg or {})
    # Positional interval_seconds wins over any stale cfg value so direct
    # callers (`extract_frames(v, o, 5)`) keep getting what they asked for.
    cfg["frame_interval_seconds"] = interval_seconds

    video = _validate_inputs(video_path, interval_seconds)
    mode = _validate_extraction_config(cfg)

    logger.info(
        "Frame extraction started | mode=%s | video=%r | interval=%ds | output_dir=%r",
        mode, str(video), interval_seconds, output_dir,
    )

    ffmpeg_bin = require_binary("ffmpeg")
    ffprobe_bin = require_binary("ffprobe")

    duration = _probe_video(video, ffprobe_bin)
    logger.debug("Video duration: %.2f s", duration)

    out_path = Path(output_dir).resolve()
    tmp_dir = out_path / ".tmp_extract"

    try:
        # Clean up stale temp files from any interrupted previous run
        if tmp_dir.exists():
            for stale in tmp_dir.iterdir():
                stale.unlink(missing_ok=True)
        out_path.mkdir(parents=True, exist_ok=True)
        tmp_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise FrameExtractionError(
            f"Cannot create output directory {str(out_path)!r}: {exc}"
        ) from exc

    extracted = _EXTRACTORS[mode](video, out_path, tmp_dir, ffmpeg_bin, duration, cfg)

    try:
        tmp_dir.rmdir()
    except OSError:
        pass

    logger.info("Extraction complete: %d frame(s) saved to %r", len(extracted), str(out_path))
    return extracted
