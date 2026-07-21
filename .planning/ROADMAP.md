# Roadmap: LOCALOCR v1.1 — Efficiency & Attribution

## Overview

Three sequenced phases building toward a faster, richer, more
interactive LOCALOCR. Phase 1 (scene-change extraction) is the biggest
multiplier — cutting frame count 5-10x makes every downstream step
proportionally cheaper. Phase 2 (whisper.cpp transcript attribution)
transforms output quality by giving vision prompts and dashboard cards
real spoken context. Phase 3 (FastAPI Web UI) is a bonus that makes
existing capability dramatically more usable via live progress and
click-to-seek video playback.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases would be urgent insertions (none planned yet)

- [ ] **Phase 1: Scene-Change Frame Extraction** — Strategy-pattern dispatch for interval / scene / hybrid extraction modes, preserving backward compat and frame naming
- [ ] **Phase 2: Whisper Transcript Attribution** — Local whisper.cpp transcription in parallel with OCR; enriches matched frames + vision prompts + dashboard with spoken context
- [ ] **Phase 3: FastAPI Web UI (Bonus)** — Live-progress server with SQLite run history, upload/run flow, and click-to-seek video playback

## Phase Details

### Phase 1: Scene-Change Frame Extraction
**Goal**: Replace fixed-interval frame extraction with an opt-in scene-detection strategy that cuts frame count 5-10x on broadcast content, preserving the existing frame-naming contract so zero downstream modules need changes.
**Depends on**: Nothing (extends `src/extractor/frame_extractor.py` only)
**Requirements**: EXTRACT-01, EXTRACT-02, EXTRACT-03, EXTRACT-04, EXTRACT-05, EXTRACT-06
**Success Criteria** (what must be TRUE):
  1. Running `python main.py ./config/config.json` with an unmodified pre-v1.1 config produces identical output to today (backward compat proof)
  2. Setting `extraction_mode: "scene"` in config produces ≥5x fewer frames than the interval baseline on `input_videos/june22zeebiz.mp4` with no loss of unique matched keywords
  3. Setting `extraction_mode: "hybrid"` samples at scene changes AND at `max_gap_seconds` intervals, so long static shots still get frames
  4. Invalid config values (bad mode, threshold out of range) fail fast at startup with a clear error message
  5. `benchmark_extraction.py` script exists, runs all three modes on the same video, and prints a comparison table
**Plans**: 3 plans

Plans:
- [ ] 01-01: Refactor `frame_extractor.py` into strategy dispatch (extract interval path into helper, add mode routing, keep behavior identical)
- [ ] 01-02: Add scene + hybrid extractors with PTS parsing and debounce
- [ ] 01-03: Tests, benchmark script, and docs

### Phase 2: Whisper Transcript Attribution
**Goal**: Add local whisper.cpp transcription that runs in parallel with OCR, enriches every matched frame with spoken context, feeds that context to the post-OCR vision prompts, and renders it in the HTML dashboard — while degrading gracefully when whisper is unavailable.
**Depends on**: Phase 1 (soft — reduced frame counts make correlation cheaper, but Phase 2 works without it)
**Requirements**: TRANSCRIPT-01, TRANSCRIPT-02, TRANSCRIPT-03, TRANSCRIPT-04, TRANSCRIPT-05, TRANSCRIPT-06, TRANSCRIPT-07, TRANSCRIPT-08, TRANSCRIPT-09
**Success Criteria** (what must be TRUE):
  1. End-to-end run on a real video produces `output/metadata/transcript.json` with sensible whisper segments and every matched frame's metadata gains a `transcript_context` block
  2. `viewer.html` shows a collapsible "Spoken context" section under each stock-pick card
  3. Wall-clock time for the full pipeline with transcript enabled is ≤ 1.3x the wall-clock time with transcript disabled (parallelism actually works)
  4. Deleting the whisper binary and re-running produces a working run — transcription skipped with a clear log line, everything else succeeds
  5. XSS-safe HTML output (all LLM-derived strings are escaped) and consistent pipeline step counter labels
**Plans**: 4 plans

Plans:
- [ ] 02-01: Extract shared `src/common/subprocess_utils.py` (DRY prep) and add `src/transcript/audio_extractor.py`
- [ ] 02-02: whisper.cpp subprocess wrapper + pure-function correlator
- [ ] 02-03: `pipeline_runner` integration (background thread + graceful degradation) and `post_ocr_pipeline` + dashboard enrichment (with XSS fix)
- [ ] 02-04: Tests, config example, and docs (README + AGENTS.md + setup guide)

### Phase 3: FastAPI Web UI (Bonus)
**Goal**: Build a local FastAPI + HTMX + Tailwind + SQLite web app that browses runs, uploads/starts new runs with live SSE progress, and renders the stock-picks dashboard with click-to-seek embedded video playback — zero JS build step, WCAG 2.2 AA compliant.
**Depends on**: Phase 1 & 2 (soft — everything Phase 3 shows is more compelling with fewer frames + transcript snippets)
**Requirements**: WEBUI-01, WEBUI-02, WEBUI-03, WEBUI-04, WEBUI-05, WEBUI-06, WEBUI-07, WEBUI-08, WEBUI-09, WEBUI-10
**Success Criteria** (what must be TRUE):
  1. `python -m localocr.web` boots the server and the browser shows the runs list at `http://localhost:8765`
  2. Uploading a small test video from the browser produces a completed run with real-time progress visible during the run
  3. Clicking a stock-pick card on the run-detail page seeks the embedded video to the exact frame timestamp and starts playback
  4. axe-core / Lighthouse accessibility audit scores ≥ 95
  5. Standalone `viewer.html` continues to be generated by `post_ocr_pipeline` (backward compat)
**Plans**: 5 plans

Plans:
- [ ] 03-01: Package scaffold, SQLite schema + repository, event bus
- [ ] 03-02: Subprocess run manager and structured-event runner_subprocess
- [ ] 03-03: Routes + Jinja2 templates (runs list, run detail, run new, videos)
- [ ] 03-04: SSE live-progress stream and click-to-seek pick cards
- [ ] 03-05: Accessibility pass, tests (unit + Playwright E2E), and docs

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Scene-Change Frame Extraction | 0/3 | Not started | - |
| 2. Whisper Transcript Attribution | 0/4 | Not started | - |
| 3. FastAPI Web UI (Bonus) | 0/5 | Not started | - |
