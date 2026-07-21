"""
test_run_manager.py
===================
Unit tests for :class:`RunManager` and the run subprocess entry point.

Zero real subprocesses: ``unittest.mock.patch`` on
``src.web.services.run_manager.subprocess.Popen`` produces a fake
process whose ``stdout`` is a controllable iterator, so we can inject
event streams synchronously and assert on the resulting bus + DB state.

Uses ``threading.Event`` to synchronize with the daemon pump thread --
the pump runs asynchronously, so tests wait on a Barrier / Event to
know when the pump has finished draining the fake stdout.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.web.services import db
from src.web.services.event_bus import EventBus
from src.web.services.run_manager import RunManager


# --- Shared fixtures -------------------------------------------------------

@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "test.sqlite"
    db.init_db(p)
    return p


class _FakePopen:
    """Stand-in for subprocess.Popen with a scripted stdout iterator.

    ``lines`` are yielded one-by-one by ``stdout`` iteration; after they
    exhaust, ``wait()`` returns ``exit_code`` (default 0). The pump
    thread will drain everything and then hit its ``finally`` block.
    """

    def __init__(self, lines: list, exit_code: int = 0):
        # An iter() lets the pump's ``for line in proc.stdout`` work
        self.stdout = iter([ln + "\n" for ln in lines])
        self._exit_code = exit_code
        self.terminated = False

    def wait(self) -> int:
        return self._exit_code

    def poll(self):
        return None if not self.terminated else self._exit_code

    def terminate(self) -> None:
        self.terminated = True


def _install_bus_capture(monkeypatch) -> list:
    """Swap the module-level event_bus for a fresh EventBus and capture
    every publish call. Returns a list that grows as events arrive."""
    captured: list = []
    fresh = EventBus()
    orig_publish = fresh.publish

    def spy(run_id, event):
        captured.append((run_id, event))
        return orig_publish(run_id, event)

    fresh.publish = spy  # type: ignore[assignment]
    monkeypatch.setattr("src.web.services.run_manager.event_bus", fresh)
    return captured


def _wait_for_done(captured: list, run_id: int, timeout: float = 3.0) -> None:
    """Wait for the pump thread for ``run_id`` to exit -- guarantees the
    finally-block finalize_run() has already committed to the DB.

    (Watching the event stream isn't enough: the pump publishes a
    terminal ``done`` AFTER finalize_run, but the child's own ``done``
    fires BEFORE the DB write. Joining the thread is the only
    ordering-safe signal.)
    """
    deadline = time.time() + timeout
    thread_name = f"pump-{run_id}"
    while time.time() < deadline:
        alive = [t for t in threading.enumerate() if t.name == thread_name]
        if not alive:
            # Pump thread has exited -- DB write is committed
            return
        for t in alive:
            t.join(timeout=0.05)
    raise AssertionError(f"Timeout waiting for pump-{run_id} to exit")


# --- Tests -----------------------------------------------------------------

def test_start_run_creates_db_row(db_path: Path, monkeypatch):
    """start_run inserts a DB row and returns its id BEFORE the pump
    even starts consuming events."""
    _install_bus_capture(monkeypatch)
    fake = _FakePopen(lines=[
        json.dumps({"type": "start", "run_id": 1}),
        json.dumps({"type": "done", "status": "completed"}),
    ])
    with patch("src.web.services.run_manager.subprocess.Popen", return_value=fake):
        mgr = RunManager(db_path)
        rid = mgr.start_run({"video_path": "/v.mp4", "mode": "context"})
    assert rid == 1
    row = db.get_run(db_path, rid)
    assert row is not None
    assert row["video_path"] == "/v.mp4"
    assert row["mode"] == "context"


def test_pump_forwards_events_to_bus(db_path: Path, monkeypatch):
    """Every line the subprocess emits reaches the event bus."""
    captured = _install_bus_capture(monkeypatch)
    fake = _FakePopen(lines=[
        json.dumps({"type": "start", "run_id": 1}),
        json.dumps({"type": "log", "level": "INFO", "message": "extracting"}),
        json.dumps({"type": "summary", "summary": {"total_frames": 42}}),
        json.dumps({"type": "done", "status": "completed"}),
    ])
    with patch("src.web.services.run_manager.subprocess.Popen", return_value=fake):
        mgr = RunManager(db_path)
        rid = mgr.start_run({"video_path": "/v.mp4"})
        _wait_for_done(captured, rid)

    types = [ev.get("type") for _, ev in captured if _ == rid]
    # 4 from the subprocess + 1 terminal done from the finally block
    assert types == ["start", "log", "summary", "done", "done"]


def test_pump_handles_non_json_lines_as_logs(db_path: Path, monkeypatch):
    """Free-text stdout lines become {type: log, level: INFO, ...}."""
    captured = _install_bus_capture(monkeypatch)
    fake = _FakePopen(lines=[
        "plain log line, not JSON",
        json.dumps({"type": "done", "status": "completed"}),
    ])
    with patch("src.web.services.run_manager.subprocess.Popen", return_value=fake):
        mgr = RunManager(db_path)
        rid = mgr.start_run({"video_path": "/v.mp4"})
        _wait_for_done(captured, rid)

    # First captured event was the free-text line wrapped as a log
    log_event = next(ev for _, ev in captured if ev.get("type") == "log")
    assert log_event == {
        "type": "log", "level": "INFO",
        "message": "plain log line, not JSON",
        "logger": "subprocess",
    }


def test_pump_handles_json_that_is_not_a_dict(db_path: Path, monkeypatch):
    """A JSON list/scalar is treated as free text, not an event."""
    captured = _install_bus_capture(monkeypatch)
    fake = _FakePopen(lines=[
        "[1, 2, 3]",  # parses as JSON but isn't a dict
        json.dumps({"type": "done", "status": "completed"}),
    ])
    with patch("src.web.services.run_manager.subprocess.Popen", return_value=fake):
        mgr = RunManager(db_path)
        rid = mgr.start_run({"video_path": "/v.mp4"})
        _wait_for_done(captured, rid)

    log_event = next(ev for _, ev in captured if ev.get("type") == "log")
    assert log_event["message"] == "[1, 2, 3]"


def test_pump_persists_summary_via_finalize(db_path: Path, monkeypatch):
    """A `summary` event's payload lands in the DB via finalize_run."""
    captured = _install_bus_capture(monkeypatch)
    summary = {"total_frames": 100, "matched_frames": 15, "elapsed": 8.4}
    fake = _FakePopen(lines=[
        json.dumps({"type": "summary", "summary": summary}),
        json.dumps({"type": "done", "status": "completed"}),
    ])
    with patch("src.web.services.run_manager.subprocess.Popen", return_value=fake):
        mgr = RunManager(db_path)
        rid = mgr.start_run({"video_path": "/v.mp4"})
        _wait_for_done(captured, rid)

    row = db.get_run(db_path, rid)
    assert row["status"] == "completed"
    assert row["total_frames"] == 100
    assert row["matched_count"] == 15
    assert row["finished_at"] is not None


def test_pump_finalizes_failed_when_child_never_says_done(db_path: Path, monkeypatch):
    """If the subprocess dies without emitting a done event, the DB
    row still gets finalized (default status: failed)."""
    captured = _install_bus_capture(monkeypatch)
    fake = _FakePopen(lines=[
        json.dumps({"type": "log", "message": "starting"}),
        # NO done event
    ], exit_code=139)  # segfault-ish exit
    with patch("src.web.services.run_manager.subprocess.Popen", return_value=fake):
        mgr = RunManager(db_path)
        rid = mgr.start_run({"video_path": "/v.mp4"})
        _wait_for_done(captured, rid)

    row = db.get_run(db_path, rid)
    assert row["status"] == "failed"
    # And a synthetic terminal done was published
    done_events = [ev for _, ev in captured if ev.get("type") == "done"]
    assert done_events == [{"type": "done", "status": "failed"}]


def test_max_concurrent_queues_extras(db_path: Path, monkeypatch):
    """Second start_run when at capacity gets a queued event, not a
    Popen call."""
    captured = _install_bus_capture(monkeypatch)

    # First run's fake process blocks on a signal so it stays active
    hold = threading.Event()

    class _BlockingPopen(_FakePopen):
        def __init__(self):
            super().__init__(lines=[
                json.dumps({"type": "done", "status": "completed"}),
            ])

        def wait(self):
            hold.wait(timeout=2)
            return 0

    popen_calls = []
    def fake_popen(*args, **kwargs):
        popen_calls.append(args)
        # First call returns the blocking popen; subsequent calls would
        # be second-run spawns.
        return _BlockingPopen() if len(popen_calls) == 1 else _FakePopen(lines=[
            json.dumps({"type": "done", "status": "completed"})
        ])

    with patch("src.web.services.run_manager.subprocess.Popen", side_effect=fake_popen):
        mgr = RunManager(db_path, max_concurrent=1)
        rid1 = mgr.start_run({"video_path": "/v1.mp4"})
        # Give pump time to register the active proc
        time.sleep(0.05)
        rid2 = mgr.start_run({"video_path": "/v2.mp4"})

        # Second run got a queued event but NO Popen call yet
        queued = [ev for r, ev in captured
                  if r == rid2 and ev.get("type") == "queued"]
        assert len(queued) == 1
        assert queued[0]["queued_position"] == 1
        assert len(popen_calls) == 1  # only run 1 spawned

        # Release the first run's wait() so it exits, triggering drain
        hold.set()
        _wait_for_done(captured, rid1)
        _wait_for_done(captured, rid2)

    # Both runs eventually spawned
    assert len(popen_calls) == 2


def test_cancel_run_terminates_process(db_path: Path, monkeypatch):
    """cancel_run(rid) sends terminate() to the active process."""
    _install_bus_capture(monkeypatch)
    hold = threading.Event()

    class _BlockingPopen(_FakePopen):
        def __init__(self):
            super().__init__(lines=[
                json.dumps({"type": "done", "status": "interrupted"}),
            ])

        def wait(self):
            hold.wait(timeout=2)
            return 130

    proc = _BlockingPopen()
    with patch("src.web.services.run_manager.subprocess.Popen", return_value=proc):
        mgr = RunManager(db_path)
        rid = mgr.start_run({"video_path": "/v.mp4"})
        time.sleep(0.05)  # let pump start
        assert mgr.cancel_run(rid) is True
        assert proc.terminated is True
        hold.set()  # let the pump finish


def test_cancel_run_returns_false_for_unknown_run(db_path: Path):
    mgr = RunManager(db_path)
    assert mgr.cancel_run(999) is False


def test_active_run_ids_snapshot(db_path: Path, monkeypatch):
    """active_run_ids returns the currently-running ids."""
    _install_bus_capture(monkeypatch)
    hold = threading.Event()

    class _BlockingPopen(_FakePopen):
        def __init__(self):
            super().__init__(lines=[
                json.dumps({"type": "done", "status": "completed"}),
            ])

        def wait(self):
            hold.wait(timeout=2)
            return 0

    with patch("src.web.services.run_manager.subprocess.Popen",
               return_value=_BlockingPopen()):
        mgr = RunManager(db_path)
        assert mgr.active_run_ids() == []
        rid = mgr.start_run({"video_path": "/v.mp4"})
        time.sleep(0.05)
        assert mgr.active_run_ids() == [rid]
        hold.set()
