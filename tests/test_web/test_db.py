"""
test_db.py
==========
Unit tests for the SQLite repository layer.

Uses ``tmp_path`` for a fresh, isolated DB per test -- zero shared state.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.web.services import db


# --- Schema / lifecycle ----------------------------------------------------

def test_init_db_creates_tables(tmp_path: Path):
    """After init_db, the runs and picks tables exist with the right columns."""
    p = tmp_path / "test.sqlite"
    db.init_db(p)

    import sqlite3
    with sqlite3.connect(p) as conn:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert "runs" in tables
        assert "picks" in tables

        run_cols = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
        assert {"id", "video_path", "config_json", "mode", "status",
                "started_at", "finished_at", "total_frames", "matched_count",
                "summary_json"}.issubset(run_cols)

        pick_cols = {row[1] for row in conn.execute("PRAGMA table_info(picks)")}
        assert {"id", "run_id", "analyst", "stock_pick", "current_price",
                "target", "stop_loss", "frame_path", "frame_timestamp_seconds",
                "transcript_context", "raw_json"}.issubset(pick_cols)


def test_init_db_is_idempotent(tmp_path: Path):
    """Calling init_db twice on the same path must not raise."""
    p = tmp_path / "test.sqlite"
    db.init_db(p)
    db.init_db(p)  # should be a no-op via PRAGMA user_version check
    # Prove the schema still works after re-init
    rid = db.insert_run(p, "/v.mp4", {"mode": "context"})
    assert rid == 1


def test_init_db_records_user_version(tmp_path: Path):
    """PRAGMA user_version is set to SCHEMA_VERSION after init."""
    p = tmp_path / "test.sqlite"
    db.init_db(p)
    import sqlite3
    with sqlite3.connect(p) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == db.SCHEMA_VERSION


# --- Runs ------------------------------------------------------------------

def test_insert_and_get_run(tmp_path: Path):
    p = tmp_path / "t.sqlite"
    db.init_db(p)
    rid = db.insert_run(p, "/v.mp4", {"mode": "context", "match_keywords": ["x"]})
    fetched = db.get_run(p, rid)
    assert fetched is not None
    assert fetched["id"] == rid
    assert fetched["video_path"] == "/v.mp4"
    assert fetched["mode"] == "context"
    assert fetched["status"] == "running"
    assert fetched["finished_at"] is None


def test_get_run_returns_none_for_missing_id(tmp_path: Path):
    p = tmp_path / "t.sqlite"
    db.init_db(p)
    assert db.get_run(p, 999) is None


def test_list_runs_ordered_desc(tmp_path: Path):
    """List returns most-recent first (SQLite auto-increment gives us
    monotonically increasing IDs, and started_at ISO strings sort
    lexicographically). Verify order via IDs."""
    p = tmp_path / "t.sqlite"
    db.init_db(p)
    import time
    rid1 = db.insert_run(p, "/v1.mp4", {})
    time.sleep(1.01)  # ISO timestamps have 1-second precision
    rid2 = db.insert_run(p, "/v2.mp4", {})
    time.sleep(1.01)
    rid3 = db.insert_run(p, "/v3.mp4", {})

    runs = db.list_runs(p)
    assert [r["id"] for r in runs] == [rid3, rid2, rid1]


def test_list_runs_respects_limit(tmp_path: Path):
    p = tmp_path / "t.sqlite"
    db.init_db(p)
    for i in range(5):
        db.insert_run(p, f"/v{i}.mp4", {})
    assert len(db.list_runs(p, limit=3)) == 3


def test_finalize_run_updates_status_and_summary(tmp_path: Path):
    p = tmp_path / "t.sqlite"
    db.init_db(p)
    rid = db.insert_run(p, "/v.mp4", {})

    summary = {"total_frames": 150, "matched_frames": 22, "elapsed": 12.5}
    db.finalize_run(p, rid, "success", summary)

    fetched = db.get_run(p, rid)
    assert fetched["status"] == "success"
    assert fetched["finished_at"] is not None
    assert fetched["total_frames"] == 150
    assert fetched["matched_count"] == 22
    import json
    assert json.loads(fetched["summary_json"])["elapsed"] == 12.5


def test_finalize_run_without_summary(tmp_path: Path):
    """Failure paths may finalize without a summary dict."""
    p = tmp_path / "t.sqlite"
    db.init_db(p)
    rid = db.insert_run(p, "/v.mp4", {})
    db.finalize_run(p, rid, "failed", None)
    fetched = db.get_run(p, rid)
    assert fetched["status"] == "failed"
    assert fetched["total_frames"] is None
    assert fetched["summary_json"] is None


# --- Picks -----------------------------------------------------------------

def test_insert_and_list_picks(tmp_path: Path):
    p = tmp_path / "t.sqlite"
    db.init_db(p)
    rid = db.insert_run(p, "/v.mp4", {})

    pick = {
        "stockPick": "RELIANCE",
        "analyst": "Rahul Shah",
        "recommended_price": 2400,
        "current_price": 2380,
        "stop_loss": 2300,
        "target": 2600,
        "_frame_path": "output/matched/sethi/frame_0100.png",
        "frame_timestamp_seconds": 200.0,
        "transcript_context": {"before": "hi", "at": "reliance", "after": "bye",
                               "speaker": None},
    }
    pid = db.insert_pick(p, rid, pick)
    assert pid == 1

    picks = db.list_picks(p, rid)
    assert len(picks) == 1
    assert picks[0]["stock_pick"] == "RELIANCE"
    assert picks[0]["analyst"] == "Rahul Shah"
    assert picks[0]["frame_path"] == "output/matched/sethi/frame_0100.png"
    assert picks[0]["frame_timestamp_seconds"] == 200.0

    # transcript_context stored as JSON blob
    import json
    ctx = json.loads(picks[0]["transcript_context"])
    assert ctx["at"] == "reliance"

    # raw_json is the whole pick, source of truth
    raw = json.loads(picks[0]["raw_json"])
    assert raw["recommended_price"] == 2400


def test_insert_pick_without_transcript_context(tmp_path: Path):
    """Picks without transcript_context store NULL in that column."""
    p = tmp_path / "t.sqlite"
    db.init_db(p)
    rid = db.insert_run(p, "/v.mp4", {})
    db.insert_pick(p, rid, {"stockPick": "TCS"})
    picks = db.list_picks(p, rid)
    assert picks[0]["transcript_context"] is None


def test_list_picks_ordered_by_insertion(tmp_path: Path):
    p = tmp_path / "t.sqlite"
    db.init_db(p)
    rid = db.insert_run(p, "/v.mp4", {})
    for name in ["A", "B", "C"]:
        db.insert_pick(p, rid, {"stockPick": name})
    assert [pk["stock_pick"] for pk in db.list_picks(p, rid)] == ["A", "B", "C"]


def test_list_picks_scopes_to_run(tmp_path: Path):
    """Picks from one run don't leak into another."""
    p = tmp_path / "t.sqlite"
    db.init_db(p)
    r1 = db.insert_run(p, "/v1.mp4", {})
    r2 = db.insert_run(p, "/v2.mp4", {})
    db.insert_pick(p, r1, {"stockPick": "R1_PICK"})
    db.insert_pick(p, r2, {"stockPick": "R2_PICK"})
    assert [pk["stock_pick"] for pk in db.list_picks(p, r1)] == ["R1_PICK"]
    assert [pk["stock_pick"] for pk in db.list_picks(p, r2)] == ["R2_PICK"]


def test_delete_run_cascades_to_picks(tmp_path: Path):
    """ON DELETE CASCADE + PRAGMA foreign_keys=ON wipes child picks."""
    p = tmp_path / "t.sqlite"
    db.init_db(p)
    rid = db.insert_run(p, "/v.mp4", {})
    db.insert_pick(p, rid, {"stockPick": "X"})
    db.insert_pick(p, rid, {"stockPick": "Y"})
    assert len(db.list_picks(p, rid)) == 2

    db.delete_run(p, rid)
    assert db.get_run(p, rid) is None
    assert db.list_picks(p, rid) == []


# --- Transaction rollback --------------------------------------------------

def test_connect_rolls_back_on_exception(tmp_path: Path):
    """Exception inside connect() context should roll back writes."""
    p = tmp_path / "t.sqlite"
    db.init_db(p)

    with pytest.raises(RuntimeError):
        with db.connect(p) as conn:
            conn.execute(
                "INSERT INTO runs (video_path, config_json, mode, status, "
                "started_at) VALUES ('/x.mp4', '{}', 'context', 'running', "
                "'2026-01-01T00:00:00+00:00')"
            )
            raise RuntimeError("boom")

    # Rollback means the insert didn't stick
    assert db.list_runs(p) == []
