"""
tests/test_transcript/test_whisper_transcriber.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Unit tests for the whisper.cpp wrapper. Everything is mocked -- no
real ``whisper-cli`` binary or model file is required to run.

Real end-to-end integration lands in plan 02-04.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src.common.subprocess_utils import BinaryNotFoundError
from src.transcript import (
    Segment,
    WhisperFailureError,
    WhisperNotAvailableError,
    transcribe,
)
from src.transcript.whisper_transcriber import (
    _locate_binary,
    _locate_model,
    _parse_whisper_json,
)

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "whisper_output.json"


# ---------------------------------------------------------------------------
# _parse_whisper_json
# ---------------------------------------------------------------------------
def test_parse_whisper_json_normalizes_segments():
    segs = _parse_whisper_json(_FIXTURE)
    assert len(segs) == 3

    # ms -> s conversion sanity
    assert segs[0].start == 0.0
    assert segs[0].end == pytest.approx(3.24)
    assert segs[1].start == pytest.approx(3.24)
    assert segs[1].end == pytest.approx(8.5)
    assert segs[2].start == pytest.approx(8.5)
    assert segs[2].end == pytest.approx(12.0)

    # Text is stripped
    assert segs[0].text == "Welcome back to Zee Business."
    assert "Reliance" in segs[1].text
    assert "Stop loss" in segs[2].text

    # speaker None everywhere (whisper.cpp doesn't diarize)
    assert all(s.speaker is None for s in segs)


def test_parse_whisper_json_skips_empty_text(tmp_path):
    """Empty-text segments (silence gaps) must be filtered out."""
    original = json.loads(_FIXTURE.read_text())
    original["transcription"].insert(1, {
        "timestamps": {"from": "00:00:03,000", "to": "00:00:03,100"},
        "offsets": {"from": 3000, "to": 3100},
        "text": "   ",  # whitespace-only -> should be dropped
    })
    original["transcription"].append({
        "timestamps": {"from": "00:00:15,000", "to": "00:00:16,000"},
        "offsets": {"from": 15000, "to": 16000},
        "text": "",  # empty -> dropped
    })
    tampered = tmp_path / "tampered.json"
    tampered.write_text(json.dumps(original))

    segs = _parse_whisper_json(tampered)
    assert len(segs) == 3  # still 3, silence-only segments filtered


def test_parse_whisper_json_sorts_by_start(tmp_path):
    """Segments are sorted defensively even if the file is out of order."""
    scrambled = {
        "transcription": [
            {"offsets": {"from": 8500, "to": 12000}, "text": "third"},
            {"offsets": {"from": 0, "to": 3240}, "text": "first"},
            {"offsets": {"from": 3240, "to": 8500}, "text": "second"},
        ]
    }
    p = tmp_path / "scrambled.json"
    p.write_text(json.dumps(scrambled))
    segs = _parse_whisper_json(p)
    assert [s.text for s in segs] == ["first", "second", "third"]


# ---------------------------------------------------------------------------
# _locate_binary
# ---------------------------------------------------------------------------
def test_locate_binary_configured_wins():
    """When cfg['binary'] resolves, it's returned even if canonical names exist."""
    calls = []

    def _fake_require(name):
        calls.append(name)
        if name == "my-custom-whisper":
            return "/opt/local/my-custom-whisper"
        raise BinaryNotFoundError(name)

    with patch("src.transcript.whisper_transcriber.require_binary", side_effect=_fake_require):
        assert _locate_binary("my-custom-whisper") == "/opt/local/my-custom-whisper"
    assert calls == ["my-custom-whisper"], "configured binary must be tried FIRST and short-circuit"


def test_locate_binary_falls_back_through_aliases():
    def _fake_require(name):
        if name == "whisper":  # only the last alias resolves
            return "/usr/local/bin/whisper"
        raise BinaryNotFoundError(name)

    with patch("src.transcript.whisper_transcriber.require_binary", side_effect=_fake_require):
        assert _locate_binary(None) == "/usr/local/bin/whisper"


def test_locate_binary_missing_raises_helpful():
    with patch(
        "src.transcript.whisper_transcriber.require_binary",
        side_effect=BinaryNotFoundError("none"),
    ):
        with pytest.raises(WhisperNotAvailableError) as excinfo:
            _locate_binary(None)
    msg = str(excinfo.value)
    assert "brew install whisper-cpp" in msg
    assert "transcript_config.binary" in msg


# ---------------------------------------------------------------------------
# _locate_model
# ---------------------------------------------------------------------------
def test_locate_model_found(tmp_path):
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    (model_dir / "ggml-base.en.bin").write_bytes(b"stub")

    result = _locate_model({"model_dir": str(model_dir), "model": "base.en"})
    assert result == model_dir / "ggml-base.en.bin"


def test_locate_model_missing_raises_helpful(tmp_path):
    # Empty dir -> ggml-base.en.bin absent
    with pytest.raises(WhisperNotAvailableError) as excinfo:
        _locate_model({"model_dir": str(tmp_path), "model": "base.en"})
    msg = str(excinfo.value)
    assert "download-ggml-model.sh" in msg
    assert "base.en" in msg


def test_locate_model_defaults_to_base_en(tmp_path, monkeypatch):
    """Default model name is base.en."""
    fake_home = tmp_path
    monkeypatch.setenv("HOME", str(fake_home))
    # No model file created -> should raise, but the message will name base.en
    with pytest.raises(WhisperNotAvailableError) as excinfo:
        _locate_model({})
    assert "base.en" in str(excinfo.value)


# ---------------------------------------------------------------------------
# transcribe (integration of the pieces, all subprocess mocked)
# ---------------------------------------------------------------------------
def test_transcribe_missing_audio_raises_failure(tmp_path):
    with pytest.raises(WhisperFailureError) as excinfo:
        transcribe(str(tmp_path / "nope.wav"), {})
    assert "Audio file not found" in str(excinfo.value)


def test_transcribe_command_shape(tmp_path):
    """Verify the assembled whisper-cli command flags."""
    audio = tmp_path / "test.wav"
    audio.write_bytes(b"RIFF")  # existence is all we check

    model_dir = tmp_path / "models"
    model_dir.mkdir()
    model_file = model_dir / "ggml-base.en.bin"
    model_file.write_bytes(b"stub")

    # Simulate the JSON output whisper would write
    json_out = tmp_path / "test.json"

    captured: dict = {}

    def _spy_run(cmd, label, timeout, **kw):
        captured["cmd"] = cmd
        captured["timeout"] = timeout
        # Create the expected output file so transcribe() doesn't raise
        json_out.write_text(json.dumps({"transcription": []}))
        return None

    with patch(
        "src.transcript.whisper_transcriber.require_binary",
        return_value="/fake/whisper-cli",
    ), patch(
        "src.transcript.whisper_transcriber.run_subprocess",
        side_effect=_spy_run,
    ):
        segs = transcribe(
            str(audio),
            {
                "model_dir": str(model_dir),
                "model": "base.en",
                "language": "en",
                "threads": 4,
            },
        )

    cmd = captured["cmd"]
    assert cmd[0] == "/fake/whisper-cli"
    assert "-oj" in cmd  # JSON output flag
    assert "-l" in cmd and cmd[cmd.index("-l") + 1] == "en"
    assert "-t" in cmd and cmd[cmd.index("-t") + 1] == "4"
    assert "-m" in cmd and cmd[cmd.index("-m") + 1] == str(model_file)
    assert "-f" in cmd and cmd[cmd.index("-f") + 1] == str(audio.resolve())
    # Empty transcription -> empty segment list (not an error)
    assert segs == []


def test_transcribe_missing_json_output_raises_failure(tmp_path):
    """If whisper 'succeeds' but never writes JSON, we surface a WhisperFailureError."""
    audio = tmp_path / "test.wav"
    audio.write_bytes(b"RIFF")

    model_dir = tmp_path / "models"
    model_dir.mkdir()
    (model_dir / "ggml-base.en.bin").write_bytes(b"stub")

    with patch(
        "src.transcript.whisper_transcriber.require_binary",
        return_value="/fake/whisper-cli",
    ), patch(
        "src.transcript.whisper_transcriber.run_subprocess",
        return_value=None,  # subprocess "succeeds", but writes no JSON
    ):
        with pytest.raises(WhisperFailureError) as excinfo:
            transcribe(str(audio), {"model_dir": str(model_dir)})
    assert "no JSON output" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Segment immutability fence
# ---------------------------------------------------------------------------
def test_segment_is_frozen():
    """Segment must be frozen so downstream code can't mutate transcription state."""
    s = Segment(start=0.0, end=1.0, text="hi")
    with pytest.raises(Exception):  # FrozenInstanceError
        s.text = "mutated"  # type: ignore[misc]
