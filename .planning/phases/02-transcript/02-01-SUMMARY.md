---
phase: 02-transcript
plan: 01
subsystem: transcript, common
tags: [dry-refactor, subprocess, whisper-prep, boundary-translation]

requires:
  - phase: 01-02
    provides: frame_extractor with local _require_binary + _run_ffmpeg helpers to migrate
provides:
  - src.common.subprocess_utils.require_binary
  - src.common.subprocess_utils.run_subprocess (with capture_stdout / capture_stderr / both)
  - src.common.subprocess_utils.BinaryNotFoundError
  - src.common.subprocess_utils.SubprocessError
  - src.transcript.audio_extractor.extract_audio (16 kHz mono PCM WAV via ffmpeg)
  - src.transcript.audio_extractor.NoAudioStreamError (graceful signal for audio-less video)
  - src.transcript.audio_extractor.AudioExtractionError (hard failures)
affects: [02-02-PLAN (whisper subprocess wrapper), 02-03-PLAN (pipeline integration)]

tech-stack:
  added: []
  patterns:
    - "Boundary translation: public entry point wraps a private _impl in try/except that re-raises new-module exceptions as the pre-existing legacy exception type"
    - "Curated install-hint dict per binary (ffmpeg -> brew install; whisper-cli -> whisper.cpp URL; unknown -> generic 'put on PATH')"
    - "Test spy for shell=True regression (verifies subprocess.run kwargs never contain shell=True)"

key-files:
  created:
    - src/common/__init__.py
    - src/common/subprocess_utils.py (~150 lines)
    - src/transcript/__init__.py
    - src/transcript/audio_extractor.py (~130 lines)
    - tests/test_common/__init__.py
    - tests/test_common/test_subprocess_utils.py (13 tests)
    - tests/test_transcript/__init__.py
    - tests/test_transcript/test_audio_extractor.py (7 tests)
  modified:
    - src/extractor/frame_extractor.py (-32 lines: removed _require_binary + _run_ffmpeg; +12 lines: boundary wrapper + shared import)
    - tests/test_frame_extractor.py (patched module renamed in mock target)

key-decisions:
  - "run_subprocess return type is polymorphic: None / str / tuple[str, str] based on capture flags. Preserves the pre-refactor _run_ffmpeg(..., capture_stderr=True) contract used by scene mode showinfo parsing (str, not tuple). Both-capture returns a tuple. Keeps the common one-capture case ergonomic"
  - "Distinct BinaryNotFoundError vs SubprocessError so callers can degrade differently: missing binary -> maybe fall back to a different engine; timeout -> maybe retry"
  - "Boundary translation over adapter functions: the plan spec said 'delete _require_binary and _run_ffmpeg', but I ALMOST kept them as 8-line delegating adapters. Rejected because (a) the acceptance criteria explicitly forbade the definitions and (b) a single top-level try/except in extract_frames is cleaner than N adapter fns. Now: private _extract_frames_impl does the work, public extract_frames wraps it in one try/except that translates BinaryNotFoundError / SubprocessError -> FrameExtractionError"
  - "Curated install-hints dict (_INSTALL_HINTS) rather than one hard-coded 'brew install ffmpeg' line: means whisper-cli (Phase 2's next dep) already has a useful error message ready when it lands. Extensible with one dict entry per binary"
  - "audio_extractor uses ffprobe JSON output (not stderr parsing) to detect audio streams: safer, no false positives from stderr noise, and works cross-version"
  - "Whisper params (16 kHz, mono, pcm_s16le) as module-level constants: future model changes edit one place"

patterns-established:
  - "New Phase 2 modules import from src.common.subprocess_utils - do NOT reintroduce local require_binary / run_subprocess helpers"
  - "Graceful degradation gets a distinct exception subclass (NoAudioStreamError < AudioExtractionError). Hard failures use the base class. Callers can then except only the graceful type"

requirements-completed: []  # TRANSCRIPT-06 partial (graceful degradation building blocks); TRANSCRIPT-01 depends on 02-02

coverage:
  - id: D1
    description: "src.common.subprocess_utils exposes require_binary, run_subprocess, BinaryNotFoundError, SubprocessError with the documented API"
    requirement: TRANSCRIPT-01
    verification:
      - kind: unit
        ref: "tests/test_common/test_subprocess_utils.py (13 tests)"
        status: pass
      - kind: manual_procedural
        ref: "REPL smoke: require_binary('ffmpeg') resolves, unknown binary raises BinaryNotFoundError with hint, ffprobe -version capture_stdout returns version string"
        status: pass
    human_judgment: false
  - id: D2
    description: "frame_extractor is fully migrated - grep for old helper definitions returns 0 hits; import from src.common present; all Phase 1 tests still pass"
    requirement: TRANSCRIPT-01
    verification:
      - kind: manual_procedural
        ref: "grep 'def _require_binary\\|def _run_ffmpeg' src/extractor/frame_extractor.py -> 0"
        status: pass
      - kind: manual_procedural
        ref: "grep 'from src.common.subprocess_utils' src/extractor/frame_extractor.py -> 1"
        status: pass
      - kind: unit
        ref: "tests/test_frame_extractor.py -> 19/19 pass (all Phase 1 regression tests still green)"
        status: pass
    human_judgment: false
  - id: D3
    description: "audio_extractor produces a 16 kHz mono PCM WAV from a video with audio; raises NoAudioStreamError for audio-less video; AudioExtractionError for missing video"
    requirement: TRANSCRIPT-06
    verification:
      - kind: unit
        ref: "tests/test_transcript/test_audio_extractor.py::test_extract_audio_ffmpeg_command_shape (proves -ar 16000, -ac 1, -acodec pcm_s16le, -vn, -y)"
        status: pass
      - kind: unit
        ref: "tests/test_transcript/test_audio_extractor.py::test_extract_audio_no_audio_stream_raises"
        status: pass
      - kind: unit
        ref: "tests/test_transcript/test_audio_extractor.py::test_extract_audio_missing_video_raises"
        status: pass
      - kind: manual_procedural
        ref: "Real ffmpeg smoke: video with sine tone -> WAV produced, ffprobe confirms pcm_s16le/16000/1; video with -vn only -> NoAudioStreamError; /tmp/nope.mp4 -> AudioExtractionError"
        status: pass
    human_judgment: false
  - id: D4
    description: "shell=True security fence - no path through run_subprocess ever invokes subprocess.run(shell=True)"
    requirement: TRANSCRIPT-06
    verification:
      - kind: unit
        ref: "tests/test_common/test_subprocess_utils.py::test_run_subprocess_never_uses_shell_true (spy on subprocess.run kwargs)"
        status: pass
    human_judgment: false
---

# Plan 02-01 Summary — DRY Prep + Audio Extractor

## Accomplishments

- **`src/common/subprocess_utils.py`** — new shared module. Extracts
  `require_binary` and `run_subprocess` (plus their exception types)
  from `frame_extractor.py` so every subsystem that shells out to a
  binary uses one implementation. Polymorphic return type on
  `run_subprocess` preserves the pre-Phase-2 `capture_stderr=True`
  string contract while adding new `capture_stdout` and combined
  `(stdout, stderr)` tuple modes.
- **`src/transcript/audio_extractor.py`** — extracts 16 kHz mono
  PCM WAV audio from a video via ffmpeg. Uses shared subprocess
  helpers. Detects audio-less videos via ffprobe JSON (safer than
  stderr parsing) and raises `NoAudioStreamError` (subclass of
  `AudioExtractionError`) so downstream callers can degrade
  gracefully rather than treat this as a hard failure.
- **`frame_extractor.py`** fully migrated to the shared helpers.
  Local `_require_binary` and `_run_ffmpeg` deleted. Public
  `extract_frames()` is now a boundary translator that wraps
  private `_extract_frames_impl()` in a single try/except that
  re-raises the shared helpers' exceptions as `FrameExtractionError`
  — preserving the pre-Phase-2 public exception surface unchanged.
- **20 new unit tests** across two new packages
  (`tests/test_common/`, `tests/test_transcript/`). Includes a
  security fence (`test_run_subprocess_never_uses_shell_true`) that
  spies on `subprocess.run` kwargs so any future accidental
  `shell=True` regression trips immediately.

## Test Results

```
$ pytest tests/
39 passed in 0.39s

  tests/test_common/test_subprocess_utils.py    ......... 13/13
  tests/test_frame_extractor.py                 ......... 19/19  (Phase 1 regression)
  tests/test_transcript/test_audio_extractor.py ......... 7/7
```

## Manual Smoke (real ffmpeg)

Generated 3 test videos with `ffmpeg lavfi`:
- `with_audio.mp4` — 3 s sine tone + red color → `extract_audio` produced
  WAV; `ffprobe` confirmed `pcm_s16le,16000,1`
- `no_audio.mp4` — 3 s blue color, `-an` → `NoAudioStreamError` raised
  with `"No audio stream in ... — skipping transcription"`
- `/tmp/nope.mp4` (nonexistent) → `AudioExtractionError` raised with
  `"Video not found: ..."`

## Deviations From Plan

The plan sketched a `_has_audio_stream` implementation that either
extended `run_subprocess` OR fell back to a direct `subprocess.run`
call. **Chose the "extend" path** (cleaner, one obvious way) and added
a `capture_stdout` param plus the both-capture tuple return. This is
the "cleanest — preferred" option the plan itself explicitly favored.

The plan's Task 2 acceptance criteria required deleting
`_require_binary` and `_run_ffmpeg`. I initially wrote 8-line
delegating adapters (would keep the definitions technically present
but empty of logic). Reverted to full deletion + boundary translator
at `extract_frames` per the criteria. Net result is actually cleaner
— one try/except at the public boundary, zero delegation noise
inside the strategy functions.

## What This Unblocks

- **Plan 02-02** — whisper.cpp subprocess wrapper can `from
  src.common.subprocess_utils import run_subprocess, require_binary`
  and never reimplement the pattern. The `whisper-cli` install hint
  is already curated in `_INSTALL_HINTS`.
- **Plan 02-03** — pipeline integration can `from src.transcript
  import extract_audio, NoAudioStreamError` and know that
  audio-less videos raise a distinct type that can be caught for
  graceful skip.

## Commits

```
92327f7 test(02-01/T4): 20 unit tests for subprocess_utils + audio_extractor
b6c3f3b feat(02-01/T3): src/transcript/audio_extractor.py - 16kHz mono WAV
b7e2761 refactor(02-01/T2): frame_extractor uses src.common.subprocess_utils (DRY)
67e8c2f feat(02-01/T1): src/common/subprocess_utils.py - shared helpers
```
