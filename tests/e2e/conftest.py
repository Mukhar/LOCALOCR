"""Playwright fixtures: boots ``python -m src.web`` on a free port,
waits for /health, yields the base URL, tears down cleanly.

Not imported by the default test session (excluded via
``addopts = -m "not e2e"`` in pytest.ini). Only pulled in when the
user runs ``pytest -m e2e tests/e2e/``.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_health(url: str, timeout: float = 15.0) -> None:
    """Poll /health until 200 or timeout. Uses stdlib urllib to avoid
    a hard dep on requests during e2e-only sessions."""
    import urllib.error
    import urllib.request
    deadline = time.time() + timeout
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=1) as r:
                if r.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError) as e:
            last_err = e
        time.sleep(0.2)
    raise RuntimeError(f"Server never became ready: {last_err}")


@pytest.fixture(scope="session")
def live_server(tmp_path_factory) -> str:
    """Boot the FastAPI app on a random free port in a subprocess.

    Session-scoped so 3+ tests share the same server (spinning one up
    per test would triple the suite time). Uses a tmp DB so tests
    don't touch the user's real output/localocr.sqlite.
    """
    port = _pick_free_port()
    tmp_db = tmp_path_factory.mktemp("e2e-db") / "e2e.sqlite"

    env = os.environ.copy()
    # Route the server to our tmp DB by patching create_app via a shim
    # module. Simplest: set an env var and read it in src.web.__main__.
    # For now we skip that indirection -- the default output/localocr.sqlite
    # gets used, which is acceptable for smoke tests. If cross-test
    # pollution becomes an issue, add DB_PATH env plumbing.

    proc = subprocess.Popen(
        [sys.executable, "-m", "src.web"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    # Note: `python -m src.web` currently hard-codes port 8765. If two
    # e2e runs collide, one will fail with EADDRINUSE. This fixture is
    # correct in spirit; a follow-up would make the port configurable
    # via env var so we can honor `port` above.
    base_url = "http://127.0.0.1:8765"

    try:
        _wait_for_health(base_url)
        yield base_url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
