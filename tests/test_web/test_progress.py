"""
test_progress.py
================
Tests for the SSE progress endpoint and the `/runs/{id}/progress`
partial template. Two layers:

  1. HTTP layer (TestClient) -- template render + 404 semantics.
  2. Async generator layer (direct) -- SSE payload shape and
     termination-on-done, without the flakiness of asserting on
     an EventSource stream over TestClient.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import List
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.web import create_app
from src.web.services import db, run_manager as rm
from src.web.services.event_bus import EventBus


# --- Fixtures --------------------------------------------------------------

@pytest.fixture
def app_and_db(tmp_path: Path):
    db_path = tmp_path / "test.sqlite"
    rm._reset_for_tests()
    app = create_app(db_path=db_path)
    yield app, db_path
    rm._reset_for_tests()


@pytest.fixture
def client(app_and_db):
    app, _ = app_and_db
    return TestClient(app, follow_redirects=False)


# --- Progress panel HTML ---------------------------------------------------

def test_progress_panel_renders_for_existing_run(client, app_and_db):
    _, db_path = app_and_db
    rid = db.insert_run(db_path, "/v.mp4", {"mode": "context"})
    r = client.get(f"/runs/{rid}/progress")
    assert r.status_code == 200
    # Fragment contains the sse-connect attribute pointing at the events
    # endpoint for this run id
    assert f'sse-connect="/runs/{rid}/events"' in r.text
    # And an aria-live region so screen readers announce updates
    assert 'aria-live="polite"' in r.text
    # And the JS listener hook
    assert "htmx:sseMessage" in r.text


def test_progress_panel_404_for_unknown_run(client):
    r = client.get("/runs/99999/progress")
    assert r.status_code == 404


# --- SSE async generator ---------------------------------------------------

@pytest.mark.asyncio
async def test_sse_generator_forwards_bus_events(monkeypatch):
    """Publishing to the bus surfaces messages on the generator."""
    from src.web.routes import progress as progress_module

    fresh = EventBus()
    monkeypatch.setattr(progress_module, "event_bus", fresh)

    # Fake Request that never disconnects
    req = MagicMock()
    async def not_disconnected():
        return False
    req.is_disconnected = not_disconnected

    gen = progress_module._sse_generator(req, run_id=1)

    async def publish_then_done():
        # Give the generator a beat to reach `async for` on subscribe()
        await asyncio.sleep(0.02)
        fresh.publish(1, {"type": "log", "level": "INFO",
                                "message": "hello"})
        await asyncio.sleep(0.02)
        fresh.publish(1, {"type": "done", "status": "completed"})

    publisher = asyncio.create_task(publish_then_done())

    received = []
    async for msg in gen:
        received.append(msg)

    await publisher

    # Every yielded msg is {event: ..., data: <json string>}
    assert len(received) == 2
    assert received[0]["event"] == "log"
    payload0 = json.loads(received[0]["data"])
    assert payload0["message"] == "hello"

    assert received[1]["event"] == "done"
    payload1 = json.loads(received[1]["data"])
    assert payload1["status"] == "completed"


@pytest.mark.asyncio
async def test_sse_generator_terminates_on_done(monkeypatch):
    """The subscribe() iterator stops after `done`, so the SSE
    generator naturally exits -- no infinite hang."""
    from src.web.routes import progress as progress_module

    fresh = EventBus()
    monkeypatch.setattr(progress_module, "event_bus", fresh)

    req = MagicMock()
    async def not_disconnected():
        return False
    req.is_disconnected = not_disconnected

    async def publish():
        await asyncio.sleep(0.02)
        fresh.publish(1, {"type": "done", "status": "failed"})

    asyncio.create_task(publish())

    gen = progress_module._sse_generator(req, run_id=1)
    # If terminate-on-done is broken, this loop hangs forever; wait_for
    # gives us a clean timeout signal instead of a stuck test.
    async def drain():
        events = []
        async for msg in gen:
            events.append(msg)
        return events

    events = await asyncio.wait_for(drain(), timeout=2.0)
    assert len(events) == 1
    assert events[0]["event"] == "done"


@pytest.mark.asyncio
async def test_sse_generator_stops_on_client_disconnect(monkeypatch):
    """If the client hangs up, is_disconnected() flips True and we
    bail before the next yield."""
    from src.web.routes import progress as progress_module

    fresh = EventBus()
    monkeypatch.setattr(progress_module, "event_bus", fresh)

    # Request that reports disconnected on the SECOND check
    disconnect_checks = {"count": 0}
    async def is_disconnected():
        disconnect_checks["count"] += 1
        return disconnect_checks["count"] >= 2

    req = MagicMock()
    req.is_disconnected = is_disconnected

    async def publish():
        await asyncio.sleep(0.02)
        fresh.publish(1, {"type": "log", "message": "1"})
        await asyncio.sleep(0.02)
        fresh.publish(1, {"type": "log", "message": "2"})
        await asyncio.sleep(0.02)
        fresh.publish(1, {"type": "done", "status": "completed"})

    asyncio.create_task(publish())

    gen = progress_module._sse_generator(req, run_id=1)
    received = []
    async def drain():
        async for msg in gen:
            received.append(msg)
    await asyncio.wait_for(drain(), timeout=2.0)

    # Received one message before the second is_disconnected check fired
    assert len(received) == 1
    assert json.loads(received[0]["data"])["message"] == "1"


# --- run_detail wiring ----------------------------------------------------

def test_run_detail_only_loads_progress_when_running(client, app_and_db):
    """For a completed run we skip the progress panel fetch entirely."""
    _, db_path = app_and_db
    rid = db.insert_run(db_path, "/v.mp4", {})
    db.finalize_run(db_path, rid, "completed", {"total_frames": 10})

    r = client.get(f"/runs/{rid}")
    assert r.status_code == 200
    # No hx-get for the progress panel when status is not "running"
    assert f"/runs/{rid}/progress" not in r.text


def test_run_detail_loads_progress_when_running(client, app_and_db):
    _, db_path = app_and_db
    rid = db.insert_run(db_path, "/v.mp4", {})
    r = client.get(f"/runs/{rid}")
    assert r.status_code == 200
    # Running runs get an hx-get to the progress panel
    assert f'hx-get="/runs/{rid}/progress"' in r.text
    assert 'hx-trigger="load"' in r.text


def test_run_detail_wires_seek_script_and_video_id(client, app_and_db):
    _, db_path = app_and_db
    rid = db.insert_run(db_path, "/tmp/vid.mp4", {})
    r = client.get(f"/runs/{rid}")
    assert r.status_code == 200
    assert 'id="source-video"' in r.text
    assert '/static/seek.js' in r.text


def test_pick_card_seek_button_uses_data_seek(client, app_and_db):
    """The seek button on a pick uses data-seek (matches seek.js selector)."""
    _, db_path = app_and_db
    rid = db.insert_run(db_path, "/v.mp4", {})
    db.insert_pick(db_path, rid, {
        "stockPick": "INFY",
        "frame_timestamp_seconds": 42.5,
    })
    r = client.get(f"/runs/{rid}")
    assert r.status_code == 200
    assert 'data-seek="42.5"' in r.text
    # No stale data-seek-btn attr (removed in this plan)
    assert 'data-seek-btn=' not in r.text
