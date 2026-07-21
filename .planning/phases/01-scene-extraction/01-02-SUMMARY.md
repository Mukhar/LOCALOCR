---
phase: 01-scene-extraction
plan: 02
subsystem: extraction
tags: [ffmpeg, scene-detection, hybrid, pts-parsing, debounce, blocker-fence]

requires:
  - phase: 01-01
    provides: strategy-pattern dispatch skeleton (_EXTRACTORS dict, _finalize_frames, cfg passthrough)
provides:
  - _extract_by_scene strategy (real-PTS-driven filenames + tmp-file unlink for debounced losers)
  - _extract_by_hybrid strategy (two ffmpeg passes into scoped subdirs, merged via _debounce_pairs)
  - _parse_showinfo_pts (ffmpeg showinfo stderr → sorted list[float])
  - _debounce_timestamps + _debounce_pairs (pure functions, no fs side effects)
  - _validate_extraction_config extended with scene_config bounds (threshold ∈ [0,1], gaps ≥ 0 / > 0)
  - _run_ffmpeg's capture_stderr kwarg
affects: [01-03-PLAN, phase-2-transcript, phase-3-web-ui]

tech-stack:
  added: []
  patterns:
    - "Two-pass ffmpeg merge (replaces broken single-pass modulo filter)"
    - "Caller-cleans-up tmp files (pure debounce + strategy-owned unlink)"
    - "Layered regression fences: semantic + runtime negative + source-text scan"

key-files:
  created:
    - tests/fixtures/showinfo_stderr.txt (canned ffmpeg showinfo stderr sample)
  modified:
    - src/extractor/frame_extractor.py (+~360 lines: 2 strategies, 3 helpers, extended validation)
    - tests/test_frame_extractor.py (+13 tests; total 19)

key-decisions:
  - "Split debounce into _debounce_timestamps (list[float]) and _debounce_pairs (list[(Path, float)]) — extractors need the pair variant to correlate PTS with tmp files for cleanup"
  - "Both debounce helpers are PURE — the tmp-file unlink lives in the extractor strategies, not the helpers. Keeps helpers unit-testable without a temp dir and lets each strategy own its cleanup policy"
  - "Hybrid mode runs TWO ffmpeg passes into scoped subdirs (tmp_dir/_scene, tmp_dir/_gap) instead of the previous single-pass eq(mod(t,N),0) filter — the modulo filter almost never fired because floats rarely hit exact modulo boundaries (BLOCKER 2)"
  - "Symmetric drift-guard fallback in both scene and hybrid: if file count != PTS count BEFORE debounce, log WARNING and either fall back to synthetic timestamps (scene) or trim to shorter list (hybrid). D5 stays 'real PTS unless proven impossible'"
  - "Source-level regression fence — test_frame_extractor_source_has_no_eq_mod_filter greps the module for the forbidden substring so a future copy-paste can't silently reintroduce the broken filter"
  - "Docstrings must NOT literally spell out 'eq(mod(t,' since the source-text fence would trip on them. Reworded to 'modulo-based select filter' with reference to the fence test"
  - "capture_stderr kwarg on _run_ffmpeg is keyword-only with default False so existing interval-mode caller is byte-identical unaffected"

patterns-established:
  - "Extractor strategies own their tmp cleanup — helpers stay pure"
  - "Blocker fixes get three-layer fences: semantic proof (behavior), runtime negative (no bad cmd observed), source-text negative (no bad string in file). Any one can catch regression"
  - "Fixture-backed regex tests — canned stderr sample lives in tests/fixtures/ so parsing can be exercised without spawning ffmpeg"

requirements-completed: [EXTRACT-01, EXTRACT-02, EXTRACT-04, EXTRACT-05]

coverage:
  - id: D1
    description: "extraction_mode='scene' extracts frames using ffmpeg scene-change detection with configurable threshold; PTS from showinfo drives real timestamps in filenames"
    requirement: EXTRACT-01
    verification:
      - kind: unit
        ref: "tests/test_frame_extractor.py::test_scene_mode_ffmpeg_command_shape"
        status: pass
      - kind: unit
        ref: "tests/test_frame_extractor.py::test_scene_mode_deletes_debounced_tmp_files"
        status: pass
      - kind: manual_procedural
        ref: "Smoke test on 8s multi-color concat video: scene mode picked 2 real-PTS frames (04s, 06s), interval baseline picked 4"
        status: pass
    human_judgment: false
  - id: D2
    description: "extraction_mode='hybrid' runs TWO ffmpeg passes (scene + fps=1/max_gap) and merges via _debounce_pairs — NOT the broken single-pass modulo filter"
    requirement: EXTRACT-02
    verification:
      - kind: unit
        ref: "tests/test_frame_extractor.py::test_hybrid_mode_runs_two_ffmpeg_passes"
        status: pass
      - kind: unit
        ref: "tests/test_frame_extractor.py::test_hybrid_mode_does_not_use_eq_mod_filter"
        status: pass
      - kind: unit
        ref: "tests/test_frame_extractor.py::test_frame_extractor_source_has_no_eq_mod_filter"
        status: pass
      - kind: manual_procedural
        ref: "Smoke test on 8s video: hybrid emitted 4 frames — scene contributions at 04s/06s (pass A) + gap contributions at 00s/03s (pass B) after debounce"
        status: pass
    human_judgment: false
  - id: D3
    description: "_parse_showinfo_pts extracts PTS timestamps from ffmpeg showinfo stderr, sorted, unrelated lines ignored"
    requirement: EXTRACT-04
    verification:
      - kind: unit
        ref: "tests/test_frame_extractor.py::test_parse_showinfo_pts_extracts_all_timestamps"
        status: pass
      - kind: unit
        ref: "tests/test_frame_extractor.py::test_parse_showinfo_pts_empty_stderr"
        status: pass
    human_judgment: false
  - id: D4
    description: "_debounce_pairs correctly filters (file, pts) pairs by min_gap and is a pure function (no file I/O)"
    requirement: EXTRACT-05
    verification:
      - kind: unit
        ref: "tests/test_frame_extractor.py::test_debounce_pairs_returns_survivor_pairs"
        status: pass
      - kind: unit
        ref: "tests/test_frame_extractor.py::test_debounce_pairs_is_pure_no_filesystem_side_effects"
        status: pass
      - kind: unit
        ref: "tests/test_frame_extractor.py::test_debounce_drops_close_frames"
        status: pass
      - kind: unit
        ref: "tests/test_frame_extractor.py::test_debounce_min_gap_zero_is_noop"
        status: pass
    human_judgment: false
  - id: D5
    description: "Invalid scene_config values (threshold out of [0,1], negative gaps, non-dict scene_config) fail fast before any ffmpeg call"
    requirement: EXTRACT-05
    verification:
      - kind: unit
        ref: "tests/test_frame_extractor.py::test_invalid_scene_threshold_raises"
        status: pass
      - kind: unit
        ref: "tests/test_frame_extractor.py::test_invalid_scene_negative_gap_raises"
        status: pass
    human_judgment: false
---

# Plan 01-02 Summary — Scene + Hybrid Extractors

## Accomplishments

- Added `_extract_by_scene` and `_extract_by_hybrid` strategy functions and
  registered both in `_EXTRACTORS` (dispatch dict now has 3 entries;
  `not yet implemented` placeholders from 01-01 are gone).
- Added `_parse_showinfo_pts` for parsing ffmpeg's `showinfo` filter output,
  plus `_debounce_timestamps` and `_debounce_pairs` as pure helpers.
- Extended `_validate_extraction_config` with `scene_config` bounds
  (threshold ∈ [0, 1], `min_gap_seconds` ≥ 0, `max_gap_seconds` > 0 for
  hybrid). Bad configs never spawn a subprocess (D6 fail-fast).
- Widened `_run_ffmpeg` with a keyword-only `capture_stderr: bool = False`.
  Default preserves the pre-01-02 interval-mode contract exactly.
- Fixed **BLOCKER 2** (broken `eq(mod(t, N), 0)` hybrid filter): hybrid now
  runs two ffmpeg passes into scoped tmp subdirs (`_scene`, `_gap`) and
  merges their `(file, pts)` lists via `_debounce_pairs`. Layered fences
  guard against reintroduction — semantic (two calls with correct filters),
  runtime negative (no ffmpeg cmd contains the forbidden substring), and
  source-text scan (`frame_extractor.py` cannot even mention the string).
- Fixed **BLOCKER 3** (silent D5 violation via mismatched files vs PTS):
  scene extractor now debounces `(file, pts)` pairs together and unlinks
  losers from tmp before `_finalize_frames` runs, so the surviving files
  match `kept_ts` exactly.
- Added `tests/fixtures/showinfo_stderr.txt` — canned ffmpeg stderr sample
  (4 valid `pts_time` lines + 2 noise lines).
- Grew `tests/test_frame_extractor.py` from 6 → 19 tests, all green.

## Test Results

```
$ pytest tests/test_frame_extractor.py -v
19 passed in 0.22s
```

Includes all four blocker-fence tests:
- `test_scene_mode_deletes_debounced_tmp_files` — BLOCKER 3 semantic proof
- `test_hybrid_mode_runs_two_ffmpeg_passes` — BLOCKER 2 semantic proof
- `test_hybrid_mode_does_not_use_eq_mod_filter` — BLOCKER 2 runtime fence
- `test_frame_extractor_source_has_no_eq_mod_filter` — BLOCKER 2 source-text fence

## Smoke Test (manual, real ffmpeg)

Built an 8 s concat video (2 s each: red / green / blue / yellow):

| Mode | Frames | Filenames |
|------|--------|-----------|
| interval @2s | 4 | 00s, 02s, 04s, 06s (synthetic) |
| scene @0.3   | 2 | **04s, 06s (real PTS)** |
| hybrid       | 4 | 00s, 03s, 04s, 06s (pass A scene 04s+06s, pass B gap 00s+03s) |

Real-PTS timestamps in scene mode filenames (04s + 06s — not the synthetic
`(N-1)*interval` sequence 00s + 02s) confirm D5. The hybrid mix confirms
BOTH passes actually fired and got merged, not just one.

## Verification Checklist (from PLAN.md)

- [x] `pytest tests/test_frame_extractor.py -v` — 19/19 pass
- [x] Manual scene run on real video — pipeline completes, frames extracted
- [x] Manual hybrid run — pipeline completes, both passes contribute
- [~] Frame count in scene mode ≥ 3× smaller than interval — on the 8 s
      synthetic smoke video only 2× (2 vs 4). Plan-check explicitly delegates
      the tighter ratio to plan 01-03's real-broadcast benchmark ("looser than
      the 5× target — the benchmark in plan 01-03 proves the tighter number").
      Functional completeness confirmed here.

## Deviations From Plan

None material. Two small pragmatic notes:

- Docstrings in `_extract_by_hybrid` had to be reworded to describe the
  forbidden filter without literally writing out its characters — otherwise
  `test_frame_extractor_source_has_no_eq_mod_filter` would trip on our own
  educational comment. Solution: refer to it as "modulo-based select filter"
  and point at the fence test by name.
- The `scene_config` type-guard was added before the individual key checks
  — plan called for range checks but a non-dict `scene_config` would crash
  the key checks with a cryptic `TypeError`. Added an explicit
  `isinstance(scene_cfg, dict)` guard with a clean error.

## What This Unblocks

Plan 01-03 (benchmark harness + docs). All three extraction modes now
exist, so the benchmark can compare them on real broadcast content and
generate the JSON report that ships as the plan's artifact.

## Commits

```
7c21831 test(01-02/T6): 13 new tests for scene/hybrid + BLOCKER-2/3 fences
2fc6e54 feat(01-02/T5): extend _validate_extraction_config for scene_config
c95f47c feat(01-02/T4): add _extract_by_scene + _extract_by_hybrid extractors
628d7a7 feat(01-02/T3): add _debounce_timestamps + _debounce_pairs helpers
8f307d2 feat(01-02/T2): _parse_showinfo_pts helper + showinfo stderr fixture
0d80f7a refactor(01-02/T1): _run_ffmpeg gains capture_stderr kwarg
```
