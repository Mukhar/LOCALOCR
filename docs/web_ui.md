# Web UI Guide

LOCALOCR ships an optional FastAPI web app for browser-based pipeline
management. This document walks a new user through the UI end-to-end.

The web UI is a **thin wrapper** around the existing pipeline -- the
same `run_pipeline()` function that `main.py` calls also runs behind
`POST /runs`. Nothing about the CLI or the standalone `viewer.html`
generator changes; the web UI is additive.

---

## Quick start

```bash
# 1. Install web deps (already in requirements.txt)
uv pip install -r requirements.txt

# 2. Boot the server (localhost only by design)
python -m src.web

# 3. Open the UI
open http://localhost:8765
```

That's it. Runs are persisted in `output/localocr.sqlite`.

---

## Architecture at a glance

```
Browser (HTMX + Tailwind + tiny vanilla JS)
   |
   |  HTTP/SSE
   |
FastAPI app  (src/web/__init__.py -> create_app())
   |
   |-- /runs, /runs/new, /runs/{id}    (routes/runs.py)
   |-- /videos, /videos/upload         (routes/videos.py)
   |-- /runs/{id}/events (SSE stream)  (routes/progress.py)
   |
   |-- RunManager (services/run_manager.py)
   |        spawns:
   |
   |   python -m src.web.services.runner_subprocess --run-id N --config JSON
   |        (fresh Python process per run for isolation)
   |
   |-- SQLite  output/localocr.sqlite (services/db.py)
   |-- EventBus (services/event_bus.py -- async pub/sub)
```

Design principles that shaped this stack:

- **Zero JS build step**. Tailwind + HTMX + one 15-line seek.js file
  loaded from CDN or `/static`. No npm, no bundler, no watch scripts.
- **Subprocess per run**. Isolates PyObjC/torch state, allows clean
  SIGTERM cancellation, protects the server from pipeline crashes.
- **max_concurrent = 1**. OCR + whisper + Ollama are heavy; running
  two pipelines at once would thrash the machine. Extra requests
  queue and emit a `queued` event.
- **Localhost binding**. The server processes uploaded videos with
  local subprocesses. Exposing to LAN is a footgun; if you need it,
  put a reverse proxy in front with auth.

---

## Pages

### Runs List (`/runs`)

<!-- TODO: screenshot -->

Table of all past runs, most recent first. Columns:

| Column   | What it shows                                                |
|----------|--------------------------------------------------------------|
| Started  | UTC timestamp when RunManager inserted the row               |
| Video    | Basename of the video file (click for detail)                |
| Mode     | `accurate` / `context` / `scene` / `hybrid`                  |
| Frames   | Total frames extracted (populated on completion)             |
| Matched  | Frames whose OCR text matched a keyword                      |
| Status   | `running` / `completed` / `failed` / `interrupted` / `queued`|
| Actions  | Delete button (uses HTMX `hx-delete` for zero-JS row removal) |

Empty state: a friendly "No runs yet" panel with a big "Start Your
First Run" CTA.

### New Run (`/runs/new`)

<!-- TODO: screenshot -->

Simple two-field form:

- **Video** -- dropdown populated from `input_videos/`. If the
  directory is empty, the form shows a friendly "No videos found"
  message and disables the submit button.
- **Config profile** -- choose the base config file. Options:
  - `default` -- `config/config.json` (the standard config)
  - `transcript` -- `config/config.transcript.example.json`
    (Whisper + audio transcription enabled)

The chosen profile's `video_path` is overridden with your selection
above. Submit does a 303 See Other redirect to the new run's detail
page.

### Run Detail (`/runs/{id}`)

<!-- TODO: screenshot -->

Layout top-to-bottom:

1. **Header**: run number + status badge + timing + counts
2. **Live Progress panel** (only for running runs) -- server-sent
   events stream from the pipeline subprocess. Shows Frames,
   Matched, current Phase, and a scrollable log tail (last 200
   lines, color-coded by level).
3. **Video player**: embedded `<video>` with byte-range enabled via
   FastAPI's `FileResponse`. Playback + seek works natively.
4. **Picks grid**: one card per stock pick. Each card has:
   - Analyst chip + stock pick title
   - Current / Target / Stop Loss prices (color-coded)
   - Optional collapsible "Spoken context" section (transcript
     +/-8s around the frame)
   - **Seek button**: clicks the timestamp -- seek.js jumps the
     embedded video to that time and plays.

### Live progress (SSE)

When a run is `running`, the detail page lazy-loads the progress
panel via HTMX (`hx-get /runs/{id}/progress` on `load`). The panel
opens an SSE connection to `/runs/{id}/events` via HTMX's SSE
extension. Every event the pipeline subprocess emits (log lines,
summary, done) flows through the event bus and lands in the
browser as an `htmx:sseMessage` DOM event, which a small vanilla-JS
listener dispatches to the right DOM update:

- `log` -> append log line
- `summary` -> update Frames + Matched counters
- `done` -> flip status badge + reload the page 1.5s later

Kill your browser tab mid-run: the SSE endpoint polls
`request.is_disconnected()` between events and frees the
subscriber-side queue promptly.

### Click-to-seek

`src/web/static/seek.js` is 15 lines. It's a delegated click handler:
any element with `data-seek="<seconds>"` triggers a `video.currentTime`
+ `play()` on the `#source-video` element. Works for
HTMX-swapped-in elements too (delegation at document root).

---

## Endpoints reference

| Method | Path                       | Purpose                                              |
|--------|----------------------------|------------------------------------------------------|
| GET    | `/`                        | 307 redirect to `/runs`                              |
| GET    | `/health`                  | `{"status": "ok", "db_path": "..."}`                 |
| GET    | `/runs`                    | Runs list (HTML)                                     |
| GET    | `/runs/new`                | New-run form (HTML)                                  |
| POST   | `/runs`                    | Start a run; 303 -> `/runs/{id}`                     |
| GET    | `/runs/{id}`               | Run detail (HTML)                                    |
| DELETE | `/runs/{id}`               | Delete a run + its picks; empty 200 for HTMX         |
| GET    | `/runs/{id}/progress`      | Progress panel fragment (HTML)                       |
| GET    | `/runs/{id}/events`        | SSE stream of pipeline events                        |
| GET    | `/videos`                  | JSON list of `input_videos/` contents                |
| POST   | `/videos/upload`           | Multipart upload; 500 MB cap, extension allowlist    |
| GET    | `/videos/{name}`           | Serve a video with byte-range support                |
| GET    | `/output/*`                | Static: matched frames, viewer.html                  |
| GET    | `/static/*`                | Static: seek.js and future assets                    |

---

## Troubleshooting

| Symptom                              | Cause / fix                                                                 |
|--------------------------------------|-----------------------------------------------------------------------------|
| `address already in use` on 8765     | `lsof -ti:8765 \| xargs kill -9` -- prior server didn't shut down cleanly    |
| No videos in the New Run dropdown    | Drop a .mp4/.mkv/.webm into `input_videos/`, or use `POST /videos/upload`   |
| Upload returns 413                   | File larger than 500 MB. Split, transcode, or bump `_MAX_UPLOAD_MB`         |
| Upload returns 400                   | Unsupported extension. Allowed: mp4, webm, mkv, mov, m4v, avi               |
| Run stays `running` after finish     | Pump thread didn't hit the finally block -- check server logs for a crash    |
| Live progress never appears          | Check browser console for HTMX SSE errors; verify `/runs/{id}/events` opens |
| SQLite `database is locked`          | Multi-server against the same DB. Only run one server per DB file.          |
| DB corrupted / weird state           | `rm output/localocr.sqlite` -- schema recreates on next boot                |
| Standalone viewer.html missing picks | Regenerate via `python post_ocr_pipeline.py`                                |

---

## Config profiles

`_CONFIG_PROFILES` in `src/web/routes/runs.py`:

| Profile      | File                                       | When to use                                      |
|--------------|--------------------------------------------|--------------------------------------------------|
| `default`    | `config/config.json`                       | Standard OCR pipeline (whatever's in config)     |
| `transcript` | `config/config.transcript.example.json`    | Whisper-based audio transcription enabled        |

Adding a new profile is a two-line change:

```python
# src/web/routes/runs.py
_CONFIG_PROFILES = {
    "default":    "config/config.json",
    "transcript": "config/config.transcript.example.json",
    "myprofile":  "config/my_profile.json",   # <- new entry
}
```

Then update the `<select>` in `run_new.html`.

---

## Testing

```bash
# Unit + integration tests (fast, run by default)
pytest tests/

# End-to-end Playwright smoke tests (opt-in)
uv pip install pytest-playwright --index-url <walmart index>
playwright install chromium
pytest -m e2e tests/e2e/
```

The e2e suite boots a real server, drives Chromium, and clicks through
the UI. It's opt-in because Playwright + a browser download add ~300 MB
and 10-30 s per test.

---

## Backward compatibility

The web UI is **additive**. Everything from Phases 1 and 2 keeps
working unchanged:

- `python main.py ./config/config.json` -- runs the pipeline the old way
- `python main.py --ocr-only --frames-dir ./output/all_frames` -- OCR-only
- `python post_ocr_pipeline.py` -- still generates `output/viewer.html`

The standalone `viewer.html` and the web UI's Run Detail page render
the same data from different sources: the former reads the JSON
metadata files, the latter reads the SQLite DB. Pick whichever
matches your workflow.

---

## Related documentation

- [README.md](../README.md) -- top-level project overview
- [AGENTS.md](../AGENTS.md) -- architecture guide for code contributors
- [docs/setup_whisper.md](./setup_whisper.md) -- transcript profile setup
