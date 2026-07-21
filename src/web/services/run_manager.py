"""Subprocess-based pipeline run manager.

Design:
  - Each pipeline run is a FRESH Python subprocess. Rationale:
      (a) Isolation -- an OCR/whisper/Ollama import that leaks state
          (PyObjC, torch, etc.) can't corrupt the web server.
      (b) Cancellation -- a subprocess we can SIGTERM cleanly beats an
          in-process thread we can't interrupt through native code.
      (c) Concurrency budget -- we cap max_concurrent runs to protect
          the machine (OCR is CPU/GPU heavy, whisper eats RAM).
  - Communication is one-way: parent -> child via argv (config JSON),
    child -> parent via structured JSON stdout events.
  - Every event flows to TWO places:
      1) the event bus (fan-out to any live SSE subscribers)
      2) the DB (lifecycle transitions: start / summary / done get
         persisted so a page reload rejoins the last known state)

Concurrency:
  - ``max_concurrent`` defaults to 1. Extra ``start_run`` calls enqueue
    the run and emit a ``queued`` event immediately (so the UI can show
    "position N in queue"). When an active run finishes, the pump thread
    calls ``_drain_pending`` which spawns the next queued run.
  - Everything mutating ``_active`` / ``_pending`` runs inside a
    ``threading.Lock`` so the pump thread and the request thread never
    race on the concurrency counter.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
from pathlib import Path
from queue import Empty, Queue
from typing import Dict, Optional, Tuple

from .db import finalize_run, insert_run
from .event_bus import event_bus

logger = logging.getLogger(__name__)


class RunManager:
    """Spawns and supervises pipeline subprocesses."""

    def __init__(self, db_path: Path, max_concurrent: int = 1):
        self.db_path = db_path
        self.max_concurrent = max_concurrent
        self._active: Dict[int, subprocess.Popen] = {}
        self._pending: "Queue[Tuple[int, dict]]" = Queue()
        self._lock = threading.Lock()

    # --- Public API --------------------------------------------------------

    def start_run(self, config: dict) -> int:
        """Insert a DB row, then either spawn immediately or enqueue.

        Returns the newly-created run_id in both cases so the caller
        (a FastAPI route) can redirect to the run-detail page without
        waiting for the pipeline to start.
        """
        video_path = config.get("video_path", "")
        run_id = insert_run(self.db_path, video_path, config)

        with self._lock:
            at_capacity = len(self._active) >= self.max_concurrent
            if at_capacity:
                self._pending.put((run_id, config))
                position = self._pending.qsize()
        if at_capacity:
            # Publish OUTSIDE the lock (event_bus.publish is fire-and-
            # forget but we still don't want to hold the lock across it).
            event_bus.publish(run_id, {
                "type": "queued", "queued_position": position,
            })
            return run_id

        self._spawn(run_id, config)
        return run_id

    def cancel_run(self, run_id: int) -> bool:
        """SIGTERM an active run. Returns True if a process was signalled."""
        with self._lock:
            proc = self._active.get(run_id)
        if proc and proc.poll() is None:
            proc.terminate()
            return True
        return False

    def active_run_ids(self) -> list:
        """Snapshot of currently-active run IDs -- for status endpoints."""
        with self._lock:
            return list(self._active.keys())

    # --- Internals ---------------------------------------------------------

    def _spawn(self, run_id: int, config: dict) -> None:
        """Fork a subprocess and register a pump thread to drain its stdout."""
        cmd = [
            sys.executable, "-m", "src.web.services.runner_subprocess",
            "--run-id", str(run_id),
            "--config", json.dumps(config, ensure_ascii=False),
        ]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,   # merge so the pump sees ordered output
            text=True,
            bufsize=1,                  # line-buffered
        )
        with self._lock:
            self._active[run_id] = proc

        threading.Thread(
            target=self._pump,
            args=(run_id, proc),
            daemon=True,
            name=f"pump-{run_id}",
        ).start()

    def _pump(self, run_id: int, proc: subprocess.Popen) -> None:
        """Drain the child's stdout, forwarding events to bus + DB.

        Runs in a daemon thread per run. Every line is either JSON
        (structured event) or free text (treated as an INFO log). The
        pump keeps the LAST ``summary`` event and the LAST ``done``
        status so ``finalize_run`` records the real outcome, not a
        placeholder.
        """
        summary: Optional[dict] = None
        final_status = "failed"  # default: something went wrong if the
                                 # child never emitted a `done` event
        try:
            assert proc.stdout is not None, "stdout must be captured"
            for line in proc.stdout:
                line = line.rstrip("\n")
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    if not isinstance(event, dict):
                        raise ValueError("event must be a JSON object")
                except (json.JSONDecodeError, ValueError):
                    # Not a structured event -- treat as a plain log line
                    event = {
                        "type": "log", "level": "INFO",
                        "message": line, "logger": "subprocess",
                    }
                event_bus.publish(run_id, event)
                etype = event.get("type")
                if etype == "summary":
                    summary = event.get("summary")
                elif etype == "done":
                    final_status = event.get("status", "completed")
            proc.wait()
        finally:
            # Belt AND suspenders: persist + emit terminal done even if
            # the child crashed hard and we never parsed a `done` event.
            finalize_run(self.db_path, run_id, final_status, summary)
            event_bus.publish(run_id, {"type": "done", "status": final_status})
            with self._lock:
                self._active.pop(run_id, None)
            self._drain_pending()

    def _drain_pending(self) -> None:
        """Spawn the next queued run when a slot frees up.

        Called from ``_pump``'s finally block, so ``_active`` is
        guaranteed to have room by the time we look. We still re-check
        the invariant inside the lock because ``start_run`` might race
        us and fill the slot first.
        """
        with self._lock:
            if len(self._active) >= self.max_concurrent:
                return
            if self._pending.empty():
                return
        try:
            run_id, config = self._pending.get_nowait()
        except Empty:
            return
        self._spawn(run_id, config)


# --- Module-level singleton ------------------------------------------------
#
# FastAPI routes and tests both need to reach the same RunManager
# instance. Bind lazily at first access so the app's db_path is honored.

_instance: Optional[RunManager] = None


def get_run_manager(db_path: Path, max_concurrent: int = 1) -> RunManager:
    """Return the process-global RunManager, creating it on first call."""
    global _instance
    if _instance is None:
        _instance = RunManager(db_path, max_concurrent=max_concurrent)
    return _instance


def _reset_for_tests() -> None:
    """Test-only helper: forget the singleton so a fresh one binds next call."""
    global _instance
    _instance = None
