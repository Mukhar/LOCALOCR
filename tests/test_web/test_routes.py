"""
test_routes.py
==============
End-to-end HTTP tests using FastAPI's TestClient. No live server, no
network, no real subprocess -- everything runs in-process against an
isolated tmp SQLite DB per test.

The tricky bit: the runs router pulls in run_manager (which spawns
subprocesses). We monkeypatch ``get_run_manager`` to a fake that
records ``start_run`` calls without touching real processes.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import List, Tuple
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.web import create_app
from src.web.services import db, run_manager as rm


# --- Fixtures --------------------------------------------------------------

@pytest.fixture
def app_and_db(tmp_path: Path, monkeypatch):
    """Fresh FastAPI app pointed at a tmp SQLite DB. Resets the
    run_manager singleton between tests so state doesn't leak."""
    db_path = tmp_path / "test.sqlite"
    # Reset the singleton BEFORE create_app so it binds to our tmp DB
    rm._reset_for_tests()
    app = create_app(db_path=db_path)
    yield app, db_path
    rm._reset_for_tests()


@pytest.fixture
def client(app_and_db):
    app, _ = app_and_db
    # follow_redirects=False so we can assert on 3xx responses
    return TestClient(app, follow_redirects=False)


# --- Root + runs list ------------------------------------------------------

def test_root_redirects_to_runs(client: TestClient):
    r = client.get("/")
    assert r.status_code == 307
    assert r.headers["location"] == "/runs"


def test_health_returns_ok(client: TestClient):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_runs_list_empty(client: TestClient):
    r = client.get("/runs")
    assert r.status_code == 200
    assert "No runs yet" in r.text
    assert "LOCALOCR" in r.text
    # Skip-to-content link present (WCAG 2.4.1)
    assert "Skip to main content" in r.text


def test_runs_list_shows_run(client: TestClient, app_and_db):
    _, db_path = app_and_db
    db.insert_run(db_path, "/tmp/my_video.mp4", {"mode": "context"})
    r = client.get("/runs")
    assert r.status_code == 200
    assert "my_video.mp4" in r.text
    assert "context" in r.text
    assert "running" in r.text  # status badge


# --- New run form ----------------------------------------------------------

def test_run_new_form_renders_without_videos(client: TestClient, monkeypatch):
    # Force _list_input_videos to return empty
    monkeypatch.setattr("src.web.routes.videos._list_input_videos", lambda: [])
    r = client.get("/runs/new")
    assert r.status_code == 200
    assert "Start a New Run" in r.text
    assert "No videos found" in r.text


def test_run_new_form_lists_videos(client: TestClient, monkeypatch):
    monkeypatch.setattr(
        "src.web.routes.videos._list_input_videos",
        lambda: [{"name": "test_clip.mp4", "path": "/abs/test_clip.mp4"}],
    )
    r = client.get("/runs/new")
    assert r.status_code == 200
    assert "test_clip.mp4" in r.text
    assert 'value="/abs/test_clip.mp4"' in r.text


def test_run_new_submit_creates_run_and_redirects(
    client: TestClient, app_and_db, monkeypatch,
):
    """POST /runs -> RunManager.start_run mocked -> redirects to detail."""
    _, db_path = app_and_db

    class _FakeManager:
        def __init__(self):
            self.calls: List[dict] = []

        def start_run(self, config):
            self.calls.append(config)
            return db.insert_run(db_path, config["video_path"], config)

    fake = _FakeManager()
    monkeypatch.setattr(
        "src.web.routes.runs.get_run_manager",
        lambda _db_path: fake,
    )

    # Ensure the referenced config profile file exists (it does in the
    # repo since Plan 02-04 landed config.transcript.example.json, and
    # Phase 1 already has config/config.json).
    r = client.post("/runs", data={
        "video_path": "/tmp/fake_video.mp4",
        "config_profile": "default",
    })
    assert r.status_code == 303
    assert r.headers["location"].startswith("/runs/")

    # RunManager was called with an overridden video_path
    assert len(fake.calls) == 1
    assert fake.calls[0]["video_path"] == "/tmp/fake_video.mp4"


def test_run_new_submit_missing_profile_returns_500(
    client: TestClient, monkeypatch,
):
    """Unknown profile falls back to default; if default file is missing,
    500. We simulate the missing-file case."""
    monkeypatch.setattr(
        "src.web.routes.runs._CONFIG_PROFILES",
        {"default": "config/does_not_exist.json"},
    )
    r = client.post("/runs", data={
        "video_path": "/tmp/x.mp4",
        "config_profile": "default",
    })
    assert r.status_code == 500
    assert "Config profile file missing" in r.json()["detail"]


# --- Run detail ------------------------------------------------------------

def test_run_detail_404_for_unknown(client: TestClient):
    r = client.get("/runs/99999")
    assert r.status_code == 404


def test_run_detail_shows_picks(client: TestClient, app_and_db):
    _, db_path = app_and_db
    rid = db.insert_run(db_path, "/v.mp4", {"mode": "context"})
    db.insert_pick(db_path, rid, {"stockPick": "RELIANCE", "analyst": "Rahul"})
    db.insert_pick(db_path, rid, {"stockPick": "TCS", "analyst": "Ashwani"})

    r = client.get(f"/runs/{rid}")
    assert r.status_code == 200
    assert "RELIANCE" in r.text
    assert "TCS" in r.text
    assert "Rahul" in r.text


def test_run_detail_autoescapes_malicious_pick(
    client: TestClient, app_and_db,
):
    """Malicious LLM output must render as inert entities, not HTML."""
    _, db_path = app_and_db
    rid = db.insert_run(db_path, "/v.mp4", {})
    db.insert_pick(db_path, rid, {
        "stockPick": "<script>alert(1)</script>",
        "analyst":   "<img src=x onerror=alert(2)>",
    })

    r = client.get(f"/runs/{rid}")
    assert r.status_code == 200
    # Escaped forms present
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in r.text
    assert "&lt;img src=x onerror=alert(2)&gt;" in r.text
    # Executable forms absent
    assert "<script>alert(1)</script>" not in r.text
    assert "<img src=x onerror=alert(2)>" not in r.text


def test_run_detail_renders_transcript_context(
    client: TestClient, app_and_db,
):
    _, db_path = app_and_db
    rid = db.insert_run(db_path, "/v.mp4", {})
    db.insert_pick(db_path, rid, {
        "stockPick": "INFY",
        "transcript_context": {
            "before": "Our top pick is",
            "at":     "Infosys buy call",
            "after":  "with stop loss 1500",
            "speaker": None,
        },
    })
    r = client.get(f"/runs/{rid}")
    assert r.status_code == 200
    assert "Spoken context" in r.text
    assert "Infosys buy call" in r.text
    assert "with stop loss 1500" in r.text


# --- Delete ----------------------------------------------------------------

def test_run_delete_removes_row(client: TestClient, app_and_db):
    _, db_path = app_and_db
    rid = db.insert_run(db_path, "/v.mp4", {})
    r = client.delete(f"/runs/{rid}")
    assert r.status_code == 200
    assert db.get_run(db_path, rid) is None


# --- Videos ----------------------------------------------------------------

def test_videos_list_returns_json(client: TestClient, monkeypatch):
    monkeypatch.setattr(
        "src.web.routes.videos._list_input_videos",
        lambda: [{"name": "a.mp4", "path": "/x/a.mp4"}],
    )
    r = client.get("/videos")
    assert r.status_code == 200
    assert r.json() == [{"name": "a.mp4", "path": "/x/a.mp4"}]


def test_videos_list_missing_dir_returns_empty(
    client: TestClient, tmp_path, monkeypatch,
):
    """No input_videos/ directory -> empty list, not an error."""
    monkeypatch.setattr(
        "src.web.routes.videos._INPUT_DIR", tmp_path / "does_not_exist",
    )
    r = client.get("/videos")
    assert r.status_code == 200
    assert r.json() == []


def test_videos_upload_rejects_bad_extension(client: TestClient):
    r = client.post(
        "/videos/upload",
        files={"file": ("evil.exe", b"binary bytes", "application/octet-stream")},
    )
    assert r.status_code == 400
    assert "Unsupported extension" in r.json()["detail"]


def test_videos_upload_strips_path_traversal(
    client: TestClient, tmp_path, monkeypatch,
):
    """Filename like '../../etc/passwd.mp4' is reduced to 'passwd.mp4'
    before writing -- can't escape input_videos/."""
    monkeypatch.setattr("src.web.routes.videos._INPUT_DIR", tmp_path)
    r = client.post(
        "/videos/upload",
        files={"file": ("../../evil.mp4", b"fake mp4 bytes", "video/mp4")},
    )
    assert r.status_code == 200
    assert r.json()["name"] == "evil.mp4"
    # File landed in tmp_path (not two dirs up)
    assert (tmp_path / "evil.mp4").exists()
    assert not (tmp_path.parent.parent / "evil.mp4").exists()


def test_videos_upload_size_cap(
    client: TestClient, tmp_path, monkeypatch,
):
    """Upload larger than 500 MB gets 413. Test with a tiny cap for speed."""
    monkeypatch.setattr("src.web.routes.videos._INPUT_DIR", tmp_path)
    monkeypatch.setattr("src.web.routes.videos._MAX_UPLOAD_BYTES", 1024)  # 1 KB

    # 2 KB of bytes -> should get rejected mid-stream
    big = b"A" * 2048
    r = client.post(
        "/videos/upload",
        files={"file": ("big.mp4", big, "video/mp4")},
    )
    assert r.status_code == 413
    # Partial file was cleaned up
    assert not (tmp_path / "big.mp4").exists()


def test_video_stream_serves_file(
    client: TestClient, tmp_path, monkeypatch,
):
    monkeypatch.setattr("src.web.routes.videos._INPUT_DIR", tmp_path)
    (tmp_path / "clip.mp4").write_bytes(b"fake mp4 content")
    r = client.get("/videos/clip.mp4")
    assert r.status_code == 200
    assert r.content == b"fake mp4 content"
    # FastAPI's FileResponse advertises byte-range support
    assert r.headers.get("accept-ranges") == "bytes"


def test_video_stream_supports_byte_range(
    client: TestClient, tmp_path, monkeypatch,
):
    """Range request returns 206 Partial Content with the right slice."""
    monkeypatch.setattr("src.web.routes.videos._INPUT_DIR", tmp_path)
    payload = b"0123456789ABCDEF"
    (tmp_path / "clip.mp4").write_bytes(payload)
    r = client.get("/videos/clip.mp4", headers={"Range": "bytes=4-9"})
    assert r.status_code == 206
    assert r.content == b"456789"


def test_video_stream_path_traversal_defense(
    client: TestClient, tmp_path, monkeypatch,
):
    """GET /videos/../../etc/passwd -> stripped to /videos/passwd -> 404."""
    monkeypatch.setattr("src.web.routes.videos._INPUT_DIR", tmp_path)
    # Client sends a path-traversal filename; server should reduce to
    # basename which won't exist in tmp_path
    r = client.get("/videos/..%2F..%2Fetc%2Fpasswd")
    # Starlette normalizes the URL path first; we get a 404 either way
    # because the resolved file doesn't exist in _INPUT_DIR.
    assert r.status_code == 404


def test_video_stream_404_for_missing_file(
    client: TestClient, tmp_path, monkeypatch,
):
    monkeypatch.setattr("src.web.routes.videos._INPUT_DIR", tmp_path)
    r = client.get("/videos/nonexistent.mp4")
    assert r.status_code == 404
