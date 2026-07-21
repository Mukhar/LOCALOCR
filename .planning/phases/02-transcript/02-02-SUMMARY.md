---
phase: 02-transcript
plan: 02
subsystem: transcript
tags: [whisper.cpp, subprocess-wrapper, pure-function-correlator, defensive-immutability]

requires:
  - phase: 02-01
    provides: src.common.subprocess_utils shared helpers, src.transcript package skeleton
provides:
  - src.transcript.transcribe (whisper.cpp subprocess wrapper)
  - src.transcript.Segment (frozen dataclass, seconds-normalized)
  - src.transcript.WhisperNotAvailableError (graceful-skip signal)
  - src.transcript.WhisperFailureError (harder failure)
  - src.transcript.correlate (pure function: frame_ts + segments -> {before, at, after, speaker})
  - src.transcript.enrich_ocr_results (only touches matched frames; identity-preserving for unmatched)
  - src.transcript.frame_timestamp_seconds (XXmYYs parser)
  - tests/fixtures/whisper_output.json (realistic canned whisper -oj output)
affects: [02-03-PLAN (pipeline integration will import all of these)]

tech-stack:
  added:
    - "whisper.cpp `whisper-cli` binary (optional user-setup; caller must install for real transcription)"
    - "whisper.cpp ggml-*.bin model files (optional; base.en default)"
  patterns:
    - "Binary alias fallback: try configured -> whisper-cli -> main -> whisper. Handles brew build, source build, and future renames without config churn"
    - "Curated install hint on WhisperNotAvailableError: mentions both the brew command AND the transcript_config.binary escape hatch. Never dead-ends the user"
    - "Pure-function correlator: zero I/O + zero mutation -> tests need zero mocks and run in microseconds"
    - "Identity-preserving pass-through: enrich_ocr_results returns unmatched dicts by reference (not copy) so tests can assert out[i] is inp[i]"

key-files:
  created:
    - src/transcript/whisper_transcriber.py (~200 lines)
    - src/transcript/correlator.py (~130 lines)
    - tests/fixtures/whisper_output.json
    - tests/test_transcript/test_whisper_transcriber.py (13 tests)
    - tests/test_transcript/test_correlator.py (15 tests)
  modified:
    - src/transcript/__init__.py (re-exports transcribe/Segment/errors + correlator API)

key-decisions:
  - "Segment is frozen=True. Tests fence this (test_segment_is_frozen) so a future 'just add a mutation' commit trips CI immediately. Whisper output is transient state we want to treat as immutable through the whole pipeline"
  - "Times in seconds, not milliseconds. Whisper.cpp emits ms in the JSON offsets block; we normalize once at parse time. Downstream code compares to frame timestamps in seconds too - one unit, one obvious way"
  - "Empty-text segments dropped. Whisper emits these for pure-silence gaps. Filtering at parse time means the correlator never has to think about them"
  - "Malformed frame timestamp returns 0.0 rather than raising. Rationale: one weird frame shouldn't crash the enrichment pass across a 6000-frame video. The resulting frame gets 'wrong' context but the pipeline completes"
  - "correlator.enrich_ocr_results returns unmatched entries BY IDENTITY, matched entries as new dicts. Two properties fenced by tests: (1) input list never mutated, (2) callers can spot-check unmatched pass-through with `is`. Cheap defensive-programming win"
  - "Binary discovery order (configured -> whisper-cli -> main -> whisper): whisper.cpp renamed 'main' -> 'whisper-cli' at v1.6. Supporting the historical name means users on older brew formulae or hand-built binaries just work"
  - "Timeout of 1 h on whisper subprocess. base.en on M-series runs 5-10x realtime, so 1 h of audio finishes in minutes. 1 h ceiling is a paranoid catch for a wedged process, not a realistic runtime bound"

patterns-established:
  - "New Phase 2 modules that shell out MUST use src.common.subprocess_utils and raise their own module-specific exception type (translation at the boundary), following the whisper_transcriber.py + audio_extractor.py precedent"
  - "New pure-function modules (like correlator) explicitly avoid I/O so tests can be zero-mock. Tests for these should include an identity-preservation fence"
  - "Fixtures live in tests/fixtures/ and mirror the FULL upstream envelope, not just the field(s) the parser reads. Catches parser regressions when the envelope keys drift"

requirements-completed: []  # TRANSCRIPT-02/03 require pipeline integration in 02-03; 02-02 provides the machinery.

coverage:
  - id: D1
    description: "transcribe() invokes whisper-cli with the correct flag shape (-oj, -l, -t, -m, -f) and returns normalized Segment records"
    requirement: TRANSCRIPT-02
    verification:
      - kind: unit
        ref: "tests/test_transcript/test_whisper_transcriber.py::test_transcribe_command_shape"
        status: pass
      - kind: unit
        ref: "tests/test_transcript/test_whisper_transcriber.py::test_parse_whisper_json_normalizes_segments (ms->s conversion, text stripping, speaker=None)"
        status: pass
    human_judgment: false
  - id: D2
    description: "Missing whisper binary/model raises WhisperNotAvailableError with a helpful install hint (both brew AND the transcript_config.binary escape hatch surfaced)"
    requirement: TRANSCRIPT-06
    verification:
      - kind: unit
        ref: "tests/test_transcript/test_whisper_transcriber.py::test_locate_binary_missing_raises_helpful"
        status: pass
      - kind: unit
        ref: "tests/test_transcript/test_whisper_transcriber.py::test_locate_model_missing_raises_helpful"
        status: pass
      - kind: unit
        ref: "tests/test_transcript/test_whisper_transcriber.py::test_locate_binary_falls_back_through_aliases (proves whisper-cli/main/whisper aliases all get tried)"
        status: pass
    human_judgment: false
  - id: D3
    description: "correlate(frame_ts, segments, window) returns {before, at, after, speaker} with correct bucketing and speaker majority"
    requirement: TRANSCRIPT-03
    verification:
      - kind: unit
        ref: "tests/test_transcript/test_correlator.py (10 correlate tests covering empty/all-before/all-after/containing/boundary/window-filter/speaker-majority/speaker-none/fixture-shape)"
        status: pass
    human_judgment: false
  - id: D4
    description: "enrich_ocr_results only adds transcript_context to matched frames; unmatched pass through by identity; input list never mutated"
    requirement: TRANSCRIPT-03
    verification:
      - kind: unit
        ref: "tests/test_transcript/test_correlator.py::test_enrich_ocr_results_only_touches_matched (identity assertion on unmatched)"
        status: pass
      - kind: unit
        ref: "tests/test_transcript/test_correlator.py::test_enrich_ocr_results_matched_dicts_are_new_objects (input-mutation fence)"
        status: pass
      - kind: unit
        ref: "tests/test_transcript/test_correlator.py::test_enrich_empty_segments_returns_shallow_copy"
        status: pass
    human_judgment: false
  - id: D5
    description: "Segment is immutable (frozen dataclass) so downstream code can't mutate transcription state"
    requirement: TRANSCRIPT-02
    verification:
      - kind: unit
        ref: "tests/test_transcript/test_whisper_transcriber.py::test_segment_is_frozen (FrozenInstanceError on attribute set)"
        status: pass
    human_judgment: false
---

# Plan 02-02 Summary -- Whisper Wrapper + Correlator

## Accomplishments

- **`src/transcript/whisper_transcriber.py`** -- whisper.cpp subprocess
  wrapper. `transcribe(audio_path, cfg)` runs `whisper-cli -oj` on a
  16 kHz mono PCM WAV, parses the JSON output into `Segment` records
  (frozen dataclass, times in seconds, empty-text filtered). Binary
  discovery tries the configured name first then falls back through
  `whisper-cli` / `main` / `whisper` so brew and source builds all
  work. Missing binary or model raises `WhisperNotAvailableError`
  with a curated install hint that mentions both the brew command
  AND the `transcript_config.binary` escape hatch.

- **`src/transcript/correlator.py`** -- pure functions (zero I/O, zero
  mutation) that turn whisper segments + frame timestamps into a
  `transcript_context` dict. `correlate()` buckets segments by
  before/at/after around a frame timestamp with a configurable
  window. `enrich_ocr_results()` only touches matched frames and
  passes unmatched through by identity (not copy) so tests can
  spot-check with `assert out[i] is inp[i]`.

- **`tests/fixtures/whisper_output.json`** -- realistic canned
  whisper.cpp `-oj` output modeled after Zee Business broadcast
  finance content. Includes the full `systeminfo` / `model` / `params`
  envelope, not just the `transcription` key -- catches parser
  regressions when upstream envelope drifts.

- **28 new tests** (13 whisper_transcriber + 15 correlator) with
  zero external dependencies. Every path -- happy, missing-binary,
  missing-model, missing-JSON, empty-text-filter, defensive-sort,
  identity-preservation, immutability -- has explicit coverage.

## Test Results

```
$ pytest tests/
67 passed in 0.39s

  tests/test_common/test_subprocess_utils.py    ............. 13/13
  tests/test_frame_extractor.py                 ................... 19/19
  tests/test_transcript/test_audio_extractor.py .......  7/7
  tests/test_transcript/test_correlator.py      ............... 15/15
  tests/test_transcript/test_whisper_transcriber.py ............. 13/13
```

## Manual Smoke

```python
>>> from src.transcript import Segment, correlate, enrich_ocr_results
>>> segs = [
...     Segment(0, 3, 'Welcome back.'),
...     Segment(3, 8, 'Top pick is Reliance target 2900.'),
...     Segment(8, 12, 'Stop loss at 2750.'),
... ]
>>> correlate(5.0, segs, 5.0)
{'before': 'Welcome back.',
 'at': 'Top pick is Reliance target 2900.',
 'after': 'Stop loss at 2750.',
 'speaker': None}
```

## Deviations From Plan

None material. The plan sketched `list[Segment]` / `str | None`
type hints (Python 3.10+ syntax); on our Python 3.9 target we use
`from __future__ import annotations` + `List[Segment]` /
`Optional[str]` from `typing`. Behavior identical.

## What This Unblocks

- **Plan 02-03** (pipeline integration) can now:
  - Import `extract_audio` + `transcribe` from `src.transcript`
  - Kick both off in a background thread parallel with OCR
  - Catch `NoAudioStreamError` / `WhisperNotAvailableError` for
    graceful skip paths
  - Call `enrich_ocr_results` on the matched OCR output before
    handing to post_ocr_pipeline

- **Plan 02-04** (docs + config example) has the entire public
  surface stable; can document exact `transcript_config` keys.

## Commits

```
bbb3678 test(02-02/T4): 28 tests for whisper_transcriber + correlator
43dbb40 feat(02-02/T2+T3): correlator.py + whisper JSON fixture
0b4ae5b feat(02-02/T1): whisper_transcriber.py - whisper.cpp subprocess wrapper
```
