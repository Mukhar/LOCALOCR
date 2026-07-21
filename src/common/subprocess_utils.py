"""
subprocess_utils.py
~~~~~~~~~~~~~~~~~~~
Shared subprocess helpers used across LOCALOCR modules that shell out
to external binaries (ffmpeg, ffprobe, whisper-cli, ...).

Zen of Python: "There should be one -- and preferably only one --
obvious way to do it." Before this module, every subsystem that
needed to invoke a binary reimplemented its own require-binary +
run-subprocess pair. Consolidating here keeps error handling,
timeout semantics, and logging uniform.

Public API:
    require_binary(name)                       -> str  (path)
    run_subprocess(cmd, label, timeout, ...)   -> None | str | tuple[str, str]

    class BinaryNotFoundError(Exception)
    class SubprocessError(Exception)
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from typing import Optional, Tuple, Union

logger = logging.getLogger(__name__)


class BinaryNotFoundError(Exception):
    """Raised when a required external binary is not on PATH."""


class SubprocessError(Exception):
    """Raised when a subprocess call fails (timeout, launch failure, non-zero exit)."""


# Hints displayed alongside BinaryNotFoundError. Extend as new binaries land.
_INSTALL_HINTS = {
    "ffmpeg": "brew install ffmpeg",
    "ffprobe": "brew install ffmpeg  # ffprobe ships with ffmpeg",
    "whisper-cli": (
        "clone + build https://github.com/ggerganov/whisper.cpp "
        "and put the built `whisper-cli` binary on PATH"
    ),
}


def require_binary(name: str) -> str:
    """Resolve an external binary via ``shutil.which`` or raise a helpful error.

    Parameters
    ----------
    name
        Binary name to look up on PATH (e.g. ``"ffmpeg"``).

    Returns
    -------
    str
        Absolute path to the resolved binary.

    Raises
    ------
    BinaryNotFoundError
        If the binary is not on PATH. Message includes an install hint
        when the binary is one of the well-known LOCALOCR deps.
    """
    path = shutil.which(name)
    if path is None:
        hint = _INSTALL_HINTS.get(name, f"install '{name}' and put it on PATH")
        raise BinaryNotFoundError(f"{name!r} not found on PATH. Install it with: {hint}")
    return path


def run_subprocess(
    cmd: list,
    label: str,
    timeout: int,
    *,
    capture_stdout: bool = False,
    capture_stderr: bool = False,
) -> Union[None, str, Tuple[Optional[str], Optional[str]]]:
    """Run a subprocess with structured error handling.

    Never uses ``shell=True``. Timeouts, launch failures, and non-zero exit
    codes all raise ``SubprocessError`` with the binary path and ``label``
    included in the message for debuggability.

    Parameters
    ----------
    cmd
        Argv list, e.g. ``["ffmpeg", "-i", "in.mp4", ...]``.
    label
        Human-readable identifier for the call (typically the video path)
        included in error messages.
    timeout
        Seconds before the child is killed and ``SubprocessError`` raised.
    capture_stdout
        If True, capture and return the child's stdout.
    capture_stderr
        If True, capture and return the child's stderr.

    Returns
    -------
    None
        When neither capture flag is set.
    str
        The captured stream, when EXACTLY ONE of ``capture_stdout`` /
        ``capture_stderr`` is True. Kept as a plain string to preserve the
        pre-refactor ``_run_ffmpeg(..., capture_stderr=True)`` contract
        used by scene-mode showinfo parsing.
    tuple[str | None, str | None]
        ``(stdout, stderr)`` when BOTH capture flags are True.

    Raises
    ------
    SubprocessError
        On timeout, launch failure (OSError), or non-zero returncode.
        The message includes ``cmd[0]`` and ``label`` plus any captured
        stderr snippet.
    """
    stdout_pipe = subprocess.PIPE if capture_stdout else subprocess.DEVNULL
    stderr_pipe = subprocess.PIPE if capture_stderr else subprocess.DEVNULL

    try:
        result = subprocess.run(
            cmd,
            stdout=stdout_pipe,
            stderr=stderr_pipe,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise SubprocessError(
            f"{cmd[0]} timed out after {timeout}s processing {label!r}"
        ) from exc
    except OSError as exc:
        raise SubprocessError(f"Failed to launch {cmd[0]}: {exc}") from exc

    if result.returncode != 0:
        stderr_snippet = (result.stderr or "").strip() if capture_stderr else "(stderr not captured)"
        raise SubprocessError(
            f"{cmd[0]} exited with code {result.returncode} for {label!r}.\n"
            f"stderr: {stderr_snippet}"
        )

    if capture_stdout and capture_stderr:
        return (result.stdout or "", result.stderr or "")
    if capture_stdout:
        return result.stdout or ""
    if capture_stderr:
        return result.stderr or ""
    return None
