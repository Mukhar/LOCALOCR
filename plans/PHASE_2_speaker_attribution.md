# Phase 2 — Speaker & Transcript Attribution (whisper.cpp)

**Depends on:** Phase 1 (scene-change extraction). Not strictly required,
but scene mode dramatically reduces the number of frames whose transcript
we need to correlate — makes Phase 2 cheaper.

**Goal:** Run local audio transcription in parallel with OCR, then
correlate each matched frame with the words spoken around it. Every
stock pick in the dashboard gets **who said it** plus a sentence of
surrounding context. Turns a screenshot dashboard into an actual
research tool.

**Why whisper.cpp:** Fully local (fits the "offline-first" charter),
Metal-accelerated on Apple Silicon, ~5-10x realtime for the `base.en`
model, no Python GIL issues, no PyTorch, no Ollama dependency creep.

---

## Success criteria

- [ ] `python main.py` produces a `output/metadata/transcript.json` file
  containing whisper.cpp segments with `start`, `end`, `text`, and
  (optionally) `speaker` tags.
- [ ] Every entry in `output/metadata/ocr_results.json` for a matched
  frame gets a new `transcript_context` field: `{ "before": "…", "at":
  "…", "after": "…", "speaker": "SPEAKER_00" }`.
- [ ] `post_ocr_pipeline.py` includes the transcript context when
  building each Ollama prompt, giving the vision model actual spoken
  context (huge accuracy win — the model no longer guesses from
  visual-only cues).
- [ ] `viewer.html` shows the transcript snippet under each stock pick.
- [ ] Audio-less videos degrade gracefully (log a warning, skip Phase 2,
  everything else works).
- [ ] Whisper runs in a background thread while OCR runs — total wall
  time ≈ `max(ocr_time, whisper_time)` not their sum.
- [ ] Feature is behind a config flag (`transcript_config.enabled`) so
  users on non-whisper systems aren't broken.

---

## Design

### Where the change lives
- **New module:** `src/transcript/whisper_transcriber.py` — the
  whisper.cpp subprocess wrapper.
- **New module:** `src/transcript/correlator.py` — timestamp-to-frame
  correlation logic. Zero I/O, pure functions, easy to test.
- **Touched module:** `src/pipeline/pipeline_runner.py` — kicks off
  transcription in a background thread right after frame extraction, then
  awaits it before the "generate metadata" step.
- **Touched module:** `post_ocr_pipeline.py` — reads the transcript
  context per frame and includes it in vision prompts + dashboard.

Speaker diarization is **optional and pluggable** — Phase 2a ships
whisper-only (single-speaker attribution just uses the segment text),
Phase 2b (later) adds `pyannote.audio` diarization. This is the correct
YAGNI move: 80% of the value with 20% of the setup pain.

### Config schema addition

```jsonc
{
  "transcript_config": {
    "enabled": true,
    "model": "base.en",              // whisper.cpp model name
    "model_dir": "~/.whisper.cpp/models",
    "binary": "whisper-cli",          // whisper.cpp CLI executable name
    "context_window_seconds": 8,      // ± seconds around each matched frame
    "language": "en",
    "diarize": false                  // Phase 2b — reserved for now
  }
}
```

### Whisper.cpp invocation

whisper.cpp `whisper-cli` (or `main`) outputs SRT/JSON. We use JSON:
```
whisper-cli \
  -m ~/.whisper.cpp/models/ggml-base.en.bin \
  -f <audio.wav> \
  -oj \                          # JSON output
  -of <output_prefix> \
  -l en \
  -t 8                            # threads
```

Audio extraction from video via ffmpeg (already a dependency):
```
ffmpeg -i <video> -vn -acodec pcm_s16le -ar 16000 -ac 1 <audio.wav>
```
16kHz mono PCM = whisper.cpp's native format, no resampling overhead.

### JSON output shape (whisper.cpp)
```json
{
  "transcription": [
    { "timestamps": { "from": "00:00:00,000", "to": "00:00:03,240" },
      "offsets":    { "from": 0, "to": 3240 },
      "text": " Welcome back to Zee Business…" },
    ...
  ]
}
```
We normalize this to our own shape in `whisper_transcriber.py`:
```python
[
  { "start": 0.0, "end": 3.24, "text": "Welcome back to Zee Business",
    "speaker": None },
  ...
]
```

### Correlator logic

`correlator.correlate_frame_to_transcript(frame_ts, segments, window)`:
- Binary-search `segments` (sorted by `start`) for segments overlapping
  `[frame_ts - window, frame_ts + window]`.
- Return `{ "before": "<text of segments ending before frame_ts>",
             "at":     "<segment(s) containing frame_ts>",
             "after":  "<segments starting after frame_ts>",
             "speaker": "<majority speaker in the window, if any>" }`.
- Pure function. Fully unit-testable without any real audio.

### Parallelism

`pipeline_runner.run_pipeline`:
```python
transcript_future = None
if transcript_cfg.get("enabled"):
    transcript_future = _executor.submit(
        transcribe_video, video_path, transcript_cfg, metadata_dir
    )

# ... run steps 1-4 (extract, OCR, match, organize) as normal ...

transcript = None
if transcript_future:
    try:
        transcript = transcript_future.result(timeout=…)
    except Exception as exc:
        logger.warning("Transcription failed: %s (continuing without it)", exc)

# metadata step gets `transcript` and enriches matched frames
```

Whisper on an M-series Mac with `base.en` on a 30-minute video takes
roughly 3-5 minutes. OCR on the same video takes roughly 2-4 minutes.
Running them in parallel → total pipeline time barely moves.

### Graceful degradation matrix

| Scenario | Behavior |
|----------|----------|
| `transcript_config.enabled = false` | Skip entirely, warn nobody |
| `whisper-cli` binary not on PATH | Log helpful install instruction, skip Phase 2, continue |
| Model file not found | Log expected download command, skip Phase 2, continue |
| Video has no audio stream | Detect via ffprobe, log warning, skip Phase 2 |
| Whisper crashes / times out | Log stderr excerpt, skip Phase 2, continue |
| `--ocr-only` mode | Skip Phase 2 (no video available) with clear log line |

**Zero scenario causes pipeline failure.** Transcript is purely
additive; its absence just means matched frames lack the `transcript_context`
field.

---

## Task breakdown

### Task 2.1 — whisper.cpp bootstrap docs (~30 min)
- Add a `docs/setup_whisper.md` with:
  - `brew install whisper-cpp` (macOS)
  - Model download: `bash download-ggml-model.sh base.en`
  - Verification: `whisper-cli --help`
- Update `AGENTS.md` "Dependencies" section.

### Task 2.2 — Audio extraction helper (~45 min)
- New file: `src/transcript/audio_extractor.py`
- Function: `extract_audio(video_path, out_wav_path) -> Path`
- Reuses `_require_binary`, `_run_ffmpeg` patterns from
  `frame_extractor.py` — but **do not** import from there. Extract those
  helpers into `src/common/subprocess_utils.py` FIRST (small refactor),
  then both modules import from common. DRY without cross-module coupling.
- Detects no-audio case (ffprobe reports zero audio streams) and raises
  a specific `NoAudioStreamError`.
- Unit tests: mocked subprocess, verify command shape.

### Task 2.3 — Whisper subprocess wrapper (~1.5 h)
- New file: `src/transcript/whisper_transcriber.py`
- Public function: `transcribe(audio_path, cfg) -> list[Segment]`
- `Segment` is a `dataclass(frozen=True)` with `start: float`, `end: float`,
  `text: str`, `speaker: str | None = None`.
- Builds whisper-cli command, runs it with a generous timeout
  (`max(300, duration * 2)`), parses the JSON output, normalizes to
  `Segment` list.
- Raises `WhisperNotAvailableError` / `WhisperFailureError` for the
  degradation matrix above.
- Unit tests: fixture JSON files → assert correct `Segment` list.

### Task 2.4 — Correlator (~1 h)
- New file: `src/transcript/correlator.py`
- Pure functions:
  - `frame_timestamp_seconds(frame_dict) -> float` — parses the
    `XXmYYs` timestamp already present in every frame dict. (Frame `0`
    timestamp handling: current interval mode names them from
    `(N-1)*interval`; scene mode from actual PTS. Either way the parsed
    `timestamp` field is authoritative.)
  - `correlate(frame_ts, segments, window_seconds) -> dict` — the
    before/at/after/speaker structure.
  - `enrich_ocr_results(ocr_results, segments, window_seconds) -> list` —
    returns a new list (immutability) with `transcript_context` added to
    matched entries only (unmatched frames don't need it — saves
    metadata bloat).
- Unit tests for every branch: no segments, all before, all after,
  overlapping, window larger than transcript, etc.

### Task 2.5 — Pipeline integration (~1 h)
- `pipeline_runner.py`:
  - Add a `_kickoff_transcription(video_path, cfg, metadata_dir)`
    helper that returns a `Future[list[Segment]] | None`.
  - Await the future after Step 4 but before metadata generation.
  - Feed the enriched `matched_results` through `enrich_ocr_results`.
  - Log timing: "Transcription: X.Xs (ran in parallel with OCR — saved
    Y.Ys)."
- Handles all degradation cases from the matrix. **Never** raise.
- Update the step counter (currently "1/4"…"5/5") to be consistent
  (bug B2 from the reviewer's list — we fix it in passing).

### Task 2.6 — post_ocr_pipeline integration (~1.5 h)
- Read `transcript_context` from `matched_results` metadata (or from the
  results dict passed in).
- Vision prompt gets an extra section:
  > **Spoken context (±8s):** "…what the anchor was saying around this frame…"
  >
  > **Attributed to:** SPEAKER_00
- Deduplicated picks JSON gains a `transcript_context` field so the
  dashboard can render it.
- Guard everything with `if transcript_context:` — old runs without a
  transcript still work.

### Task 2.7 — Dashboard rendering (~1 h)
- In `viewer.html` (built by `post_ocr_pipeline`), each stock-pick card
  gets a collapsible "Spoken context" section with the before/at/after
  text.
- Speaker shown as a chip if present.
- **Sanitize before injecting into HTML** (fixes reviewer's finding B4 /
  W6 XSS while we're in the neighborhood) — escape via a small helper
  in the template generator, not client-side.

### Task 2.8 — Ollama-Analyzer honors the `include_context_in_vision_analysis` flag (~30 min)
- Small consistency fix: the transcript enrichment should also respect
  the existing `ctx_*` skip behavior. Context frames (`ctx_` prefix) get
  the transcript_context of their anchor, not their own timestamp, so
  the dashboard shows consistent quotes across an anchor + its context
  cluster.

### Task 2.9 — Tests (~2 h)
File: `tests/test_transcript/` (new package).

- `test_audio_extractor.py` — mocked subprocess, no-audio detection.
- `test_whisper_transcriber.py` — fixture JSON parsing, missing binary
  handling, failure handling.
- `test_correlator.py` — every branch of the correlator function.
- `test_pipeline_integration.py` — end-to-end with mocked whisper +
  mocked ocr, verify graceful degradation of every failure mode.

### Task 2.10 — Docs (~30 min)
- `README.md` — new "Speaker attribution" section with example config.
- `AGENTS.md` — new module tree entries, config schema table update.
- Example config: `config/config.transcript.example.json`.

---

## Risks & mitigations

| Risk | Likelihood | Mitigation |
|------|-----------|-----------|
| whisper.cpp binary name varies (`main` vs `whisper-cli`) across versions | Medium | Config-driven binary name; try `whisper-cli` then fallback to `main` |
| Model download is a huge one-time UX hit (~150MB for base.en) | Medium | Clear docs + one-liner shell helper; skip Phase 2 with actionable message if model missing |
| Frame timestamps drift from wall-clock audio timestamps in scene-extraction mode | Low | Scene mode uses actual PTS from ffmpeg — same clock as audio. Zero drift. |
| Transcription blocks pipeline on very short videos where whisper takes longer than OCR | Low | It'll just take max(ocr, whisper). Log "transcription-bound" if this happens. |
| Speaker diarization is genuinely hard | High (for 2b) | Ship 2a WITHOUT diarization first. Add pyannote.audio in a separate follow-on phase if there's demand. |

---

## Definition of done

1. All new + existing tests pass.
2. End-to-end run on `input_videos/june22zeebiz.mp4` produces a
   `transcript.json` with sensible segments and every matched stock-pick
   in `viewer.html` shows a transcript snippet.
3. Deleting the whisper binary and re-running the pipeline produces a
   working run (Phase 2 skipped with a clear log message, no exceptions).
4. Wall-clock time for the full pipeline (with Phase 2 enabled) is ≤
   1.3x the wall-clock time of the pipeline with Phase 2 disabled —
   proving the parallelism actually works.
5. Docs updated.
6. Committed in ≤5 focused commits: (a) common subprocess utils
   extraction, (b) audio extractor, (c) whisper wrapper + correlator,
   (d) pipeline + post_ocr integration, (e) tests + docs.

**Estimated effort:** 1.5-2 focused days (~10-12 hours).
