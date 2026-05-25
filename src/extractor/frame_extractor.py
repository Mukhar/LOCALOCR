"""
frame_extractor.py
~~~~~~~~~~~~~~~~~~
Extract frames from an MP4 video at a configurable interval using ffmpeg.

Output naming convention:
    frame_NNNN_XXmYYs.png
    e.g. frame_0003_00m06s.png  — 3rd frame at 6 s into the video
"""

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

    if path.suffix.lower() != ".mp4":
        raise FrameExtractionError(
            f"Unsupported file extension {path.suffix!r}. Only .mp4 files are accepted."
        )

    return path


def _format_timestamp(total_seconds: int) -> str:
    minutes, seconds = divmod(total_seconds, 60)
    return f"{minutes:02d}m{seconds:02d}s"


def extract_frames(
    video_path: str,
    output_dir: str,
    interval_seconds: int = 2,
) -> list:
    """
    Extract PNG frames from an MP4 video at a fixed time interval.

    Returns list of dicts with: frame_path, frame_name, timestamp, frame_number
    """
    video = _validate_inputs(video_path, interval_seconds)

    logger.info(
        "Frame extraction started | video=%r | interval=%ds | output_dir=%r",
        str(video), interval_seconds, output_dir,
    )

    ffmpeg_bin = _require_binary("ffmpeg")
    ffprobe_bin = _require_binary("ffprobe")

    duration = _probe_video(video, ffprobe_bin)
    logger.debug("Video duration: %.2f s", duration)

    out_path = Path(output_dir).resolve()
    tmp_dir = out_path / ".tmp_extract"

    try:
        out_path.mkdir(parents=True, exist_ok=True)
        tmp_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise FrameExtractionError(
            f"Cannot create output directory {str(out_path)!r}: {exc}"
        ) from exc

    tmp_pattern = str(tmp_dir / "frame_%04d.png")

    cmd = [
        ffmpeg_bin,
        "-hide_banner",
        "-loglevel", "error",
        "-i", str(video),
        "-vf", f"fps=1/{interval_seconds}",
        "-vsync", "vfr",
        tmp_pattern,
    ]

    logger.debug("ffmpeg command: %s", " ".join(cmd))

    timeout = max(300, int(duration) * 3)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise FrameExtractionError(
            f"ffmpeg timed out after {timeout}s processing {str(video)!r}"
        ) from exc
    except OSError as exc:
        raise FrameExtractionError(f"Failed to launch ffmpeg: {exc}") from exc

    if result.returncode != 0:
        raise FrameExtractionError(
            f"ffmpeg exited with code {result.returncode} for {str(video)!r}.\n"
            f"stderr: {result.stderr.strip()}"
        )

    tmp_frames = sorted(tmp_dir.glob("frame_*.png"))

    if not tmp_frames:
        raise FrameExtractionError(
            f"ffmpeg completed but wrote no frames for {str(video)!r}."
        )

    logger.info("Renaming %d extracted frame(s)…", len(tmp_frames))

    _seq_re = re.compile(r"frame_(\d+)\.png$")
    extracted = []

    for tmp_file in tmp_frames:
        m = _seq_re.search(tmp_file.name)
        if not m:
            logger.warning("Ignoring unexpected file: %s", tmp_file.name)
            continue

        frame_number = int(m.group(1))
        ts_str = _format_timestamp((frame_number - 1) * interval_seconds)
        frame_name = f"frame_{frame_number:04d}_{ts_str}.png"
        final_path = out_path / frame_name

        try:
            tmp_file.rename(final_path)
        except OSError as exc:
            raise FrameExtractionError(
                f"Failed to move frame file: {exc}"
            ) from exc

        extracted.append({
            "frame_path": str(final_path),
            "frame_name": frame_name,
            "timestamp": ts_str,
            "frame_number": frame_number,
        })
        logger.debug("Saved %s", frame_name)

    try:
        tmp_dir.rmdir()
    except OSError:
        pass

    extracted.sort(key=lambda e: e["frame_number"])

    logger.info("Extraction complete: %d frame(s) saved to %r", len(extracted), str(out_path))
    return extracted
