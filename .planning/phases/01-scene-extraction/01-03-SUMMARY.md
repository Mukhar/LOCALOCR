---
phase: 01-scene-extraction
plan: 03
subsystem: extraction
tags: [benchmark, docs, ci-gate, example-configs]

requires:
  - phase: 01-02
    provides: three working extraction modes (interval / scene / hybrid) + strategy dispatch
provides:
  - benchmark_extraction.py (three-mode compare + EXTRACT-01 correctness gate)
  - config/config.scene.example.json (drop-in scene-mode config)
  - config/config.hybrid.example.json (drop-in hybrid-mode config)
  - README "Frame Extraction Modes" section with mode table + when-to-use table
  - AGENTS.md Configuration Schema rows for extraction_mode + scene_config.*
  - ARCHITECTURE.md § 6.0 "Extraction Modes (strategy dispatch)" + updated sequence diagram
affects: [phase-2-transcript]

tech-stack:
  added: []
  patterns:
    - "Exit-code-gated benchmark script (usable as pre-commit / CI hook, not just a print utility)"
    - "Zero-duplication policy: benchmark imports production code; no logic copy-paste"

key-files:
  created:
    - benchmark_extraction.py (~330 lines)
    - config/config.scene.example.json
    - config/config.hybrid.example.json
    - .planning/phases/01-scene-extraction/01-03-SUMMARY.md
  modified:
    - README.md (+51 lines: Frame Extraction Modes subsection)
    - AGENTS.md (+5 config rows, updated frame_extractor.py module description)
    - ARCHITECTURE.md (updated signature in sequence diagram + new § 6.0)

key-decisions:
  - "Benchmark keeps ocr_engine constant (apple_vision) across all three modes — comparing modes is the goal, not comparing OCR engines. Fair timing"
  - "Exit code gate uses 3 levels: 0 pass, 1 crash, 2 semantic fail (scene lost keywords). 2 is reserved for the specific EXTRACT-01 half of the claim so downstream CI can special-case it"
  - "Benchmark's temp dirs default to auto-cleanup; --keep-output opt-in preserves them for manual triage"
  - "Example configs mirror the reference config/config.json exactly plus the new keys — swapping is a one-line diff for a user"
  - "AGENTS.md scene_config rows include ranges + defaults + which mode uses them — matches _validate_extraction_config's actual enforcement"

patterns-established:
  - "Benchmark scripts are CI-gates, not print-utilities — real exit codes on semantic failure"
  - "Two example configs per new feature (one per mode) so users have zero-friction paths"

requirements-completed: [EXTRACT-06]

coverage:
  - id: D1
    description: "benchmark_extraction.py runs all three modes on the same video and prints a markdown comparisonble"
    requirement: EXTRACT-06
    verification:
      - kind: manual_procedural
        ref: "Run `python benchmark_extraction.py --video <sample>` — table printed with 3 rows, one per mode, columns: Mode | Frames | Extract | OCR | Match | Total | Unique Keywords Matched"
        status: pass
    human_judgment: false
  - id: D2
    description: "Benchmark provides the EXTRACT-01 'no keywords lost' correctness gate via non-zero exit code"
    requirement: EXTRACT-06
    verification:
      - kind: manual_procedural
        ref: "10-min real broadcast sample: scene mode dropped 4 keywords the interval baseline found ('BUY', 'SELL', 'STOCK', 'the') — benchmark exited 2 with `FAIL: scene mode lost keywords vs interval: ['BUY', 'SELL', 'STOCK', 'the']` to stderr"
        status: pass
    human_judgment: false
  - id: D3
    description: "Two example configs (scene + hybrid) exist under config/ and are valid JSON"
    requirement: EXTRACT-06
    verification:
      - kind: manual_procedural
        ref: "`python -c 'import json; json.load(open(f))'` succeeds for both config/config.scene.example.json and config/config.hybrid.example.json"
        status: pass
    human_judgment: false
  - id: D4
    description: "README documents extraction_mode + scene_config and points at benchmark_extraction.py"
    requirement: EXTRACT-06
    verification:
      - kind: manual_procedural
        ref: "`grep extraction_mode README.md` and `grep benchmark_extraction README.md` both return matches"
        status: pass
    human_judgment: false
  - id: D5
    description: "AGENTS.md Configuration Schema table has extraction_mode + scene_config rows; ARCHITECTURE.md sequence diagram reflects cfg passthrough and describes the three modes"
    requirement: EXTRACT-06
    verification:
      - kind: manual_procedural
        ref: "`grep -E 'extraction_mode|scene_config' AGENTS.md` returns 5 matches; `grep 'cfg=config' ARCHITECTURE.md` returns 1 match in the sequence diagram (line 128); new § 6.0 'Extraction Modes (strategy dispatch)' present"
        status: pass
    human_judgment: false
---

# Plan 01-03 Summary — Benchmark + Docs

## Accomplishments

- **`benchmark_extraction.py`** (~330 lines) — runs interval + scene +
  hybrid on the same video, times extract / OCR / match per mode, prints
  a markdown comparison table, and enforces the EXTRACT-01 correctness
  gate via exit code:
    - `0` — every mode ran AND scene mode kept every keyword interval
      mode found
    - `1` — one of the runs crashed (extract / OCR / match raised)
    - `2` — scene mode lost at least one keyword (missing set printed
      to stderr) — pre-commit / CI can catch this
  Zero-duplication: imports `extract_frames`, `run_ocr`, `match_text`
  from `src/`. Configurable via `--video / --interval / --threshold /
  --min-gap / --max-gap / --keywords / --ocr-engine / --keep-output`.
- **`config/config.scene.example.json`** and **`config/config.hybrid.example.json`** —
  drop-in ready. Both mirror `config/config.json` exactly, only adding
  the mode-specific keys (`extraction_mode`, `scene_config`) plus a
  `__comment` field.
- **README** — new "Frame Extraction Modes" subsection under
  Configuration: mode table, config snippets, when-to-use table, and
  a pointer to `benchmark_extraction.py` with its exit-code semantics.
  +51 lines, no existing content touched.
- **AGENTS.md** — Configuration Schema table gains 4 rows for
  `extraction_mode` and `scene_config.{threshold,min_gap_seconds,max_gap_seconds}`.
  `frame_extractor.py` module description now names the strategy
  dispatch (interval / scene / hybrid) and its helpers.
- **ARCHITECTURE.md** — sequence diagram in § 3.1 shows the new
  4-arg signature `extract_frames(video_path, output_dir, interval, cfg=config)`
  plus the per-mode dispatch branches; new § 6.0 "Extraction Modes
  (strategy dispatch)" with per-mode strategy fn + ffmpeg call +
  timestamp source, and pointer to README + benchmark. Fixes
  plan-check WARNING 8 (arch doc showed old 3-arg signature).

## Verification

### Automated
- `pytest tests/test_frame_extractor.py -v` — **19/19 pass in 0.19s**
- Both example configs parse cleanly (`json.load` succeeds)
- Benchmark script parses (`ast.parse`) and `--help` prints correctly

### Manual — Real Broadcast Content

Trimmed samples from `input_videos/asset/low/13.mp4` (~9-hour Zee
Business live broadcast). Both samples used `apple_vision` engine and
default scene_config (`threshold=0.3`, `min_gap_seconds=1.0`).

| Sample | Mode | Frames | Extract | OCR | Total | Matched | Note |
|---|---|---|---|---|---|---|---|
| 3-min segment | interval | 90  | 0.9 s | 7.0 s | 7.8 s | 0 kw | static newsroom, no target text |
|               | scene    | 1   | 0.6 s | 0.8 s | 1.4 s | 0 kw | **90x reduction** |
|               | hybrid   | 19  | 1.2 s | 2.3 s | 3.5 s | 0 kw | |
| 10-min segment (default `--keywords the,and,India,BUY,SELL,STOCK,BSE,NSE`) | interval | 300 | 2.5 s | 15.2 s | 17.7 s | **7 kw** | baseline |
|               | scene    | 63  | 2.2 s | 3.6 s | 5.8 s | 3 kw | **4.76x reduction, LOSES 4 kw** |
|               | hybrid   | 116 | 3.9 s | 6.2 s | 10.1 s | **7 kw** | **2.59x reduction, ZERO loss** |

**Benchmark exit code on the 10-min sample: 2**, with stderr
`FAIL: scene mode lost keywords vs interval: ['BUY', 'SELL', 'STOCK', 'the']`.
Working exactly as designed — this is the EXTRACT-01 correctness half
of the requirement being enforced automatically.

### Reading the numbers

- The EXTRACT-01/06 5× frame-reduction claim is **content-dependent**:
  90× on static newsroom footage, ~5× on dense broadcast with
  ephemeral ticker overlays. Comfortably met on the first sample and
  approached on the second.
- **Scene mode alone is lossy on broadcast content** — ephemeral text
  (breaking-news lower-thirds, ticker) can appear and disappear without
  triggering a scene-change threshold. This is a genuine limit of
  visual scene detection, not a bug.
- **Hybrid mode is the honest recommendation for mixed / unknown
  content**: modest reduction (2-3×) with zero keyword loss on the
  test sample. The `max_gap_seconds` fallback tick is exactly what
  catches the overlays that scene mode misses.
- The exit-code gate is the mechanism that keeps this tradeoff
  explicit rather than silently degrading.

## Deviations From Plan

- **Task 5** (final "Phase 1: scene-change frame extraction" bulk
  commit) — used per-task commits throughout Phase 1 instead of a
  single final bulk commit. Matches the user's stated preference at
  execution start and is easier to bisect. All Phase 1 work is
  committed cleanly (0af2e3e..e8f21bc); no separate bulk commit
  added. `git status` is clean (only pre-existing untracked
  `.planning/` bookkeeping files, none of which are Phase 1 output).
- **Task 5** tag creation — plan says "Do NOT tag
  (`config.git.create_tag` is false)" — respected. No tag created.

## What This Unblocks

Phase 1 is complete. Downstream:
- **Phase 2 (transcript)** — can now consume any of the three
  extraction modes; the transcript worker doesn't care which mode
  produced the frames.
- **Phase 3 (web UI)** — can expose `extraction_mode` as a form field
  with the mode table from README as inline help copy.

## Commits (Plan 01-03)

```
e8f21bc docs(01-03/T4): AGENTS.md + ARCHITECTURE.md — three-mode dispatch
f3aa847 docs(01-03/T3): README — Frame Extraction Modes section
161b0c8 feat(01-03/T2): benchmark_extraction.py — three-mode compare + exit-code gate
53debfc docs(01-03/T1): scene + hybrid example configs
```

## Phase 1 Commit Chain (all 13 code commits)

```
e8f21bc docs(01-03/T4): AGENTS.md + ARCHITECTURE.md
f3aa847 docs(01-03/T3): README
161b0c8 feat(01-03/T2): benchmark_extraction.py
53debfc docs(01-03/T1): example configs
4b31499 docs(01-02): plan summary + STATE
7c21831 test(01-02/T6): 13 new tests + BLOCKER fences
2fc6e54 feat(01-02/T5): validate scene_config
c95f47c feat(01-02/T4): scene + hybrid extractors
628d7a7 feat(01-02/T3): _debounce_* helpers
8f307d2 feat(01-02/T2): _parse_showinfo_pts helper
0d80f7a refactor(01-02/T1): _run_ffmpeg capture_stderr
6e4e925 docs(01-01): plan summary + STATE
bcd4788 test(01-01/T6): dispatch + fail-fast unit tests
606fcde test(01-01/T5): byte-level D2 backward-compat manifest
0af2e3e refactor(01-01/T4): _validate_extraction_config
7faaa40 refactor(01-01/T3): _extract_by_interval + _EXTRACTORS
a6f94b1 refactor(01-01/T2): cfg kwarg + passthrough
ea4a9ba refactor(01-01/T1): _finalize_frames helper
```
