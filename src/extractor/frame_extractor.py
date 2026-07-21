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

logger = logging.getLogger(__name__)


class FrameExtractionError(Exception):
    """Raised when frame extraction cannot be completed."""


def _require_binary(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise FrameExtractionError(
            f"{name!r} not found on PATH. Install it with: brew install ffmpeg"
        )
    return path


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


def _run_ffmpeg(cmd: list, video_label: str, timeout: int, *, capture_stderr: bool = False):
    """Run ffmpeg and raise on failure.

    Parameters
    ----------
    capture_stderr
        When True, return the (possibly-empty) stderr string on success.
        Needed by scene/hybrid modes that parse ``showinfo`` output.
        When False (default), returns ``None`` — preserves the pre-01-02
        contract for the interval-mode caller.
    """
    try:
        result = subprocess.run(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise FrameExtractionError(
            f"ffmpeg timed out after {timeout}s processing {video_label!r}"
        ) from exc
    except OSError as exc:
        raise FrameExtractionError(f"Failed to launch ffmpeg: {exc}") from exc

    if result.returncode != 0:
        raise FrameExtractionError(
            f"ffmpeg exited with code {result.returncode} for {video_label!r}.\n"
            f"stderr: {result.stderr.strip()}"
        )

    if capture_stderr:
        return result.stderr or ""
    return None


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
    _run_ffmpeg(cmd, str(video), timeout)

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


# Extraction-mode dispatch table. Plan 01-02 fills in ``scene`` and ``hybrid``
# entries. Adding a new mode is: (1) write a strategy function with the same
# ``(video, out_path, tmp_dir, ffmpeg_bin, duration, cfg) -> list[dict]``
# signature, (2) register it here.
_EXTRACTORS = {
    "interval": _extract_by_interval,
    # "scene":   TODO — plan 01-02
    # "hybrid":  TODO — plan 01-02
}


def _validate_extraction_config(cfg: dict) -> str:
    """
    Resolve and validate ``cfg['extraction_mode']``, returning the
    normalized (lower-cased) mode name.

    Fails fast per D6 when the mode is not a registered extractor. Mode-
    specific parameter validation (scene threshold, hybrid gaps, …) lives
    inside the individual strategy functions — plan 01-02 wires those up.
    """
    mode = str(cfg.get("extraction_mode", "interval")).lower()
    if mode not in _EXTRACTORS:
        raise FrameExtractionError(
            f"extraction_mode {mode!r} invalid. Must be one of: {sorted(_EXTRACTORS)}"
        )
    return mode


def extract_frames(
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

    ffmpeg_bin = _require_binary("ffmpeg")
    ffprobe_bin = _require_binary("ffprobe")

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
