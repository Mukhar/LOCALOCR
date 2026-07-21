"""
test_pipeline_integration.py
============================
Integration tests for the transcript degradation matrix. Every failure
mode from the plan MUST resolve the Future to ``None`` and log a clear
skip reason -- the main OCR pipeline is never allowed to crash because
transcription had an off day.

These tests mock the boundaries (``extract_audio`` and ``transcribe``)
so they don't need whisper.cpp or a real audio file installed.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from src.transcript import (
    Segment,
    enrich_ocr_results,
    kickoff_transcription,
)
from src.transcript.audio_extractor import (
    AudioExtractionError,
    NoAudioStreamError,
)
from src.transcript.whisper_transcriber import (
    WhisperFailureError,
    WhisperNotAvailableError,
)

# --- Shared helpers ---------------------------------------------------------

_GLUE_MODULE = "src.transcript.pipeline_glue"


def _patched_run(monkeypatch_extract=None, monkeypatch_transcribe=None):
    """Convenience patcher: monkeypatch extract_audio + transcribe inside
    the glue module (that's where the imported names live)."""
    patchers = []
    if monkeypatch_extract is not None:
        patchers.append(patch(f"{_GLUE_MODULE}.extract_audio", monkeypatch_extract))
    if monkeypatch_transcribe is not None:
        patchers.append(patch(f"{_GLUE_MODULE}.transcribe", monkeypatch_transcribe))
    return patchers


# --- Degradation-matrix tests -----------------------------------------------

def test_transcription_disabled_returns_none(tmp_path, caplog):
    """cfg.enabled=False -> None immediately, no thread spawned, no error."""
    with caplog.at_level(logging.INFO, logger=_GLUE_MODULE):
        future = kickoff_transcription("/x.mp4", {"enabled": False}, tmp_path)
    assert future is None
    assert "Transcription disabled" in caplog.text


def test_no_audio_stream_skips_gracefully(tmp_path, caplog):
    """Silent-film video -> NoAudioStreamError -> None + WARNING log."""
    def fake_extract(video, wav):
        raise NoAudioStreamError("no audio in this bad boi")

    with patch(f"{_GLUE_MODULE}.extract_audio", fake_extract):
        with caplog.at_level(logging.WARNING, logger=_GLUE_MODULE):
            fut = kickoff_transcription("/vid.mp4", {"enabled": True}, tmp_path)
            result = fut.result(timeout=10)
    assert result is None
    assert "Transcription skipped" in caplog.text
    assert "no audio in this bad boi" in caplog.text


def test_audio_extraction_failure_skips_gracefully(tmp_path, caplog):
    """ffmpeg blows up mid-extraction -> AudioExtractionError -> None + WARNING."""
    def fake_extract(video, wav):
        raise AudioExtractionError("ffmpeg went boom")

    with patch(f"{_GLUE_MODULE}.extract_audio", fake_extract):
        with caplog.at_level(logging.WARNING, logger=_GLUE_MODULE):
            fut = kickoff_transcription("/vid.mp4", {"enabled": True}, tmp_path)
            result = fut.result(timeout=10)
    assert result is None
    assert "audio extraction failed" in caplog.text
    assert "ffmpeg went boom" in caplog.text


def test_missing_whisper_binary_skips_gracefully(tmp_path, caplog):
    """No whisper-cli on PATH -> WhisperNotAvailableError -> None + brew hint."""
    def fake_extract(video, wav):
        # audio extraction succeeds; write an empty placeholder WAV
        Path(wav).write_bytes(b"RIFF")

    def fake_transcribe(audio, cfg):
        raise WhisperNotAvailableError(
            "whisper.cpp binary not found. Install: brew install whisper-cpp"
        )

    with patch(f"{_GLUE_MODULE}.extract_audio", fake_extract), \
         patch(f"{_GLUE_MODULE}.transcribe", fake_transcribe):
        with caplog.at_level(logging.WARNING, logger=_GLUE_MODULE):
            fut = kickoff_transcription("/vid.mp4", {"enabled": True}, tmp_path)
            result = fut.result(timeout=10)
    assert result is None
    assert "brew install whisper-cpp" in caplog.text
    # WAV was cleaned up in the finally block
    assert not (tmp_path / "_audio.wav").exists()


def test_missing_whisper_model_skips_gracefully(tmp_path, caplog):
    """Model file absent -> WhisperNotAvailableError with download hint."""
    def fake_extract(video, wav):
        Path(wav).write_bytes(b"RIFF")

    def fake_transcribe(audio, cfg):
        raise WhisperNotAvailableError(
            "whisper model not found. Run: bash "
            "~/.whisper.cpp/models/download-ggml-model.sh base.en"
        )

    with patch(f"{_GLUE_MODULE}.extract_audio", fake_extract), \
         patch(f"{_GLUE_MODULE}.transcribe", fake_transcribe):
        with caplog.at_level(logging.WARNING, logger=_GLUE_MODULE):
            fut = kickoff_transcription("/vid.mp4", {"enabled": True}, tmp_path)
            result = fut.result(timeout=10)
    assert result is None
    assert "download-ggml-model" in caplog.text


def test_whisper_crash_skips_gracefully(tmp_path, caplog):
    """whisper-cli exit != 0 -> WhisperFailureError -> None + WARNING."""
    def fake_extract(video, wav):
        Path(wav).write_bytes(b"RIFF")

    def fake_transcribe(audio, cfg):
        raise WhisperFailureError("whisper-cli exit code 1: segfault")

    with patch(f"{_GLUE_MODULE}.extract_audio", fake_extract), \
         patch(f"{_GLUE_MODULE}.transcribe", fake_transcribe):
        with caplog.at_level(logging.WARNING, logger=_GLUE_MODULE):
            fut = kickoff_transcription("/vid.mp4", {"enabled": True}, tmp_path)
            result = fut.result(timeout=10)
    assert result is None
    assert "whisper failed" in caplog.text
    assert "segfault" in caplog.text


def test_unexpected_exception_swallowed(tmp_path, caplog):
    """Even a random Exception can't crash the pipeline (last-line safety net)."""
    def fake_extract(video, wav):
        raise RuntimeError("cosmic ray flipped a bit")

    with patch(f"{_GLUE_MODULE}.extract_audio", fake_extract):
        with caplog.at_level(logging.WARNING, logger=_GLUE_MODULE):
            fut = kickoff_transcription("/vid.mp4", {"enabled": True}, tmp_path)
            result = fut.result(timeout=10)
    assert result is None
    assert "unexpected audio-extraction error" in caplog.text
    assert "cosmic ray" in caplog.text


# --- Happy-path test --------------------------------------------------------

def test_successful_transcription_persists_json(tmp_path, caplog):
    """extract_audio + transcribe both succeed -> transcript.json on disk."""
    def fake_extract(video, wav):
        Path(wav).write_bytes(b"RIFF fake wav")

    fake_segments = [
        Segment(start=0.0, end=3.5, text="Welcome back.", speaker=None),
        Segment(start=3.5, end=8.0, text="Top pick is Reliance.", speaker=None),
    ]

    def fake_transcribe(audio, cfg):
        return fake_segments

    with patch(f"{_GLUE_MODULE}.extract_audio", fake_extract), \
         patch(f"{_GLUE_MODULE}.transcribe", fake_transcribe):
        with caplog.at_level(logging.INFO, logger=_GLUE_MODULE):
            fut = kickoff_transcription("/vid.mp4", {"enabled": True}, tmp_path)
            result = fut.result(timeout=10)

    # In-memory result
    assert result == fake_segments

    # Persisted artifact
    transcript_path = tmp_path / "transcript.json"
    assert transcript_path.exists()
    persisted = json.loads(transcript_path.read_text(encoding="utf-8"))
    assert len(persisted) == 2
    assert persisted[0] == {"start": 0.0, "end": 3.5, "text": "Welcome back.",
                            "speaker": None}
    assert persisted[1]["text"] == "Top pick is Reliance."

    # Transient WAV cleaned up
    assert not (tmp_path / "_audio.wav").exists()

    # Success log
    assert "Transcript saved" in caplog.text
    assert "2 segments" in caplog.text


# --- Bonus: enrichment identity fence at the pipeline layer ------------------

def test_enrich_only_matched_frames_preserves_unmatched_identity():
    """
    Simulates what pipeline_runner does after transcription completes:
    calls enrich_ocr_results on the matched_results list. Unmatched
    entries MUST pass through by identity so callers can spot-check.
    """
    matched_1 = {"frame_name": "frame_0001_00m10s.png", "matched": True,
                 "matched_keywords": ["reliance"], "ocr_text": "Reliance target 2900"}
    matched_2 = {"frame_name": "frame_0002_00m20s.png", "matched": True,
                 "matched_keywords": ["reliance"], "ocr_text": "..."}
    matched_3 = {"frame_name": "frame_0003_00m30s.png", "matched": True,
                 "matched_keywords": ["tcs"], "ocr_text": "..."}
    unmatched_1 = {"frame_name": "frame_0100_01m40s.png", "matched": False,
                   "matched_keywords": [], "ocr_text": "boring content"}
    unmatched_2 = {"frame_name": "frame_0101_01m41s.png", "matched": False,
                   "matched_keywords": [], "ocr_text": "more boring content"}

    ocr_results = [matched_1, unmatched_1, matched_2, unmatched_2, matched_3]

    segments = [
        Segment(start=8.0, end=12.0, text="Top pick Reliance", speaker=None),
        Segment(start=25.0, end=32.0, text="Move to TCS", speaker=None),
    ]

    enriched = enrich_ocr_results(ocr_results, segments, window_seconds=8.0)

    # Same list length, matched ones have transcript_context, unmatched don't
    assert len(enriched) == 5
    assert "transcript_context" in enriched[0]
    assert "transcript_context" not in enriched[1]
    assert "transcript_context" in enriched[2]
    assert "transcript_context" not in enriched[3]
    assert "transcript_context" in enriched[4]

    # Unmatched entries pass through by identity (not copied)
    assert enriched[1] is unmatched_1
    assert enriched[3] is unmatched_2

    # Matched entries ARE new dicts (input never mutated)
    assert enriched[0] is not matched_1
    assert "transcript_context" not in matched_1  # input untouched


# --- Pipeline runner integration (metadata carries transcript_context) -------

def test_generate_metadata_carries_transcript_context(tmp_path):
    """_generate_metadata copies transcript_context onto entries when present."""
    from src.pipeline.pipeline_runner import _generate_metadata

    results = [
        {
            "frame_name": "frame_0010_00m20s.png",
            "timestamp": "00:00:20",
            "matched": True,
            "matched_keywords": ["reliance"],
            "ocr_text": "Reliance target 2900",
            "transcript_context": {
                "before": "Now for our top pick.",
                "at": "Reliance target 2900.",
                "after": "Stop loss 2750.",
                "speaker": None,
            },
        },
        {
            "frame_name": "frame_0100_03m20s.png",
            "timestamp": "00:03:20",
            "matched": False,  # no transcript_context on this one
            "matched_keywords": [],
            "ocr_text": "unrelated",
        },
    ]

    metadata_dir = tmp_path / "metadata"
    out_file = _generate_metadata(results, metadata_dir)
    persisted = json.loads(out_file.read_text(encoding="utf-8"))

    assert len(persisted) == 2
    # First entry: transcript_context preserved verbatim
    assert persisted[0]["transcript_context"]["at"] == "Reliance target 2900."
    # Second entry: no transcript_context key at all (optional field)
    assert "transcript_context" not in persisted[1]
