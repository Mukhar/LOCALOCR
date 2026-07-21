"""
tests/test_common/test_subprocess_utils.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Unit tests for the shared ``src.common.subprocess_utils`` helpers.

Every test mocks ``subprocess.run`` / ``shutil.which`` — no real
binary is invoked here. Real end-to-end wiring is exercised elsewhere
(the frame_extractor + audio_extractor tests plus manual smoke).
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from src.common.subprocess_utils import (
    BinaryNotFoundError,
    SubprocessError,
    require_binary,
    run_subprocess,
)


# ---------------------------------------------------------------------------
# require_binary
# ---------------------------------------------------------------------------
def test_require_binary_returns_path_for_existing():
    with patch("src.common.subprocess_utils.shutil.which", return_value="/usr/bin/ffmpeg"):
        assert require_binary("ffmpeg") == "/usr/bin/ffmpeg"


def test_require_binary_raises_for_missing():
    with patch("src.common.subprocess_utils.shutil.which", return_value=None):
        with pytest.raises(BinaryNotFoundError) as excinfo:
            require_binary("ffmpeg")
    msg = str(excinfo.value)
    assert "ffmpeg" in msg
    assert "brew install ffmpeg" in msg  # curated hint for known binary


def test_require_binary_unknown_binary_uses_generic_hint():
    with patch("src.common.subprocess_utils.shutil.which", return_value=None):
        with pytest.raises(BinaryNotFoundError) as excinfo:
            require_binary("some-obscure-tool")
    msg = str(excinfo.value)
    assert "some-obscure-tool" in msg
    assert "put it on PATH" in msg  # generic fallback hint


def test_require_binary_whisper_cli_has_curated_hint():
    """whisper-cli is a known LOCALOCR dep — must ship an install hint."""
    with patch("src.common.subprocess_utils.shutil.which", return_value=None):
        with pytest.raises(BinaryNotFoundError) as excinfo:
            require_binary("whisper-cli")
    assert "whisper.cpp" in str(excinfo.value)


# ---------------------------------------------------------------------------
# run_subprocess — happy paths
# ---------------------------------------------------------------------------
def _mock_completed(returncode=0, stdout="", stderr=""):
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


def test_run_subprocess_no_capture_returns_none():
    with patch("src.common.subprocess_utils.subprocess.run", return_value=_mock_completed()):
        assert run_subprocess(["fake"], "x", 10) is None


def test_run_subprocess_capture_stdout_returns_stdout_string():
    with patch(
        "src.common.subprocess_utils.subprocess.run",
        return_value=_mock_completed(stdout="hello\n"),
    ):
        out = run_subprocess(["fake"], "x", 10, capture_stdout=True)
    assert out == "hello\n"


def test_run_subprocess_capture_stderr_returns_stderr_string():
    """Backward-compat with the pre-Phase-2 _run_ffmpeg(..., capture_stderr=True) shape."""
    with patch(
        "src.common.subprocess_utils.subprocess.run",
        return_value=_mock_completed(stderr="showinfo pts_time:1.0"),
    ):
        out = run_subprocess(["fake"], "x", 10, capture_stderr=True)
    assert out == "showinfo pts_time:1.0"


def test_run_subprocess_capture_both_returns_tuple():
    with patch(
        "src.common.subprocess_utils.subprocess.run",
        return_value=_mock_completed(stdout="OUT", stderr="ERR"),
    ):
        out = run_subprocess(["fake"], "x", 10, capture_stdout=True, capture_stderr=True)
    assert out == ("OUT", "ERR")


# ---------------------------------------------------------------------------
# run_subprocess — failure modes
# ---------------------------------------------------------------------------
def test_run_subprocess_timeout_raises_subprocess_error():
    with patch(
        "src.common.subprocess_utils.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd=["fake"], timeout=5),
    ):
        with pytest.raises(SubprocessError) as excinfo:
            run_subprocess(["fake"], "myvideo.mp4", 5)
    msg = str(excinfo.value)
    assert "timed out" in msg
    assert "myvideo.mp4" in msg
    assert "5s" in msg


def test_run_subprocess_launch_failure_raises_subprocess_error():
    with patch(
        "src.common.subprocess_utils.subprocess.run",
        side_effect=OSError("permission denied"),
    ):
        with pytest.raises(SubprocessError) as excinfo:
            run_subprocess(["fake"], "x", 10)
    assert "Failed to launch fake" in str(excinfo.value)


def test_run_subprocess_nonzero_returncode_includes_stderr_when_captured():
    with patch(
        "src.common.subprocess_utils.subprocess.run",
        return_value=_mock_completed(returncode=1, stderr="ffmpeg: bad codec"),
    ):
        with pytest.raises(SubprocessError) as excinfo:
            run_subprocess(["fake"], "x", 10, capture_stderr=True)
    msg = str(excinfo.value)
    assert "exited with code 1" in msg
    assert "ffmpeg: bad codec" in msg


def test_run_subprocess_nonzero_returncode_without_capture_notes_absent_stderr():
    """Error message must be honest that stderr wasn't captured (debuggability)."""
    with patch(
        "src.common.subprocess_utils.subprocess.run",
        return_value=_mock_completed(returncode=2),
    ):
        with pytest.raises(SubprocessError) as excinfo:
            run_subprocess(["fake"], "x", 10)  # no capture_stderr
    assert "stderr not captured" in str(excinfo.value)


def test_run_subprocess_never_uses_shell_true():
    """No shell=True anywhere — security rule."""
    captured_kwargs = {}

    def _spy(*args, **kw):
        captured_kwargs.update(kw)
        return _mock_completed()

    with patch("src.common.subprocess_utils.subprocess.run", side_effect=_spy):
        run_subprocess(["fake"], "x", 10)

    assert captured_kwargs.get("shell", False) is False, (
        "run_subprocess must never invoke subprocess.run(shell=True)"
    )
