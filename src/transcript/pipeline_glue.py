"""
pipeline_glue.py
~~~~~~~~~~~~~~~~
Runs :func:`extract_audio` + :func:`transcribe` in a background thread
so the main pipeline (OCR / match / organize) proceeds in parallel.

**NEVER RAISES.** Every failure mode from the transcript degradation
matrix -- no audio stream, missing ffmpeg, missing whisper binary,
missing whisper model, whisper subprocess failure -- is caught here,
logged with a clear message, and swallowed. The pipeline gets ``None``
back and continues its non-transcript work unaffected.

Design notes
------------
* Single-worker :class:`ThreadPoolExecutor` (max_workers=1) because
  each pipeline invocation only ever kicks off ONE transcription; a
  full pool would just waste threads.
* Temporary WAV is written under the metadata dir and removed in a
  ``finally`` block regardless of outcome.
* The final ``transcript.json`` is the durable artifact -- the WAV
  is transient and never lingers past the transcription call.
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import List, Optional

from .audio_extractor import (
    AudioExtractionError,
    NoAudioStreamError,
    extract_audio,
)
from .whisper_transcriber import (
    Segment,
    WhisperFailureError,
    WhisperNotAvailableError,
    transcribe,
)

logger = logging.getLogger(__name__)

# One worker is enough -- each pipeline invocation kicks off exactly
# one transcription. Named prefix makes thread logs readable.
_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="whisper")


def _run_transcription(
    video_path: str,
    cfg: dict,
    metadata_dir: Path,
) -> Optional[List[Segment]]:
    """Body of the background task. Returns None on ANY graceful failure.

    Persists a `transcript.json` artifact to ``metadata_dir`` on success.
    """
    wav = metadata_dir / "_audio.wav"

    # --- Audio extraction ------------------------------------------------
    try:
        extract_audio(video_path, str(wav))
    except NoAudioStreamError as exc:
        logger.warning("Transcription skipped: %s", exc)
        return None
    except AudioExtractionError as exc:
        logger.warning("Transcription skipped (audio extraction failed): %s", exc)
        return None
    except Exception as exc:  # noqa: BLE001 -- last-line safety net
        # Should never happen -- extract_audio only raises the two above.
        # If it does, we still refuse to crash the pipeline.
        logger.warning("Transcription skipped (unexpected audio-extraction error): %s", exc)
        return None

    # --- Whisper transcription ------------------------------------------
    try:
        segments = transcribe(str(wav), cfg)
    except WhisperNotAvailableError as exc:
        logger.warning("Transcription skipped: %s", exc)
        return None
    except WhisperFailureError as exc:
        logger.warning("Transcription skipped (whisper failed): %s", exc)
        return None
    except Exception as exc:  # noqa: BLE001 -- last-line safety net
        logger.warning("Transcription skipped (unexpected whisper error): %s", exc)
        return None
    finally:
        # Always drop the transient WAV; transcript.json is the artifact.
        try:
            wav.unlink(missing_ok=True)
        except OSError:
            pass

    # --- Persist the transcript ------------------------------------------
    transcript_path = metadata_dir / "transcript.json"
    try:
        with open(transcript_path, "w", encoding="utf-8") as f:
            json.dump(
                [
                    {
                        "start": s.start,
                        "end": s.end,
                        "text": s.text,
                        "speaker": s.speaker,
                    }
                    for s in segments
                ],
                f,
                indent=2,
                ensure_ascii=False,  # keep Unicode readable (Hindi, punctuation)
            )
    except OSError as exc:
        # We got segments, we just couldn't persist. Return the segments
        # anyway so in-memory enrichment still works this run.
        logger.warning("Transcript saved in-memory only (write failed: %s)", exc)
        return segments

    logger.info(
        "Transcript saved: %s (%d segments)", transcript_path, len(segments)
    )
    return segments


def kickoff_transcription(
    video_path: str,
    cfg: dict,
    metadata_dir: Path,
) -> Optional[Future]:
    """Schedule transcription in a background thread.

    Parameters
    ----------
    video_path
        Path to the input video (any ffmpeg-readable format).
    cfg
        ``transcript_config`` sub-dict from the pipeline config.
        Must contain ``enabled: true`` for anything to happen.
        Other keys forwarded to :func:`transcribe`.
    metadata_dir
        Where the transient ``_audio.wav`` and durable
        ``transcript.json`` land.

    Returns
    -------
    concurrent.futures.Future | None
        A Future that resolves to ``list[Segment]`` or ``None``.
        Returns ``None`` immediately (no future) when
        ``transcript_config.enabled`` is not truthy -- caller can
        do ``if future is None: skip`` cleanly.
    """
    if not cfg.get("enabled", False):
        logger.info(
            "Transcription disabled (transcript_config.enabled=false); "
            "set to true to enable spoken-context attribution"
        )
        return None

    metadata_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Transcription kicked off in background thread")
    return _EXECUTOR.submit(_run_transcription, video_path, cfg, metadata_dir)
