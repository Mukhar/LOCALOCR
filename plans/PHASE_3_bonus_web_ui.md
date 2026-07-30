# Phase 3 (Bonus) — Web UI / Live Dashboard

**Depends on:** Phases 1 & 2. Everything Phase 3 shows is more
compelling with scene-extraction (fewer, more meaningful frames) and
transcript context (richer dashboard cards). Not a hard dependency —
Phase 3 works with vanilla LOCALOCR too.

**Goal:** Replace the standalone `output/viewer.html` with a proper
FastAPI + HTMX + Tailwind app that:
1. Lets users **kick off pipeline runs from the browser** (video upload
   or path picker).
2. **Streams live progress** to the browser via Server-Sent Events.
3. **Serves the stock-picks dashboard** with actual click-to-jump video
   playback (using the frame timestamps).
4. **Browses run history** — every completed run is queryable.
5. Runs 100% locally, no auth, single-user assumption. WCAG 2.2 AA
   compliant styling (Walmart-standard even though this is personal
   tooling — good habits).

**Stack:** FastAPI + HTMX + Tailwind + SQLite + Chart.js. The Walmart
default. HTML-over-the-wire keeps complexity low; no build step, no
node_modules, no framework churn.

---

## Success criteria

- [ ] `python -m localocr.web` starts a server on `http://localhost:8765`.
- [ ] Landing page lists all past runs (video, timestamp, matched count,
  status). Sortable, filterable, keyboard-navigable.
- [ ] "New Run" page: pick a video from `input_videos/` OR upload one,
  choose a config profile, click Run. Redirects to a live-progress page.
- [ ] Live-progress page shows real-time updates via SSE:
  - Current phase (extract / OCR / match / organize / analyze)
  - Frame counter, matched counter, elapsed time
  - Log tail (last 20 lines)
- [ ] Run-detail page: dashboard equivalent of current `viewer.html`
  PLUS the source video embedded, click-a-pick → video seeks to that
  frame's timestamp. Transcript snippet shown per pick (Phase 2 payoff).
- [ ] All data persisted in `output/localocr.sqlite`. Runs survive
  server restarts.
- [ ] Zero JavaScript build step. Zero framework. Just HTMX + a tiny
  amount of vanilla JS for the video-seek interactions.
- [ ] Passes WCAG 2.2 AA: contrast ≥ 4.5:1, keyboard navigation, ARIA
  labels on all controls, focus indicators visible.
- [ ] Old `viewer.html` remains generated for backward compat, but the
  server is the recommended experience.

---

## Design

### Directory layout

```
src/web/
  __init__.py
  app.py                    # FastAPI app factory + route registration
  routes/
    __init__.py
    runs.py                 # GET / POST /runs, GET /runs/{id}
    progress.py             # SSE stream for a running job
    videos.py               # list input videos, upload
  services/
    __init__.py
    run_manager.py          # spawns pipeline in a subprocess, tracks state
    event_bus.py            # in-memory pub/sub for SSE
    db.py                   # SQLite schema + queries
  templates/
    base.html               # Tailwind CDN, layout, nav
    _card.html              # reusable stock-pick card partial
    _pipeline_step.html     # reusable progress-step partial
    runs_list.html
    run_new.html
    run_detail.html
    run_progress.html
  static/
    tailwind.css            # optional — pulled from CDN in base.html
    seek.js                 # video-seek click handler
```

### Database schema (`output/localocr.sqlite`)

```sql
CREATE TABLE runs (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  video_path    TEXT NOT NULL,
  config_json   TEXT NOT NULL,
  mode          TEXT NOT NULL,
  status        TEXT NOT NULL,        -- 'running'|'completed'|'failed'
  started_at    TEXT NOT NULL,
  finished_at   TEXT,
  total_frames  INTEGER,
  matched_count INTEGER,
  summary_json  TEXT                  -- full run_pipeline() summary
);

CREATE TABLE picks (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id         INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  analyst        TEXT,
  stock_pick     TEXT NOT NULL,
  current_price  TEXT,
  target         TEXT,
  stop_loss      TEXT,
  frame_path     TEXT,
  frame_timestamp_seconds REAL,
  transcript_context TEXT,            -- Phase 2 payoff
  raw_json       TEXT NOT NULL
);

CREATE INDEX idx_picks_run ON picks(run_id);
CREATE INDEX idx_runs_started ON runs(started_at DESC);
```

Migrations: pure Python via a tiny helper (`schema_v = user_version`
pragma). No Alembic. YAGNI.

### Run manager (the interesting bit)

`services/run_manager.py`:

```python
class RunManager:
    """
    Runs pipelines in a subprocess (fresh Python interpreter per run) so:
      - Ollama, EasyOCR, whisper.cpp all initialize cleanly per run
      - a crashing run can't take down the web server
      - the web server stays responsive during long runs
    """

    def start_run(self, config: dict) -> int:
        run_id = db.insert_run(config, status="running")
        proc = subprocess.Popen(
            [sys.executable, "-m", "localocr.web.runner_subprocess",
             "--run-id", str(run_id), "--config", json.dumps(config)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        threading.Thread(
            target=self._pump_output, args=(run_id, proc),
            daemon=True,
        ).start()
        return run_id

    def _pump_output(self, run_id, proc):
        for line in proc.stdout:
            event_bus.publish(run_id, {"type": "log", "line": line.rstrip()})
        proc.wait()
        db.finalize_run(run_id, status="completed" if proc.returncode == 0
                                  else "failed")
        event_bus.publish(run_id, {"type": "done",
                                    "status": db.get_run(run_id).status})
```

`runner_subprocess.py` is a tiny script that installs a logging handler
publishing structured events to stdout (parsed by the pump thread), then
calls `run_pipeline(config)`.

### Event bus (SSE)

Trivial in-memory pub/sub:
```python
class EventBus:
    def __init__(self):
        self._queues: dict[int, list[asyncio.Queue]] = defaultdict(list)

    def publish(self, run_id, event):
        for q in self._queues[run_id]:
            q.put_nowait(event)

    async def subscribe(self, run_id):
        q = asyncio.Queue()
        self._queues[run_id].append(q)
        try:
            while True:
                event = await q.get()
                yield event
                if event.get("type") == "done":
                    return
        finally:
            self._queues[run_id].remove(q)
```

FastAPI SSE endpoint:
```python
@router.get("/runs/{run_id}/events")
async def stream(run_id: int):
    return EventSourceResponse(event_bus.subscribe(run_id))
```

Client-side HTMX + `hx-sse` extension → zero custom JS for the stream.

### Video seek on pick click

Every pick card has `data-seek="<seconds>"`. `static/seek.js` is ~10
lines:
```js
document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-seek]");
  if (!el) return;
  const video = document.getElementById("source-video");
  if (!video) return;
  video.currentTime = parseFloat(el.dataset.seek);
  video.play();
  video.scrollIntoView({ behavior: "smooth", block: "center" });
});
```

That's the whole video interaction. Delegated listener, no framework.

### Walmart-standard styling

- Activate `cp_walmart_colors` skill during implementation.
- Tailwind config uses the Walmart palette (Bentonville Blue, Spark
  Yellow) via CSS variables in `base.html`.
- Dark mode toggle via a `data-theme` attribute (single-line CSS var
  swap). Dark by default — matches the current `viewer.html` vibe.
- Focus outlines: `focus-visible:ring-2 ring-offset-2` on every
  interactive element.
- Screen-reader-only labels for icon buttons.

---

## Task breakdown

### Task 3.1 — Package scaffolding (~30 min)
- Create `src/web/` structure per the layout above.
- `pyproject.toml` add: `fastapi`, `uvicorn[standard]`, `sse-starlette`,
  `python-multipart` (for uploads), `aiosqlite`.
- `__main__.py` — `python -m localocr.web` entry point.
- Install per Walmart rules: `uv pip install --index-url … …`.

### Task 3.2 — SQLite schema + repository (~1 h)
- `services/db.py` — schema DDL, migrations, CRUD queries.
- No ORM. Straight SQL with named parameters. Small enough that
  SQLAlchemy would be overkill.
- Unit tests: temp SQLite file per test, verify each query.

### Task 3.3 — Event bus (~30 min)
- `services/event_bus.py` — implement + unit test.
- Tests use `asyncio` + `anyio` for the async iteration.

### Task 3.4 — Run subprocess wrapper (~1.5 h)
- `services/runner_subprocess.py` — parses `--run-id` and `--config`,
  installs a JSON-lines logging handler, calls `run_pipeline`, writes a
  final `{"type": "summary", ...}` line.
- Handles KeyboardInterrupt cleanly (existing pipeline already does).

### Task 3.5 — Run manager (~1.5 h)
- `services/run_manager.py` — implements `start_run`, `_pump_output`,
  parses structured events from stdout, publishes to event bus AND
  persists incremental state (matched counts etc.) to SQLite.
- Guards against runaway processes (max concurrent = 1 by default,
  configurable).

### Task 3.6 — Routes: runs list + detail (~1.5 h)
- `routes/runs.py` — `GET /` (redirects to `/runs`), `GET /runs`,
  `GET /runs/{id}`, `POST /runs`.
- Templates: `runs_list.html`, `run_detail.html`, `run_new.html`.
- HTMX for the "delete run" action (`hx-delete`, `hx-target`,
  `hx-swap`).

### Task 3.7 — Progress route + SSE (~1 h)
- `routes/progress.py` — `GET /runs/{id}/events` (SSE), `GET
  /runs/{id}/progress` (HTMX partial rendering the progress card).
- Template: `run_progress.html` with `hx-sse` connect/swap directives.

### Task 3.8 — Video routes (~1 h)
- `routes/videos.py` — `GET /videos` (list files in `input_videos/`),
  `POST /videos/upload` (multipart), `GET /videos/{name}` (serve the
  video file with byte-range support — FastAPI's `FileResponse` handles
  ranges automatically).
- File name validation (path traversal defense — reject `..`, absolute
  paths).

### Task 3.9 — Pick-card rendering + seek (~1.5 h)
- `_card.html` partial: analyst chip, stock name, prices, upside %,
  target/stop, frame thumbnail, transcript snippet.
- `data-seek` attribute drives client-side video seek.
- `static/seek.js` — 10-liner listed above.
- Chart.js embed for upside distribution (fixed-height div wrapper per
  the Walmart Chart.js rule).

### Task 3.10 — Sanitization (~30 min)
- All LLM-generated strings (analyst names, stock names, transcript
  snippets) are escaped via Jinja2's autoescape (on by default) — no
  `|safe` filters anywhere. Explicit code review checkpoint for this.
- **Fixes reviewer's finding W6 (XSS in dashboard).**

### Task 3.11 — WCAG 2.2 AA pass (~1 h)
- Manual axe-core / Lighthouse pass on each page.
- Fixes: alt text on frame thumbnails, aria-labels on icon buttons,
  focus rings, semantic headings, table headers, contrast tweaks.

### Task 3.12 — Tests (~2 h)
- `tests/test_web/test_db.py` — repository CRUD.
- `tests/test_web/test_event_bus.py` — async pub/sub.
- `tests/test_web/test_routes.py` — FastAPI TestClient, hit every route,
  assert templates render, assert HTMX partial shapes are correct.
- `tests/test_web/test_run_manager.py` — mock subprocess, verify state
  transitions.
- E2E: Playwright test hitting a real running server against a tiny
  fixture video. Kept in `tests/e2e/` — slow, opt-in via `pytest -m e2e`.

### Task 3.13 — Docs (~45 min)
- New `docs/web_ui.md` walkthrough with screenshots.
- Update `README.md` with a "Web UI" section.
- Update `AGENTS.md` architecture summary to include `src/web/`.

### Task 3.14 — Polish (~1 h)
- Empty states ("No runs yet — start one!") on every list view.
- Confirm-before-delete dialogs.
- Keyboard shortcuts: `n` for new run, `/` to focus search, `?` for
  shortcut help.
- Loading spinners during HTMX swaps.

---

## Risks & mitigations

| Risk | Likelihood | Mitigation |
|------|-----------|-----------|
| Subprocess-per-run makes cold-start slow (Ollama re-warmup, whisper model load) | Medium | Print realistic wait times in the progress UI; acceptable trade-off for isolation. Follow-on: warm-pool if needed. |
| SSE connections stay open on tab close and leak queues | Low | `finally:` cleanup in `subscribe`; add periodic pruning of dead queues. |
| Multiple runs at once trash the shared `output/` tree | Medium | Enforce `max_concurrent = 1` in RunManager by default; queue extras. |
| Video file uploads eat disk without bound | Medium | Config-driven size cap; also cap total `input_videos/` bytes with clear error. |
| Client-side video-seek fails on browsers without proper byte-range support | Low | FastAPI FileResponse handles ranges; document Chrome/Safari/Firefox as tested; degrade gracefully to a "download" button if the browser refuses seek. |
| Feature creep (auth, multi-user, cloud deploy) | HIGH | This is explicitly a single-user local tool. Any auth/multi-tenant discussion goes into a follow-on phase, not this one. YAGNI. |

---

## Definition of done

1. `python -m localocr.web` boots the server, browser at
   `http://localhost:8765` shows the runs list.
2. Uploading a small test video from the browser produces a completed
   run with live progress updates visible during the run.
3. Clicking a stock pick on the run-detail page seeks the embedded
   video to the correct timestamp.
4. axe-core / Lighthouse accessibility score ≥ 95.
5. Test suite green including E2E.
6. Docs updated.
7. Committed in ≤7 focused commits following the task groupings:
   (a) scaffold + DB, (b) event bus + run manager,
   (c) runs routes + templates, (d) progress + SSE,
   (e) videos + upload, (f) pick cards + seek, (g) tests + docs + a11y.

**Estimated effort:** 2.5-3 focused days (~18-22 hours).

---

## Sequencing note

Phase 3 is genuinely bonus. If time is short, ship Phases 1 and 2 first,
then decide: does the existing `viewer.html` need to become interactive
badly enough to justify 3 days of web work? If yes → Phase 3. If not →
capture it in the backlog and move on.

**The Zen of Python says:** "Now is better than never. Although never is
often better than *right* now." Applies especially to Phase 3.
