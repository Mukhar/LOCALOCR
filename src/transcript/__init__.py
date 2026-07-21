"""
src.transcript
~~~~~~~~~~~~~~
Whisper-based transcription subsystem. Currently exposes:

* :func:`extract_audio` — pull 16 kHz mono PCM WAV out of a video
* :class:`NoAudioStreamError` — degrade-gracefully signal for
  audio-less videos
* :class:`AudioExtractionError` — hard failures
"""

from .audio_extractor import AudioExtractionError, NoAudioStreamError, extract_audio

__all__ = ["extract_audio", "NoAudioStreamError", "AudioExtractionError"]
