---
phase: 01-scene-extraction
plan: 01
subsystem: extraction
tags: [ffmpeg, strategy-pattern, refactor, backward-compat, pytest]

requires:
  - phase: none
    provides: baseline extract_frames() implementation
provides:
  - Strategy-pattern dispatch (_EXTRACTORS) inside src/extractor/frame_extractor.py
  - Pure _finalize_frames helper (rename + naming contract)
  - _extract_by_interval strategy function (interval mode)
  - _validate_extraction_config helper (fail-fast per D6)
  - cfg=dict|None kwarg on extract_frames() with backward-compat defaults
  - tests/ scaffolding + baseline manifest fixture
affects: [01-02-PLAN, 01-03-PLAN, phase-2-transcript]

tech-stack:
  added: [pytest 8.4.2 (already in .venv)]
  patterns:
    - "Module-level dispatch dict (D4) — mirrors text_matcher._is_match"
    - "PEP-563 lazy annotations (from __future__ import annotations) for Python 3.9 venv compat"
    - "sha256 manifest as byte-level regression fence"

key-files:
  created:
    - tests/__init__.py
    - tests/conftest.py
    - tests/test_frame_extractor.py
    - tests/fixtures/synthetic_baseline.mp4 (133 KB testsrc2 lavfi clip)
    - tests/fixtures/interval_baseline_manifest.json
  modified:
    - src/extractor/frame_extractor.py (refactor: dispatch + helpers + widened signature)
    - src/pipeline/pipeline_runner.py (single-line passthrough per D8 revised)

key-decisions:
  - "Reconcile positional `interval_seconds` into `cfg['frame_interval_seconds']` — positional wins so direct callers that omit cfg still get their explicit interval"
  - "cfg = dict(cfg or {}) copy at top of extract_frames() prevents mutating the caller's dict"
  - "Use `from __future__ import annotations` in frame_extractor.py so plan's literal `dict | None = None` syntax parses under the Python 3.9 venv without dropping the 3.10 syntax the plan specifies"
  - "Byte-level (sha256) regression fence over file-listing — catches encoding drift, not just naming drift"
  - "Force-add tests/fixtures/synthetic_baseline.mp4 past the *.mp4 gitignore because it is a small deterministic fixture (133 KB), not user data"

patterns-established:
  - "Strategy dispatch: _EXTRACTORS dict + strategy functions with a uniform (video, out_path, tmp_dir, ffmpeg_bin, duration, cfg) -> list[dict] signature. 01-02 adds scene/hybrid the same way."
  - "Per-task commit granularity — 6 commits, each maps 1:1 to a plan task, plan-checker-friendly bisection"
  - "Fail-fast config validation via dedicated _validate_extraction_config helper called before any expensive I/O"

requirements-completed: [EXTRACT-03, EXTRACT-05]

coverage:
  - id: D1
    description: "extract_frames() dispatches through _EXTRACTORS keyed on extraction_mode; default 'interval' produces byte-identical output to the pre-refactor build"
    requirement: EXTRACT-03
    verification:
      - kind: unit
        ref: "tests/test_frame_extractor.py::test_interval_mode_byte_identical_to_baseline"
        status: pass
      - kind: unit
        ref: "tests/test_frame_extractor.py::test_extraction_mode_defaults_to_interval"
        status: pass
    human_judgment: false
  - id: D2
    description: "_finalize_frames preserves the frame_NNNN_XXmYYs.png naming contract using regex-based numbering (not iteration index)"
    requirement: EXTRACT-05
    verification:
      - kind: unit
        ref: "tests/test_frame_extractor.py::test_finalize_frames_names_files_correctly"
        status: pass
      - kind: unit
        ref: "tests/test_frame_extractor.py::test_finalize_frames_preserves_frame_number_via_regex"
        status: pass
      - kind: unit
        ref: "tests/test_frame_extractor.py::test_finalize_frames_skips_out_of_band_files"
        status: pass
    human_judgment: false
  - id: D3
    description: "cfg=None|{} keyword on extract_frames() preserves byte-identical behavior; positional interval_seconds is reconciled into cfg"
    requirement: EXTRACT-03
    verification:
      - kind: unit
        ref: "tests/test_frame_extractor.py::test_interval_mode_byte_identical_to_baseline"
        status: pass
    human_judgment: false
  - id: D4
    description: "Invalid extraction_mode fails fast via _validate_extraction_config with a message listing valid modes"
    requirement: EXTRACT-03
    verification:
      - kind: unit
        ref: "tests/test_frame_extractor.py::test_invalid_extraction_mode_raises"
        status: pass
    human_judgment: false
  - id: D5
    description: "pipeline_runner.run_pipeline passes cfg=config into extract_frames — exactly one hunk changed per D8 revised"
    requirement: EXTRACT-05
    verification:
      - kind: manual_procedural
        ref: "git diff origin/main -- src/pipeline/pipeline_runner.py | grep '^[+-]' | grep -v '^[+-]{3}' | wc -l == 2"
        status: pass
    human_judgment: false
---

# Plan 01-01 Summary — Strategy-Pattern Refactor

## Accomplishments

- Refactored `src/extractor/frame_extractor.py` from an inline monolith into a
  strategy-dispatch layout without changing observable behavior. Four new
  module-level entities: `_finalize_frames`, `_extract_by_interval`,
  `_EXTRACTORS`, `_validate_extraction_config`.
- Widened `extract_frames()` signature with `cfg: dict | None = None`. When
  `None`/empty, behavior is byte-identical to today (proven at sha256 level).
- Single one-line change in `src/pipeline/pipeline_runner.py` — the only
  allowed downstream edit per revised D8. Diff vs `origin/main` = exactly
  one hunk.
- Established `tests/` scaffolding (`__init__.py`, `conftest.py`) with
  fixture stack (`synthetic_baseline.mp4` + `interval_baseline_manifest.json`)
  and 6 tests covering the byte-level regression fence, the pure `_finalize_frames`
  helper (including regex-numbering guard and stray-file warn-and-skip), the
  full dispatch path (fully mocked subprocess stack), and D6 fail-fast
  validation.

## Test Results

```
$ pytest tests/test_frame_extractor.py -v
tests/test_frame_extractor.py::test_interval_mode_byte_identical_to_baseline PASSED
tests/test_frame_extractor.py::test_finalize_frames_names_files_correctly    PASSED
tests/test_frame_extractor.py::test_finalize_frames_preserves_frame_number_via_regex PASSED
tests/test_frame_extractor.py::test_finalize_frames_skips_out_of_band_files  PASSED
tests/test_frame_extractor.py::test_extraction_mode_defaults_to_interval     PASSED
tests/test_frame_extractor.py::test_invalid_extraction_mode_raises           PASSED
6 passed in 0.15s
```

## Verification Checklist (from PLAN.md)

- [x] `pytest tests/test_frame_extractor.py -v` — all 6 pass
- [x] Fixed-interval extraction against a real video (synthetic 6 s testsrc2)
      produces the same frame count and filenames as the pre-refactor build
      (byte-identical sha256s in fixture manifest)
- [x] `git diff origin/main -- src/pipeline/pipeline_runner.py` shows exactly
      one changed hunk (the `cfg=config` addition)
- [x] `grep -n '_EXTRACTORS\|_finalize_frames\|_extract_by_interval\|_validate_extraction_config'`
      confirms all four helpers exist at module scope

## Deviations From Plan

None material. Two small pragmatic choices that stay inside the plan's
"Claude's Discretion" area:

- Added `from __future__ import annotations` at the top of
  `frame_extractor.py` so the plan's literal `cfg: dict | None = None` syntax
  parses under the project's Python 3.9 `.venv` without swapping to
  `Optional[dict]`. This matches `pipeline_runner.py`'s existing pattern.
- Reconciled positional `interval_seconds` into `cfg['frame_interval_seconds']`
  inside `extract_frames()` (with positional winning) so direct callers who
  pass only positional arguments still get their explicit interval instead
  of the cfg fallback of 2. Plan-suggested helper signature was preserved.

## What This Unblocks

Plan 01-02 (`scene` + `hybrid` extractors) becomes a pure addition — register
two new functions in `_EXTRACTORS`, teach `_validate_extraction_config` to
range-check `scene_config` params. Zero touches to the dispatcher.

## Commits

```
bcd4788 test(01-01/T6): add _finalize_frames + dispatch + fail-fast unit tests
606fcde test(01-01/T5): add byte-level D2 backward-compat manifest test
0af2e3e refactor(01-01/T4): add _validate_extraction_config helper
7faaa40 refactor(01-01/T3): extract _extract_by_interval + _EXTRACTORS dispatch
a6f94b1 refactor(01-01/T2): widen extract_frames signature with cfg + one-line passthrough
ea4a9ba refactor(01-01/T1): extract _finalize_frames helper for name/rename
```
