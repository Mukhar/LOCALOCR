"""SQLite repository for the LOCALOCR web UI.

Design principles:
  - Pure functions taking ``db_path`` -- no module-level singleton
    connection, no ORM. Tests can point each call at ``tmp_path``.
  - Schema versioning via ``PRAGMA user_version``. Alembic would be
    ridiculous for two tables.
  - Every write path goes through :func:`connect` which commits on
    clean exit and rolls back on exception.
  - Foreign keys ON explicitly (SQLite default is OFF); ``picks`` has
    ON DELETE CASCADE against ``runs`` so :func:`delete_run` is atomic.

The schema:
  runs  -- one row per pipeline invocation (video + config snapshot +
           lifecycle status + summary blob)
  picks -- N rows per run: the flattened stock-pick records extracted
           from ``post_ocr_pipeline`` (analyst/stockPick/prices +
           the transcript_context JSON + the whole raw pick JSON)
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, List, Optional

SCHEMA_VERSION = 1

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS runs (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  video_path    TEXT NOT NULL,
  config_json   TEXT NOT NULL,
  mode          TEXT NOT NULL,
  status        TEXT NOT NULL,
  started_at    TEXT NOT NULL,
  finished_at   TEXT,
  total_frames  INTEGER,
  matched_count INTEGER,
  summary_json  TEXT
);
CREATE TABLE IF NOT EXISTS picks (
  id                        INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id                    INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  analyst                   TEXT,
  stock_pick                TEXT NOT NULL,
  current_price             TEXT,
  target                    TEXT,
  stop_loss                 TEXT,
  frame_path                TEXT,
  frame_timestamp_seconds   REAL,
  transcript_context        TEXT,
  raw_json                  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_picks_run    ON picks(run_id);
CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at DESC);
"""


def _now_iso() -> str:
    """UTC timestamp with seconds precision, ISO 8601. Sort-friendly."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- Schema management ------------------------------------------------------

def init_db(db_path: Path) -> None:
    """Create the schema if it doesn't exist. Idempotent.

    Uses ``PRAGMA user_version`` as the migration cursor. Bumping
    ``SCHEMA_VERSION`` above the stored value triggers a re-apply -- but
    since v1 uses ``CREATE TABLE IF NOT EXISTS`` throughout, that's a
    no-op on existing databases. Future migrations can branch here.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        cur = conn.execute("PRAGMA user_version")
        version = cur.fetchone()[0]
        if version < SCHEMA_VERSION:
            conn.executescript(_SCHEMA_SQL)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.commit()


@contextmanager
def connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Yield a connection that commits on success, rolls back on error.

    ``row_factory = sqlite3.Row`` so callers can do ``dict(row)`` and
    get a real dict without knowing column indices.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# --- Run lifecycle ---------------------------------------------------------

def insert_run(db_path: Path, video_path: str, config: dict) -> int:
    """Start a new run. Returns the auto-generated run id.

    Status is always 'running' at insert; :func:`finalize_run` moves
    it to 'success' / 'failed' / 'cancelled' when the pipeline exits.
    """
    with connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO runs (video_path, config_json, mode, status, started_at) "
            "VALUES (?, ?, ?, 'running', ?)",
            (video_path, json.dumps(config),
             config.get("mode", "accurate"), _now_iso()),
        )
        return cur.lastrowid


def finalize_run(
    db_path: Path,
    run_id: int,
    status: str,
    summary: Optional[dict] = None,
) -> None:
    """Mark a run as finished. Records status + finished_at + summary.

    ``summary`` is the dict returned by ``pipeline_runner.run_pipeline()``.
    We pull out ``total_frames`` and ``matched_frames`` into indexed columns
    for cheap sorting, and stash the whole thing as JSON in ``summary_json``.
    """
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE runs SET status=?, finished_at=?, "
            "total_frames=?, matched_count=?, summary_json=? WHERE id=?",
            (status, _now_iso(),
             (summary or {}).get("total_frames"),
             (summary or {}).get("matched_frames"),
             json.dumps(summary) if summary else None,
             run_id),
        )


def list_runs(db_path: Path, limit: int = 100) -> List[dict]:
    """Return most-recent runs first."""
    with connect(db_path) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()]


def get_run(db_path: Path, run_id: int) -> Optional[dict]:
    """Fetch a single run by id. Returns ``None`` if it doesn't exist."""
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM runs WHERE id=?", (run_id,)
        ).fetchone()
        return dict(row) if row else None


def delete_run(db_path: Path, run_id: int) -> None:
    """Delete a run. Cascade wipes its picks."""
    with connect(db_path) as conn:
        conn.execute("DELETE FROM runs WHERE id=?", (run_id,))


# --- Picks ------------------------------------------------------------------

def insert_pick(db_path: Path, run_id: int, pick: dict) -> int:
    """Insert one deduplicated pick from ``post_ocr_pipeline``.

    We denormalize the frequently-queried fields (analyst, stock_pick,
    prices, frame_path, frame_timestamp_seconds) into their own columns,
    persist ``transcript_context`` as its own JSON blob (so we can render
    the dashboard's Spoken Context section without re-parsing the raw
    payload), and dump the entire pick dict into ``raw_json`` as the
    source of truth.
    """
    tc = pick.get("transcript_context")
    with connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO picks (run_id, analyst, stock_pick, current_price, "
            "target, stop_loss, frame_path, frame_timestamp_seconds, "
            "transcript_context, raw_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, pick.get("analyst"), pick.get("stockPick", ""),
             pick.get("current_price"), pick.get("target"), pick.get("stop_loss"),
             pick.get("frame_path") or pick.get("_frame_path"),
             pick.get("frame_timestamp_seconds"),
             json.dumps(tc) if tc else None,
             json.dumps(pick)),
        )
        return cur.lastrowid


def list_picks(db_path: Path, run_id: int) -> List[dict]:
    """Return picks for a run in insertion order."""
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM picks WHERE run_id=? ORDER BY id", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]
