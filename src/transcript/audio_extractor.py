"""
audio_extractor.py
~~~~~~~~~~~~~~~~~~
Extract a 16 kHz mono PCM WAV audio track from a video for downstream
whisper.cpp transcription. Uses ffmpeg — already a required LOCALOCR
dependency — via the shared ``src.common.subprocess_utils`` helpers,
so require-binary / run-subprocess semantics stay uniform across the
project.

Public API:
    extract_audio(video_path, output_wav_path) -> Path
    class NoAudioStreamError(AudioExtractionError)
    class AudioExtractionError(Exception)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from src.common.subprocess_utils import (
    BinaryNotFoundError,
    SubprocessError,
    require_binary,
    run_subprocess,
)

logger = logging.getLogger(__name__)

# whisper.cpp expects 16 kHz mono PCM_S16LE.
_WHISPER_SAMPLE_RATE = "16000"
_WHISPER_CHANNELS = "1"
_WHISPER_CODEC = "pcm_s16le"


class AudioExtractionError(Exception):
    """Raised when audio extraction cannot be completed."""


class NoAudioStreamError(AudioExtractionError):
    """Raised when the source video contains no audio streams at all.

    Distinct from :class:`AudioExtractionError` so callers can degrade
    gracefully (log + skip transcription) rather than treat this as a
    hard failure.
    """


def _has_audio_stream(video: Path, ffprobe_bin: str) -> bool:
    """Return True iff the video has at least one audio stream.

    Uses ffprobe's JSON output over ``-select_streams a`` and reads the
    resulting ``streams`` array. Safer than parsing free-form stderr.
    """
    cmd = [
        ffprobe_bin,
        "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        "-select_streams", "a",
        str(video),
    ]
    stdout = run_subprocess(cmd, str(video), timeout=30, capture_stdout=True)
    try:
        data = json.loads(stdout or "{}")
    except json.JSONDecodeError as exc:
        raise AudioExtractionError(
            f"Unexpected ffprobe output while probing audio streams of {str(video)!r}"
        ) from exc
    return bool(data.get("streams"))


def extract_audio(video_path: str, output_wav_path: str) -> Path:
    """Extract the video's audio track to a 16 kHz mono PCM WAV.

    Parameters
    ----------
    video_path
        Path to the source video (any ffmpeg-readable format).
    output_wav_path
        Destination WAV path. Parent directories are created as needed.
        Any existing file at this path is overwritten (``-y``).

    Returns
    -------
    pathlib.Path
        Resolved absolute path of the produced WAV file.

    Raises
    ------
    AudioExtractionError
        Video not found, or ffmpeg failed for any other reason.
    NoAudioStreamError
        Video is readable but has zero audio streams.
    BinaryNotFoundError
        ffmpeg or ffprobe missing from PATH.
    """
    video = Path(video_path).resolve()
    if not video.is_file():
        raise AudioExtractionError(f"Video not found: {video}")

    try:
        ffmpeg_bin = require_binary("ffmpeg")
        ffprobe_bin = require_binary("ffprobe")
    except BinaryNotFoundError:
        # Re-raise unchanged — callers can inspect this distinct type.
        raise

    if not _has_audio_stream(video, ffprobe_bin):
        raise NoAudioStreamError(
            f"No audio stream in {video} — skipping transcription"
        )

    out = Path(output_wav_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        ffmpeg_bin,
        "-hide_banner", "-loglevel", "error",
        "-y",                       # overwrite existing
        "-i", str(video),
        "-vn",                      # drop video stream
        "-acodec", _WHISPER_CODEC,  # pcm_s16le
        "-ar", _WHISPER_SAMPLE_RATE,  # 16 kHz
        "-ac", _WHISPER_CHANNELS,   # mono
        str(out),
    ]
    try:
        run_subprocess(cmd, str(video), timeout=600)
    except SubprocessError as exc:
        raise AudioExtractionError(str(exc)) from exc

    logger.info("Extracted audio (16kHz mono PCM): %s", out)
    return out
