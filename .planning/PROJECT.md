# LOCALOCR

## What This Is

LOCALOCR is a fully local, offline-first macOS video-to-text pipeline. It
extracts frames from broadcast/screen-recording videos (financial TV like
Zee Business, CNBC), runs OCR using Apple Vision and/or EasyOCR, matches
extracted text against configurable keywords, and organizes matched
frames into categorized folders. An optional post-OCR layer uses local
Ollama vision models to extract structured data (e.g., analyst stock
picks) and render a self-contained HTML dashboard.

## Core Value

Every byte of processing stays on the user's Mac — no cloud, no API
keys, no data leaving the device — while still producing publication-
quality structured extractions from long-form video.

## Requirements

### Validated

<!-- Shipped and confirmed valuable. -->

- Full video → frames → OCR → match → organize pipeline
- Pluggable OCR engines (Apple Vision, EasyOCR, Composite)
- Post-OCR LLM analysis producing dedup'd stock-pick dashboard
- Context-mode expansion (±N frames around each anchor)
- Unicode-safe Devanagari folder organization
- Graceful `KeyboardInterrupt` handling with partial save

### Active

<!-- Current milestone: v1.1 — Efficiency & Attribution -->

- [ ] **EXTRACT-01**: Scene-change frame extraction cuts frame count 5-10x
- [ ] **EXTRACT-02**: Hybrid mode combines scene detection with fallback interval
- [ ] **EXTRACT-03**: Backward-compatible config — old runs still work unchanged
- [ ] **TRANSCRIPT-01**: Local whisper.cpp transcription runs in parallel with OCR
- [ ] **TRANSCRIPT-02**: Every matched frame gets a transcript_context field
- [ ] **TRANSCRIPT-03**: Vision-model prompts include spoken context (accuracy uplift)
- [ ] **TRANSCRIPT-04**: Dashboard displays transcript snippets per stock pick
- [ ] **WEBUI-01**: FastAPI + HTMX server browses runs, kicks off pipelines, streams progress
- [ ] **WEBUI-02**: Click-a-pick in the dashboard seeks the embedded video
- [ ] **WEBUI-03**: WCAG 2.2 AA compliant styling using Walmart palette

### Out of Scope

- **Cloud OCR / cloud LLM** — violates the offline-first core value
- **Multi-user / auth** — this is a single-user local tool; no login layer
- **Speaker diarization (pyannote.audio)** — deferred to a follow-on
  milestone; whisper-only single-speaker attribution ships first (YAGNI)
- **Mobile / Windows GUI** — macOS is the target; Windows OCR engine
  exists but no GUI plans
- **Real-time streaming ingestion** — batch video files only

## Context

- **Primary use case**: Extracting analyst stock recommendations from
  Indian financial TV (Zee Business, CNBC) for personal research.
- **Hardware**: Apple Silicon Macs (M-series) — ANE, MPS GPU, and
  spawn-context multiprocessing are all first-class citizens.
- **Existing dependencies**: ffmpeg, pyobjc-framework-Vision, easyocr
  (optional), requests (for Ollama), Pillow.
- **Prior work**: Full pipeline shipped and stable; post-OCR LLM layer
  proven on real content; standalone `viewer.html` dashboard works but
  is not interactive.

## Constraints

- **Tech stack**: Python 3.10+, no `shell=True` anywhere, subprocess
  calls use list args
- **Offline-first**: Every new dependency must run locally without
  network calls at runtime
- **Backward compatibility**: Existing `config/config.json` files must
  continue to work with each new milestone
- **Testing**: New code must ship with tests; opportunistically add
  coverage to the 5 modules flagged as untested by the 2026-06-18
  code review
- **File length**: Keep modules under 600 lines; split by cohesion, not
  by arbitrary line count
- **Security**: LLM output must be escaped before HTML injection (fix
  the XSS finding W6 during Phase 3)

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Local whisper.cpp over cloud/API transcription | Preserves offline-first core value; Metal-accelerated on Apple Silicon | — Pending (Phase 2) |
| FastAPI + HTMX for Web UI | Walmart default stack; no build step; server-side rendering keeps complexity low | — Pending (Phase 3) |
| Scene-change as opt-in via config key | Preserves backward compat; old configs work unchanged | — Pending (Phase 1) |
| Skip speaker diarization in v1.1 | YAGNI — single-speaker attribution covers 80% of value at 20% of setup pain | — Pending (Phase 2) |
| SQLite for run history in Web UI | Avoid Alembic overhead; schema is small; user_version pragma for migrations | — Pending (Phase 3) |
| Subprocess-per-run in Web UI | Isolation from crashes; fresh Ollama/whisper/easyocr init per run | — Pending (Phase 3) |

---
*Last updated: 2026-07-21 during GSD initialization for v1.1 roadmap*
