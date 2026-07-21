---
phase: 02-transcript
plan: 03
subsystem: transcript
tags: [pipeline-orchestration, parallel-transcription, xss-fix, step-counter-fix, prompt-augmentation]

requires:
  - phase: 02-02
    provides: whisper transcriber, correlator, Segment dataclass, error hierarchy
provides:
  - src.transcript.kickoff_transcription (background-thread Future factory; never raises)
  - src.transcript.pipeline_glue (module wrapper that swallows every degradation-matrix failure)
  - pipeline_runner: whisper runs parallel with OCR (matched_results carries transcript_context)
  - pipeline_runner: consistent [Step N/TOTAL] labels via constants (fixes review finding B2)
  - post_ocr_pipeline: transcript_context in vision prompt + phase1 payload + dashboard
  - viewer.html: collapsible "Spoken context" section per pick card
  - viewer.html: server-side html.escape on every LLM-derived string (fixes review finding W6)
affects: [02-04-PLAN (tests + docs + config example land here)]

tech-stack:
  added:
    - "html.escape (stdlib) for server-side XSS defense"
  patterns:
    - "Background-thread Future returned to caller; caller awaits after all sequential steps -> parallelism is opportunistic (whisper finishes 'while' OCR runs, wait time is often zero)"
    - "Belt-and-suspenders XSS defense: (a) recursive _escape_pick_strings on every pick BEFORE json.dumps, (b) </-to-<\\/ substitution on the resulting JSON so a bypass of (a) still can't break out of <script>"
    - "Optional prompt augmentation: transcript_context is appended to EXTRACTION_PROMPT as short quoted snippets so the model treats it as corroborating dialogue, not commands"
    - "Server-side rendering of dashboard state means no runtime dependency on client-side escape functions; the JSON contains inert entities"
    - "OCR-only mode logs 'Transcription skipped: --ocr-only mode has no video to extract audio from' so users know why transcript.json is absent"

key-files:
  created:
    - src/transcript/pipeline_glue.py (~150 lines)
  modified:
    - src/transcript/__init__.py (re-exports kickoff_transcription)
    - src/pipeline/pipeline_runner.py (kickoff + await + enrich + step-counter fix + OCR-only skip log)
    - post_ocr_pipeline.py (5 new helpers + phase1_extract signature + _enrich_with_frame_paths ctx fallback + DEDUP prompt rule + card template + CSS + _build_html escape/replace)

key-decisions:
  - "Background thread fires RIGHT AFTER frame extraction and awaited AFTER Ollama analysis - maximum parallel window. Whisper base.en on M-series finishes in seconds; OCR takes minutes; on any realistic broadcast the await is a no-op"
  - "Single-worker ThreadPoolExecutor (max_workers=1) because each pipeline invocation transcribes ONE video. A larger pool is dead weight"
  - "kickoff_transcription NEVER raises. Every failure mode from the degradation matrix (no audio, missing binary, missing model, whisper crash, unexpected exception) is caught, logged as WARNING, and turns into a None Future.result(). Rationale: transcription is auxiliary; a whisper install issue must not break the OCR pipeline the user actually cares about"
  - "OCR-only pipeline intentionally SKIPS transcription with an explicit info log. Reasoning: no video file means no audio to extract - there is no correct behavior to run"
  - "TRANSCRIPT-07 (skip log) fires only when transcript_config.enabled=true. If the user hasn't enabled transcription, silence is correct"
  - "Step counter fix (B2): full pipeline uses TOTAL=5 across ALL step labels; OCR-only uses TOTAL=4. Constants at module top document the invariant. sed-based batch replace + manual OCR-only touchup to avoid regex-explosion"
  - "Transcript context threads into the vision prompt as formatted quotes with a header ('Spoken context (+/-8s around this frame):'). Deliberately not phrased as instructions so the model doesn't try to obey it"
  - "DEDUP prompt gets a 'preserve transcript_context verbatim' instruction PLUS a Python-side recovery via _enrich_with_frame_paths (first-frame-wins by stockPick lower(strip())). Belt AND suspenders because LLMs drop optional fields under pressure"
  - "Server-side html.escape is applied recursively (strings inside dicts and lists both) so nested transcript_context.after can't sneak an unescaped payload past the top-level pick escape"
  - "Also do a </-to-<\\/ substitution on the emitted JSON. This is defense-in-depth: JavaScript parses <\\/script> identically to </script>, but the HTML parser will NOT treat <\\ as the start of a script-close tag. So even a hypothetical escape-bypass can't break out of the <script> block"
  - "'Spoken context' section rendered as native <details> element - zero JavaScript for the toggle, works with JS disabled, keyboard-accessible via Space/Enter"
  - "Empty ctx (missing OR all fields empty) renders ZERO HTML - no ghost 'Before: \"\"' rows, backwards-compatible with pre-Phase-2 dashboards"

patterns-established:
  - "Any LLM-derived string that lands in HTML MUST pass through _escape_pick_strings (or html.escape() directly). No client-side escape function - the JSON contains inert entities by the time it hits the browser"
  - "Background-thread pipeline augmentations return a Future or None; the runner awaits after unrelated steps to keep parallel work maximally overlapping"
  - "New pipeline logs use the module-level TOTAL_STEPS constants (or their inlined values) - no more ad-hoc '/4' vs '/5' inconsistency"
  - "Optional-field metadata (transcript_context, is_context, matched_keywords) MUST be gated by 'if present' checks in downstream consumers so old runs and disabled configs Just Work"

requirements-completed:
  - TRANSCRIPT-01  # kickoff_transcription runs parallel with OCR
  - TRANSCRIPT-04  # matched frames carry transcript_context in ocr_results.json
  - TRANSCRIPT-05  # phase 1 vision prompt includes spoken context addendum
  - TRANSCRIPT-06  # every graceful-failure path logged with explicit skip reason
  - TRANSCRIPT-07  # OCR-only mode logs 'Transcription skipped: --ocr-only mode has no video'
  - TRANSCRIPT-08  # viewer.html renders collapsible <details> Spoken Context section
  - TRANSCRIPT-09  # server-side html.escape on LLM strings + </-to-<\/ defense (W6 fix)
requirements-partial:
  - TRANSCRIPT-02  # Segment + transcribe already provided by 02-02; wiring lands here
  - TRANSCRIPT-03  # enrich_ocr_results already provided by 02-02; consumer wired here

coverage:
  - id: D1
    description: "run_pipeline kicks off transcription in a background thread AFTER frame extraction and BEFORE OCR, then awaits after Ollama analysis so both run in parallel"
    requirement: TRANSCRIPT-01
    verification:
      - kind: manual
        ref: "grep 'kickoff_transcription' src/pipeline/pipeline_runner.py -> line 165 (post-extract, pre-OCR); grep 'transcript_future.result' -> line 219 (post-Ollama)"
        status: pass
      - kind: unit
        ref: "tests/test_transcript/... (67/67 still green - no regression from wiring)"
        status: pass
    human_judgment: false
  - id: D2
    description: "Every failure mode from the degradation matrix results in a completed pipeline with a clear log line - never a crash"
    requirement: TRANSCRIPT-06
    verification:
      - kind: manual
        ref: "src/transcript/pipeline_glue.py catches NoAudioStreamError, AudioExtractionError, WhisperNotAvailableError, WhisperFailureError, and bare Exception. All log WARNING and return None. Smoke test with /nowhere.mp4 -> 'Transcription skipped (audio extraction failed): Video not found'"
        status: pass
    human_judgment: false
  - id: D3
    description: "post_ocr vision prompts include the transcript_context block when present"
    requirement: TRANSCRIPT-05
    verification:
      - kind: manual
        ref: "post_ocr_pipeline._build_transcript_addendum smoke: ctx={before,at,after,speaker=None} -> 'Spoken context (+/-8s around this frame):\\nBefore this moment: \"Welcome back.\"\\nAt this moment: \"Top pick...\"\\nAfter this moment: \"Stop loss...\"'. Speaker=None correctly omits the 'Attributed to:' line"
        status: pass
    human_judgment: false
  - id: D4
    description: "viewer.html renders a collapsible Spoken Context section per stock-pick card"
    requirement: TRANSCRIPT-08
    verification:
      - kind: manual
        ref: "Smoke test _build_html with transcript_context on pick 1 (no ctx on pick 2). Grep confirms '.ctx-body' CSS, 'Spoken context' summary text, and ctx.before/at/after/speaker paths in the card() template all present"
        status: pass
    human_judgment: true  # Visual layout / UX check requires opening the file in a browser
  - id: D5
    description: "Pipeline step counter labels are consistent (fixes finding B2)"
    requirement: NONE (review finding)
    verification:
      - kind: manual
        ref: "grep '\\[Step ' src/pipeline/pipeline_runner.py: full pipeline all /5 (16 lines), OCR-only all /4 (13 lines). Zero /4 in full pipeline. Zero /3 in OCR-only. B2 gone"
        status: pass
    human_judgment: false
  - id: D6
    description: "All LLM-derived strings are HTML-escaped in the dashboard (fixes finding W6)"
    requirement: TRANSCRIPT-09
    verification:
      - kind: manual
        ref: "Malicious-pick smoke test: stockPick=RELIANCE<script>alert(1)</script>, transcript_context.after=Stop loss </script><img src=x onerror=alert(1)>. Grep confirms zero unescaped '<script>alert(1)</script>' in output, all rendered as '&lt;script&gt;alert(1)&lt;/script&gt;' entities. Defense-in-depth </-to-<\\/ replace also in place"
        status: pass
    human_judgment: false
  - id: D7
    description: "OCR-only mode logs an explicit transcript-skip reason"
    requirement: TRANSCRIPT-07
    verification:
      - kind: manual
        ref: "run_ocr_only_pipeline logs 'Transcription skipped: --ocr-only mode has no video to extract audio from' when transcript_config.enabled is truthy (grep 'Transcription skipped:' src/pipeline/pipeline_runner.py)"
        status: pass
    human_judgment: false
  - id: D8
    description: "phase1_extractions.json includes transcript_context per pick when present"
    requirement: TRANSCRIPT-04
    verification:
      - kind: manual
        ref: "_make_result adds transcript_context to result dict when non-empty. _attach_transcript_context also decorates each analysis item. Both persist because self._write(phase1_json, {results: p1_results}) dumps the whole list of dicts"
        status: pass
    human_judgment: false
  - id: D9
    description: "Wall-time overhead <= 30%"
    requirement: TRANSCRIPT-01
    verification:
      - kind: benchmark
        ref: "Not measured this plan - requires end-to-end run with real video + whisper install. Deferred to Plan 02-04 or manual verification"
        status: deferred
    human_judgment: true
---

# Plan 02-03 Summary -- Pipeline Integration + Dashboard XSS Fix

## Accomplishments

- **`src/transcript/pipeline_glue.py`** -- background-thread
  transcription wrapper. `kickoff_transcription(video_path, cfg,
  metadata_dir)` returns a Future that resolves to `list[Segment]`
  or `None`. Never raises: every path through the degradation matrix
  (no audio, missing ffmpeg, missing whisper binary, missing whisper
  model, whisper subprocess failure, unexpected exception) is caught,
  logged as WARNING, and swallowed. Single-worker
  `ThreadPoolExecutor` because each pipeline invocation transcribes
  exactly one video.

- **`src/pipeline/pipeline_runner.py`** -- transcription wired
  parallel with OCR. `kickoff_transcription` fires right after
  frame extraction (Step 1); Future awaited after Ollama analysis
  (Step 5). When segments land, `enrich_ocr_results` decorates
  `matched_results` with `transcript_context` before
  `_generate_metadata` writes `ocr_results.json`. Summary dict
  gains `transcript_segments_count`.

- **Step counter fixed (B2)** -- `_FULL_PIPELINE_STEPS = 5` and
  `_OCR_ONLY_PIPELINE_STEPS = 4` as single-source-of-truth
  constants. Previously the full pipeline mixed `[Step 1/4]` for
  steps 1-3 with `[Step 4/5]` for steps 4-5. Now every label
  reads `/5` (full) or `/4` (OCR-only) consistently.

- **`_generate_metadata`** -- carries `transcript_context` on
  matched entries when present. Optional field: pre-Phase-2 runs
  and disabled transcription produce entries without the key.

- **`post_ocr_pipeline.py`** -- five new helpers wire transcription
  through the LLM pipeline:
  - `_build_transcript_addendum(ctx)` -- natural-language prompt
    suffix ("Spoken context (+/-8s around this frame):\\n
    Before this moment: '...'\\n..."). Speaker line omitted when
    null.
  - `_load_transcript_contexts(path)` -- `{frame_name: ctx}` lookup
    from `ocr_results.json`. Returns `{}` on missing/malformed
    file.
  - `_attach_transcript_context(analysis, ctx)` -- in-place
    decorator so each analysis item carries the ctx it was
    extracted with.
  - `_escape_pick_strings(v)` -- recursive `html.escape` on every
    string in every pick.
  - `_enrich_with_frame_paths` -- extended to also carry
    `transcript_context` (first-frame-wins by stockPick,
    matching the frame_path pattern).

- **Dashboard XSS fix (W6)** -- server-side escape:
  every pick passes through `_escape_pick_strings` before
  `json.dumps`, then the resulting JSON gets `</` -> `<\\/` as
  defense-in-depth against `<script>` breakout. Malicious-pick
  smoke test confirms `<script>alert(1)</script>`,
  `</script><img src=x onerror=alert(1)>`, and unquoted analyst
  names all render as inert entities.

- **Dashboard Spoken Context section** -- collapsible native
  `<details>` element per card. Body shows Before / At / After in
  quotes with muted labels and a Speaker footer separated by a
  dashed border. Missing/empty ctx renders zero HTML
  (backwards-compatible).

- **DEDUP prompt** -- new rule #4: "PRESERVE any transcript_context
  field verbatim - do not summarize, rewrite, or drop it." Backed
  up by Python-side recovery in `_enrich_with_frame_paths` in case
  the model ignores it.

## Test Results

```
$ pytest tests/
67 passed in 0.44s
```

Zero regressions from the pipeline wiring, prompt-augmentation
helpers, or dashboard changes. All new logic verified via smoke
tests in the Python REPL; dedicated pytest coverage lands in
Plan 02-04.

## Smoke Tests (REPL)

```python
# pipeline_glue disabled config
>>> kickoff_transcription('/vid.mp4', {'enabled': False}, Path('/tmp'))
None  # no future, no thread

# pipeline_glue enabled + missing video -> Future -> None + WARNING
>>> f = kickoff_transcription('/nowhere.mp4', {'enabled': True}, Path('/tmp/x'))
>>> f.result(timeout=30)
None
# Log: 'Transcription skipped (audio extraction failed): Video not found: /nowhere.mp4'

# Transcript addendum
>>> _build_transcript_addendum({'before': 'Welcome back.', 'at': 'Top pick...', 'speaker': None})
'\\n\\nSpoken context (+-8s around this frame):\\nBefore this moment: "Welcome back."\\nAt this moment: "Top pick..."'

# XSS defense
>>> _escape_pick_strings({'a': '<script>x</script>', 'nested': {'y': 'a & b'}})
{'a': '&lt;script&gt;x&lt;/script&gt;', 'nested': {'y': 'a &amp; b'}}
```

## Deviations From Plan

- Plan sketched `_html_escape` as a local helper; we used
  `html.escape` from stdlib directly (satisfies the acceptance
  criterion "post_ocr_pipeline.py contains 'html.escape' or a
  local escape helper" and avoids reinventing the wheel).
- Plan sketched vertical `.controls` layout for the Spoken
  Context section; we used the existing card body dark-theme
  colors (`#0f151d` background, `var(--border)` border) for
  visual consistency with the rest of the card.
- No emoji anywhere in template strings per project style.

## What This Unblocks

- **Plan 02-04** (final wave) can add:
  - Pytest coverage for `pipeline_glue` (disabled -> None,
    graceful failures -> None Future.result)
  - Pytest coverage for `_escape_pick_strings` + `_build_html`
    XSS regression tests
  - Pytest coverage for `_load_transcript_contexts` +
    `_build_transcript_addendum`
  - Sample `config/config.transcript.example.json`
  - README section for `transcript_config` schema + whisper
    install steps
  - End-to-end wall-time benchmark (target: <= 1.3x OCR-only
    wall time)

## Commits

```
5b3d332 feat(02-03/T5): viewer.html - Spoken Context section + XSS fix (W6)
5172448 feat(02-03/T4): feed transcript_context into Ollama prompts + persist through phases
2057f7a feat(02-03/T2+T3): wire transcription into run_pipeline + fix step counter B2
a5aaeac feat(02-03/T1): pipeline_glue.py - background-thread transcription (never raises)
```
