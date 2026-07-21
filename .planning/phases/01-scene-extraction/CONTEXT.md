# Phase 1 Context — Scene-Change Frame Extraction

**Purpose:** Lock decisions from user before planning proceeds. Plans MUST honor
these decisions verbatim. Anything in "Deferred" is out of scope for this phase.

**Source:** Extracted from user conversation on 2026-07-21 (feature proposal
"scene-change extraction" from LOCALOCR feature brainstorm) and codified during
GSD initialization.

---

## Decisions

**LOCKED — plans MUST implement these exactly. Plan-checker will flag any deviation.**

### D1 — Three extraction modes: interval / scene / hybrid

The extractor must support three named modes via a single `extraction_mode`
config key:

- `"interval"` — current fixed-fps behavior, unchanged
- `"scene"` — ffmpeg scene-change detection via `select='gt(scene,THRESHOLD)'`
- `"hybrid"` — scene detection PLUS a `max_gap_seconds` fallback tick

**Non-negotiable:** all three must be present in the shipped code. No "we'll add
hybrid later" — the user explicitly asked for hybrid so long static shots still
get sampled.

### D2 — Backward compatibility is absolute

Configs written for LOCALOCR v1.0 (no `extraction_mode` key at all) must produce
BYTE-IDENTICAL output to the pre-Phase-1 build. If a v1.0 user runs
`python main.py ./config/config.json` after this phase ships and gets a different
frame count, that is a P0 regression.

**Enforcement mechanism:** default `extraction_mode` when missing = `"interval"`,
and interval mode's ffmpeg invocation + timestamp computation must not change.

### D3 — Frame naming contract is preserved

The filename pattern `frame_NNNN_XXmYYs.png` is a downstream API contract with
zero flexibility. Organizer, context_expander, post_ocr_pipeline, viewer.html
all parse this exact shape. Scene/hybrid modes must produce filenames of the
same shape — the `XXmYYs` portion just carries real PTS-derived timestamps
instead of `(N-1) * interval` synthetic ones.

### D4 — Strategy-pattern dispatch, not class hierarchy

Three modes → a module-level dispatch dict + three helper functions.
`_EXTRACTORS = {"interval": ..., "scene": ..., "hybrid": ...}`.

**No** `OCREngine`-style ABC hierarchy for extractors. Three cases don't
warrant it (YAGNI). The dispatch pattern also mirrors how `text_matcher._is_match`
handles its three match modes — codebase-consistent.

### D5 — PTS-driven timestamps, not synthetic

Scene and hybrid modes MUST parse actual PTS values from ffmpeg's `showinfo`
filter and use those for the `XXmYYs` portion of filenames. Do NOT fake
timestamps by counting kept frames × interval. That would break correlation
with the audio timeline (Phase 2 depends on this).

**Fallback:** if PTS-count ≠ kept-file-count after ffmpeg runs (parser drift,
unusual container), fall back to synthetic timestamps with a WARNING log line.
The pipeline must not crash on this edge case.

### D6 — Config validation is fail-fast

Invalid `extraction_mode` values, out-of-range `threshold` (not in `[0.0, 1.0]`),
negative `min_gap_seconds` or non-positive `max_gap_seconds` must raise
`FrameExtractionError` at pipeline startup with the offending key + value in
the message. Do not silently correct or default.

### D7 — Benchmark is a shipped artifact

`benchmark_extraction.py` must be committed alongside the feature. It's how we
prove the ≥5x claim and how future regressions get caught. Not just a "run this
once" thing — a permanent utility.

### D8 — Zero *behavioral* downstream changes

**Revised 2026-07-21 after plan-check pass 1.** Original intent: no downstream
module changes behavior. Practical carve-out: `pipeline_runner.py` needs a
**mechanical signature-passthrough** change (one line) so it forwards the
full `config` dict to `extract_frames()` — because `extract_frames` must read
`extraction_mode` and `scene_config` from somewhere.

**Allowed:**
- `pipeline_runner.py`: change `extract_frames(video_path, str(frames_dir), interval)` to `extract_frames(video_path, str(frames_dir), interval, cfg=config)` (or equivalent). Zero behavior change when `extraction_mode` is absent.
- `extract_frames()` signature: add a `cfg: dict | None = None` keyword parameter, defaults to `{}`. When None/empty, behaves exactly as today.

**Still forbidden:**
- `organizer.py`, `context_expander.py`, `post_ocr_pipeline.py`, `text_matcher.py`,
  `viewer.html` — zero changes. If any of these need changes, it's a design failure.
- Changing how `pipeline_runner.py` structures the pipeline sequence, logs, or
  builds its summary dict. The one-line signature-passthrough is the entire
  allowed edit surface.

---

## Claude's Discretion

**Freedom areas — planner/executor picks the approach. Don't flag.**

- Exact regex for parsing `showinfo` output — as long as it extracts the
  `pts_time` float, any working pattern is fine
- Which existing tests to touch when doing the refactor (Task 01-01 Task 1)
- How to structure `_finalize_frames` internals (only its public signature
  and behavior are locked)
- Debounce algorithm — linear scan is fine; anyone reaching for a heap is
  overengineering
- Log message wording (must be informative; exact wording is free)
- Whether `_EXTRACTORS` lives at module top or inside a factory function

---

## Deferred Ideas

**Out of scope for Phase 1. Plans must NOT include these.**

- Per-video adaptive thresholds — user didn't ask; would balloon scope
- ML-based scene detection (PySceneDetect with content-detector) — ffmpeg's
  built-in scene score is sufficient and dependency-free
- Multi-pass extraction (extract at high FPS, then dedupe via perceptual hash)
  — captured as future work in the reference plan; not in this phase
- Scene detection for audio-only streams — not applicable
- Extracting SPS/PPS metadata or other non-timestamp info from `showinfo`
- Making `extraction_mode` per-video (right now it's per-config; if a user
  needs multiple modes, they run the pipeline twice)
- Progress bar / TUI improvements during extraction — separate concern
- Extracting frames as JPEG instead of PNG — PNG is the contract

---

## Requirements Coverage

Phase 1 addresses these requirements from `.planning/REQUIREMENTS.md`:

| ID | Requirement | Plan | Verification |
|----|-------------|------|--------------|
| EXTRACT-01 | Scene mode ≥5x fewer frames, no keyword loss | 01-02 (impl), 01-03 (proof) | benchmark_extraction.py summary line |
| EXTRACT-02 | Hybrid mode with max_gap fallback | 01-02 | Manual smoke + Task 6 test |
| EXTRACT-03 | Backward compat — old configs unchanged | 01-01 | Task 4 test + verification checklist |
| EXTRACT-04 | Fail-fast config validation | 01-01, 01-02 | Task 3 (01-01) + Task 5 (01-02) tests |
| EXTRACT-05 | Frame naming preserved, zero downstream changes | 01-01 (via _finalize_frames) | Manual pipeline run |
| EXTRACT-06 | Benchmark script proves the 5x claim | 01-03 | Task 2 output |

---

## Success Criteria Recap

Copied from ROADMAP for plan-checker convenience:

1. Running `python main.py ./config/config.json` with an unmodified pre-v1.1
   config produces identical output to today
2. `extraction_mode: "scene"` produces ≥5x fewer frames with no lost matched
   keywords on `input_videos/june22zeebiz.mp4`
3. `extraction_mode: "hybrid"` samples scene changes AND at `max_gap_seconds`
   intervals
4. Invalid config values fail fast with a clear error message
5. `benchmark_extraction.py` exists and prints a comparison table
