"""
src.transcript
~~~~~~~~~~~~~~
Whisper-based transcription subsystem.

Public surface:

* :func:`extract_audio`               -- pull 16 kHz mono PCM WAV out of a video
* :func:`transcribe`                  -- run whisper.cpp on that WAV
* :class:`Segment`                    -- frozen dataclass, one transcription unit
* :class:`NoAudioStreamError`         -- audio-less video (graceful skip)
* :class:`AudioExtractionError`       -- ffmpeg audio-extraction failure
* :class:`WhisperNotAvailableError`   -- binary/model missing (graceful skip)
* :class:`WhisperFailureError`        -- whisper ran but output unusable
"""

from .audio_extractor import AudioExtractionError, NoAudioStreamError, extract_audio
from .whisper_transcriber import (
    Segment,
    WhisperFailureError,
    WhisperNotAvailableError,
    transcribe,
)

__all__ = [
    # audio
    "extract_audio",
    "AudioExtractionError",
    "NoAudioStreamError",
    # whisper
    "transcribe",
    "Segment",
    "WhisperNotAvailableError",
    "WhisperFailureError",
]
