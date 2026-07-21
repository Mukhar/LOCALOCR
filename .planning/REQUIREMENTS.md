# Requirements: LOCALOCR v1.1

**Defined:** 2026-07-21
**Core Value:** Every byte of processing stays on the user's Mac — no cloud, no API keys, no data leaving the device — while still producing publication-quality structured extractions from long-form video.

## v1.1 Requirements

Requirements for the "Efficiency & Attribution" milestone. Each maps to
one or more roadmap phases.

### Extraction

- [ ] **EXTRACT-01**: `extraction_mode: "scene"` in config triggers ffmpeg
  scene-change detection instead of fixed-interval extraction, producing
  ≥5x fewer frames on typical broadcast content with no loss of unique
  matched keywords vs the interval baseline
- [ ] **EXTRACT-02**: `extraction_mode: "hybrid"` combines scene detection
  with a `max_gap_seconds` fallback tick so long static shots still get
  sampled
- [ ] **EXTRACT-03**: Config files without `extraction_mode` set default
  to `"interval"` and behave identically to today (backward compat)
- [ ] **EXTRACT-04**: `scene_config.threshold`, `min_gap_seconds`, and
  `max_gap_seconds` are validated; invalid values fail fast with a
  helpful error
- [ ] **EXTRACT-05**: Downstream modules (organizer, context expander,
  post_ocr, viewer) require zero changes — frame naming
  `frame_NNNN_XXmYYs.png` is preserved across all extraction modes
- [ ] **EXTRACT-06**: Benchmark script proves the 5x reduction claim on
  a real video in `input_videos/`

### Transcript

- [ ] **TRANSCRIPT-01**: `transcript_config.enabled: true` runs
  whisper.cpp on the video's audio track in a background thread parallel
  with OCR; total pipeline wall time ≤ 1.3x the no-transcript baseline
- [ ] **TRANSCRIPT-02**: `output/metadata/transcript.json` is produced
  with normalized segments containing `start`, `end`, `text`, `speaker`
- [ ] **TRANSCRIPT-03**: Every matched frame in `ocr_results.json` gets
  a `transcript_context` field with `before`, `at`, `after`, `speaker`
  (context window configurable via `context_window_seconds`)
- [ ] **TRANSCRIPT-04**: `post_ocr_pipeline` vision prompts include the
  spoken context; the dedup'd picks JSON carries `transcript_context`
- [ ] **TRANSCRIPT-05**: `viewer.html` renders a collapsible "Spoken
  context" section under each stock-pick card
- [ ] **TRANSCRIPT-06**: Missing whisper binary, missing model, or
  audio-less videos degrade gracefully — pipeline completes with a
  clear log message, never crashes
- [ ] **TRANSCRIPT-07**: `--ocr-only` mode skips transcription with a
  clear log line (no video available)
- [ ] **TRANSCRIPT-08**: Existing XSS surfaces in the HTML dashboard
  (finding W6) are fixed while touching the dashboard code
- [ ] **TRANSCRIPT-09**: Pipeline step counter inconsistency (finding
  B2) is fixed in the same pass

### Web UI

- [ ] **WEBUI-01**: `python -m localocr.web` boots a FastAPI server on
  `http://localhost:8765` with browser-visible landing page
- [ ] **WEBUI-02**: Landing page lists past runs (video, timestamp,
  matched count, status); sortable, filterable, keyboard-navigable
- [ ] **WEBUI-03**: "New Run" page picks or uploads a video, selects a
  config profile, and starts a pipeline run
- [ ] **WEBUI-04**: Live-progress page streams phase/frame/matched
  counters and log tail via Server-Sent Events
- [ ] **WEBUI-05**: Run-detail page embeds the source video; clicking a
  stock-pick card seeks the video to that frame's timestamp
- [ ] **WEBUI-06**: All run metadata persists in `output/localocr.sqlite`;
  survives server restart
- [ ] **WEBUI-07**: Zero JavaScript build step; HTMX + ~10 lines of
  vanilla JS for the video-seek handler
- [ ] **WEBUI-08**: Passes WCAG 2.2 AA — contrast ≥ 4.5:1, keyboard
  navigation, ARIA labels, visible focus indicators
- [ ] **WEBUI-09**: Uses Walmart palette via `cp_walmart_colors` skill
- [ ] **WEBUI-10**: Standalone `viewer.html` continues to be generated
  by `post_ocr_pipeline` for backward compat

## v2 Requirements

Deferred to future milestones. Tracked but not in current roadmap.

### Speaker Diarization

- **DIARIZE-01**: `pyannote.audio` integration for multi-speaker
  attribution (replaces whisper's single-speaker segments)
- **DIARIZE-02**: Dashboard shows per-speaker filter and speaker names

### Resume / Incremental Mode

- **RESUME-01**: Frame-hash manifest lets subsequent runs skip OCR on
  unchanged frames (huge win for keyword-tuning iteration)

### Multi-Video Batch

- **BATCH-01**: `python main.py --batch ./videos/*.mp4` processes many
  videos with a shared output tree and per-video subfolders

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| Cloud OCR / cloud LLM | Violates offline-first core value |
| Multi-user / authentication in the Web UI | Single-user local tool by design |
| Real-time streaming ingestion | Batch video files only |
| Mobile / Windows GUI | macOS is the target platform |
| Speaker diarization in v1.1 | YAGNI — deferred to v2 (DIARIZE-01) |
| Web UI auth / cloud deploy | Out of charter; run locally only |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| EXTRACT-01 | Phase 1 | Pending |
| EXTRACT-02 | Phase 1 | Pending |
| EXTRACT-03 | Phase 1 | Pending |
| EXTRACT-04 | Phase 1 | Pending |
| EXTRACT-05 | Phase 1 | Pending |
| EXTRACT-06 | Phase 1 | Pending |
| TRANSCRIPT-01 | Phase 2 | Pending |
| TRANSCRIPT-02 | Phase 2 | Pending |
| TRANSCRIPT-03 | Phase 2 | Pending |
| TRANSCRIPT-04 | Phase 2 | Pending |
| TRANSCRIPT-05 | Phase 2 | Pending |
| TRANSCRIPT-06 | Phase 2 | Pending |
| TRANSCRIPT-07 | Phase 2 | Pending |
| TRANSCRIPT-08 | Phase 2 | Pending |
| TRANSCRIPT-09 | Phase 2 | Pending |
| WEBUI-01 | Phase 3 | Pending |
| WEBUI-02 | Phase 3 | Pending |
| WEBUI-03 | Phase 3 | Pending |
| WEBUI-04 | Phase 3 | Pending |
| WEBUI-05 | Phase 3 | Pending |
| WEBUI-06 | Phase 3 | Pending |
| WEBUI-07 | Phase 3 | Pending |
| WEBUI-08 | Phase 3 | Pending |
| WEBUI-09 | Phase 3 | Pending |
| WEBUI-10 | Phase 3 | Pending |
