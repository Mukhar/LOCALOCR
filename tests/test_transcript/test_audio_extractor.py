"""
tests/test_transcript/test_audio_extractor.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Unit tests for ``src.transcript.audio_extractor``. Mocks all
subprocess invocation — real ffmpeg wiring is exercised by the
manual smoke test recorded in the T3 commit.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.common.subprocess_utils import BinaryNotFoundError
from src.transcript import AudioExtractionError, NoAudioStreamError, extract_audio


# ---------------------------------------------------------------------------
# Fail-fast input validation
# ---------------------------------------------------------------------------
def test_extract_audio_missing_video_raises():
    with pytest.raises(AudioExtractionError) as excinfo:
        extract_audio("/nonexistent/path/video.mp4", "/tmp/out.wav")
    assert "Video not found" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Missing-binary propagation
# ---------------------------------------------------------------------------
def test_extract_audio_missing_ffmpeg_raises_binary_not_found(tmp_path):
    fake_video = tmp_path / "in.mp4"
    fake_video.write_bytes(b"")

    # First call to require_binary('ffmpeg') fails.
    with patch(
        "src.transcript.audio_extractor.require_binary",
        side_effect=BinaryNotFoundError("ffmpeg missing"),
    ):
        with pytest.raises(BinaryNotFoundError):
            extract_audio(str(fake_video), str(tmp_path / "out.wav"))


# ---------------------------------------------------------------------------
# No-audio-stream graceful signal
# ---------------------------------------------------------------------------
def test_extract_audio_no_audio_stream_raises(tmp_path):
    fake_video = tmp_path / "in.mp4"
    fake_video.write_bytes(b"")

    # require_binary returns a fake path; _has_audio_stream returns False.
    with patch(
        "src.transcript.audio_extractor.require_binary",
        side_effect=lambda name: f"/fake/{name}",
    ), patch(
        "src.transcript.audio_extractor._has_audio_stream",
        return_value=False,
    ):
        with pytest.raises(NoAudioStreamError) as excinfo:
            extract_audio(str(fake_video), str(tmp_path / "out.wav"))

    msg = str(excinfo.value)
    assert "No audio stream" in msg
    assert "skipping transcription" in msg


# ---------------------------------------------------------------------------
# Happy path — ffmpeg cmd shape
# ---------------------------------------------------------------------------
def test_extract_audio_ffmpeg_command_shape(tmp_path):
    """The assembled ffmpeg command must contain the whisper-required
    codec / sample-rate / channel flags."""
    fake_video = tmp_path / "in.mp4"
    fake_video.write_bytes(b"")
    out_path = tmp_path / "sub" / "out.wav"  # exercises parent-mkdir

    captured_cmds: list[list[str]] = []

    def _spy_run_subprocess(cmd, label, timeout, **kw):
        captured_cmds.append(cmd)
        return None  # ffmpeg call returns None (no capture flags)

    with patch(
        "src.transcript.audio_extractor.require_binary",
        side_effect=lambda name: f"/fake/{name}",
    ), patch(
        "src.transcript.audio_extractor._has_audio_stream",
        return_value=True,
    ), patch(
        "src.transcript.audio_extractor.run_subprocess",
        side_effect=_spy_run_subprocess,
    ):
        result = extract_audio(str(fake_video), str(out_path))

    assert captured_cmds, "run_subprocess was never called"
    cmd = captured_cmds[0]

    # Whisper-required flags:
    assert "-ar" in cmd and cmd[cmd.index("-ar") + 1] == "16000"
    assert "-ac" in cmd and cmd[cmd.index("-ac") + 1] == "1"
    assert "-acodec" in cmd and cmd[cmd.index("-acodec") + 1] == "pcm_s16le"

    # Video stream must be dropped:
    assert "-vn" in cmd

    # Overwrite flag (never surprise the caller with a leftover file):
    assert "-y" in cmd

    # Output path is the last positional arg:
    assert cmd[-1] == str(out_path.resolve())

    # Return value is the resolved output path:
    assert result == out_path.resolve()

    # Parent directory was created (mkdir(parents=True, exist_ok=True)):
    assert out_path.parent.is_dir()


# ---------------------------------------------------------------------------
# ffprobe audio-stream detection (via _has_audio_stream indirection)
# ---------------------------------------------------------------------------
def test_has_audio_stream_true_when_ffprobe_returns_streams(tmp_path):
    """Direct test of the private helper via its module namespace."""
    from src.transcript import audio_extractor

    fake_video = tmp_path / "in.mp4"
    fake_video.write_bytes(b"")

    ffprobe_json = json.dumps({"streams": [{"codec_type": "audio"}]})
    with patch(
        "src.transcript.audio_extractor.run_subprocess",
        return_value=ffprobe_json,
    ):
        assert audio_extractor._has_audio_stream(fake_video, "/fake/ffprobe") is True


def test_has_audio_stream_false_when_streams_empty(tmp_path):
    from src.transcript import audio_extractor

    fake_video = tmp_path / "in.mp4"
    fake_video.write_bytes(b"")

    with patch(
        "src.transcript.audio_extractor.run_subprocess",
        return_value=json.dumps({"streams": []}),
    ):
        assert audio_extractor._has_audio_stream(fake_video, "/fake/ffprobe") is False


def test_has_audio_stream_raises_on_bad_json(tmp_path):
    from src.transcript import audio_extractor

    fake_video = tmp_path / "in.mp4"
    fake_video.write_bytes(b"")

    with patch(
        "src.transcript.audio_extractor.run_subprocess",
        return_value="not json at all {",
    ):
        with pytest.raises(AudioExtractionError) as excinfo:
            audio_extractor._has_audio_stream(fake_video, "/fake/ffprobe")
    assert "Unexpected ffprobe output" in str(excinfo.value)
