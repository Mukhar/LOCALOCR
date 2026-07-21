"""
whisper_transcriber.py
~~~~~~~~~~~~~~~~~~~~~~
Subprocess wrapper around ``whisper.cpp``'s ``whisper-cli`` binary.
Runs fully local transcription and returns normalized
:class:`Segment` records (immutable dataclasses).

Design notes
------------
* :class:`Segment` is ``frozen=True`` so downstream consumers can't
  mutate our transcription state by accident.
* Two failure modes are distinct types:
  - :class:`WhisperNotAvailableError` -- binary or model missing.
    Callers should DEGRADE (log + skip transcription).
  - :class:`WhisperFailureError` -- whisper ran but produced no /
    unreadable output. Callers may retry or surface.
* Binary discovery tries the caller-configured name first, then
  the historical whisper.cpp aliases (``whisper-cli``, ``main``,
  ``whisper``) so brew, source builds, and future rename all work.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from src.common.subprocess_utils import (
    BinaryNotFoundError,
    SubprocessError,
    require_binary,
    run_subprocess,
)

logger = logging.getLogger(__name__)


class WhisperNotAvailableError(Exception):
    """Whisper binary or model missing; caller should skip transcription."""


class WhisperFailureError(Exception):
    """Whisper ran but failed to produce usable output."""


@dataclass(frozen=True)
class Segment:
    """Normalized whisper transcription segment.

    Times are in **seconds** (float). Whisper.cpp reports milliseconds
    in the JSON ``offsets`` block; we normalize here so downstream code
    can compare against frame timestamps in one unit.
    """

    start: float
    end: float
    text: str
    speaker: Optional[str] = None


# Try these binary names in order when no explicit binary is configured.
# `whisper-cli` is the modern brew build; `main` was the pre-1.6 name
# for the same binary; `whisper` covers hand-built symlinks.
_DEFAULT_BINARY_CANDIDATES = ("whisper-cli", "main", "whisper")

# Safety cap on the ffmpeg->whisper subprocess. `base.en` on M-series
# runs 5-10x realtime, so 1 h of audio finishes well under 10 minutes.
# 1 h cap is a paranoid ceiling to catch a wedged process rather than
# a realistic runtime bound.
_TRANSCRIBE_TIMEOUT_SECONDS = 3600


def _locate_binary(configured: Optional[str]) -> str:
    """Resolve the whisper binary. Configured name wins; then fall back."""
    candidates: List[str] = []
    if configured:
        candidates.append(configured)
    candidates.extend(c for c in _DEFAULT_BINARY_CANDIDATES if c not in candidates)

    for name in candidates:
        try:
            return require_binary(name)
        except BinaryNotFoundError:
            continue

    raise WhisperNotAvailableError(
        "whisper.cpp binary not found. Install via `brew install whisper-cpp` "
        "and set transcript_config.binary if your binary uses a non-standard name."
    )


def _locate_model(cfg: dict) -> Path:
    """Return the resolved model .bin path or raise WhisperNotAvailableError."""
    model_dir = Path(cfg.get("model_dir", "~/.whisper.cpp/models")).expanduser()
    model_name = cfg.get("model", "base.en")
    candidate = model_dir / f"ggml-{model_name}.bin"
    if not candidate.is_file():
        raise WhisperNotAvailableError(
            f"whisper model not found: {candidate}. "
            f"Download via: bash download-ggml-model.sh {model_name}"
        )
    return candidate


def _parse_whisper_json(json_path: Path) -> List[Segment]:
    """Parse whisper.cpp's ``-oj`` JSON output into Segments.

    - Times converted from ms to seconds.
    - Empty-text segments are dropped (whisper emits these for pure-silence gaps).
    - Segments sorted by start time (whisper's output is already ordered,
      but sorting defensively costs nothing on a few-hundred-item list).
    """
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    raw_segments = data.get("transcription", [])
    segments: List[Segment] = []
    for s in raw_segments:
        offsets = s.get("offsets", {}) or {}
        start_ms = offsets.get("from", 0)
        end_ms = offsets.get("to", 0)
        text = (s.get("text") or "").strip()
        if not text:
            continue
        segments.append(
            Segment(
                start=start_ms / 1000.0,
                end=end_ms / 1000.0,
                text=text,
                speaker=None,  # whisper.cpp doesn't do speaker diarization
            )
        )
    segments.sort(key=lambda seg: seg.start)
    return segments


def transcribe(audio_path: str, cfg: dict) -> List[Segment]:
    """Transcribe a WAV file with whisper.cpp and return normalized Segments.

    Parameters
    ----------
    audio_path
        Path to a 16 kHz mono PCM WAV (as produced by
        :func:`src.transcript.audio_extractor.extract_audio`).
    cfg
        ``transcript_config`` dict from the pipeline config. Read keys:
          - ``binary`` (str, optional)   - override binary name
          - ``model`` (str, default 'base.en')
          - ``model_dir`` (str, default '~/.whisper.cpp/models')
          - ``language`` (str, default 'en')
          - ``threads`` (int, default 8)

    Returns
    -------
    list[Segment]
        Sorted by start time. Empty list is a valid return
        (silent audio, no speech detected).

    Raises
    ------
    WhisperNotAvailableError
        Binary or model missing. Callers should degrade gracefully.
    WhisperFailureError
        Subprocess ran but output is missing / unreadable.
    """
    audio = Path(audio_path).resolve()
    if not audio.is_file():
        raise WhisperFailureError(f"Audio file not found: {audio}")

    binary = _locate_binary(cfg.get("binary"))
    model = _locate_model(cfg)
    language = cfg.get("language", "en")
    threads = int(cfg.get("threads", 8))

    output_prefix = audio.with_suffix("")  # /tmp/x.wav -> /tmp/x
    json_out = Path(str(output_prefix) + ".json")

    cmd = [
        binary,
        "-m", str(model),
        "-f", str(audio),
        "-oj",                       # emit JSON
        "-of", str(output_prefix),   # output-file base name (no extension)
        "-l", language,
        "-t", str(threads),
    ]

    try:
        run_subprocess(cmd, str(audio), timeout=_TRANSCRIBE_TIMEOUT_SECONDS)
    except SubprocessError as exc:
        raise WhisperFailureError(f"whisper-cli failed: {exc}") from exc

    if not json_out.is_file():
        raise WhisperFailureError(
            f"whisper produced no JSON output at {json_out}"
        )

    segments = _parse_whisper_json(json_out)
    logger.info(
        "Transcribed %s -> %d segment(s) via %s / %s",
        audio.name, len(segments), Path(binary).name, model.name,
    )
    return segments
