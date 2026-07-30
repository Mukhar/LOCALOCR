# Phase 1 — Scene-Change Frame Extraction

**Goal:** Replace (or complement) fixed-interval frame extraction with
ffmpeg-based scene-change detection. For screen-recording-style content
(the primary LOCALOCR use case — CNBC/Zee Business studio feeds), this
cuts frame count **5-10x with zero information loss** because static
studio shots between graphic changes are collapsed.

**Why now:** It's the highest bang-for-buck feature. Everything downstream
(OCR, matching, LLM analysis, storage) gets proportionally faster and
cheaper. Zero API surface change if we do it right.

---

## Success criteria

- [ ] `frame_interval_seconds` still works exactly as before (no regressions).
- [ ] A new `extraction_mode` config key selects the strategy:
  - `"interval"` (default, current behavior — preserves backward compat)
  - `"scene"` (new — ffmpeg `select='gt(scene,THRESHOLD)'` filter)
  - `"hybrid"` (new — scene detection with a max-gap fallback so long
    static shots still get sampled)
- [ ] Scene sensitivity is tunable via `scene_config.threshold` (0.0–1.0,
  default `0.3`). Higher = fewer frames.
- [ ] Hybrid mode enforces a `scene_config.max_gap_seconds` (default `10`)
  so we never miss a full minute of static content.
- [ ] Frame naming stays `frame_NNNN_XXmYYs.png` — downstream code
  (organizer, context_expander, viewer, post_ocr_pipeline) needs zero
  changes because the naming convention is preserved.
- [ ] Benchmark on `input_videos/june22zeebiz.mp4` (or whatever's in the
  test corpus) shows ≥5x frame reduction vs the current 2s interval,
  with **no loss** of unique matched keywords (measured by running the
  full pipeline both ways and diffing `output/metadata/ocr_results.json`).
- [ ] Unit tests cover: interval mode, scene mode, hybrid mode, invalid
  threshold, invalid mode string.
- [ ] `README.md` and `AGENTS.md` updated with the new config keys.

---

## Design

### Where the change lives
100% inside `src/extractor/frame_extractor.py`. No other module touches
the extraction command. This is a textbook Strategy-pattern situation but
we absolutely do **not** need a class hierarchy for three cases — a
dispatch dict + three helper functions is DRY and YAGNI-compliant.

### Config schema addition

```jsonc
{
  "extraction_mode": "scene",            // "interval" | "scene" | "hybrid"
  "frame_interval_seconds": 2,           // still used by "interval" and as
                                          //   the hybrid fallback tick
  "scene_config": {
    "threshold": 0.3,                    // ffmpeg scene score cutoff
    "max_gap_seconds": 10,               // hybrid mode only
    "min_gap_seconds": 1                 // debounce — drop scenes within
                                         //   this many seconds of each other
  }
}
```

### ffmpeg command shapes

**Scene mode:**
```
ffmpeg -i <video> \
  -vf "select='gt(scene,0.3)',metadata=print:file=-,showinfo" \
  -vsync vfr \
  -frame_pts true \
  frame_%04d.png
```
We parse the `showinfo` filter's stderr to grab each frame's PTS
(presentation timestamp in seconds) so we can build the correct
`frame_NNNN_XXmYYs.png` name.

**Hybrid mode:**
```
ffmpeg -i <video> \
  -vf "select='gt(scene,0.3)+eq(mod(t,10),0)',showinfo" \
  -vsync vfr \
  frame_%04d.png
```
`+` in the select expression means logical OR. So we grab a frame if
EITHER the scene changed OR we've gone `max_gap_seconds` without one.

### Debounce (min_gap_seconds)

Scene detection can double-fire on transitions (e.g., a graphic slides in
over 3 frames). Post-process the timestamp list in Python and drop any
frame whose PTS is within `min_gap_seconds` of the previous kept frame.
Keeps the extractor code simple; ffmpeg does the heavy lifting.

### Frame numbering

Sequential `NNNN` becomes the *n*th kept frame (not the *n*th frame in
the video). This preserves the invariant that downstream code sorts
lexicographically and gets chronological order. Timestamp part
(`XXmYYs`) comes from the parsed PTS, not from `(N-1) * interval`.

---

## Task breakdown

### Task 1.1 — Refactor `_run_ffmpeg` for stderr capture (~30 min)
- Current impl captures stderr but drops it on success. We need it on
  success too when using `showinfo`.
- Add a `capture_stderr: bool = False` parameter. Return `(stdout, stderr)`
  tuple instead of `None` when true.
- Verify existing callers unaffected.

### Task 1.2 — Add PTS parser (~45 min)
- New private helper `_parse_showinfo_pts(stderr: str) -> list[float]`.
- ffmpeg `showinfo` lines look like:
  `[Parsed_showinfo_1 @ 0x…] n:  0 pts:  180 pts_time:6.000000 …`
- Regex-extract `pts_time` values → sorted list of floats.
- Unit test with a captured stderr sample (fixture in `tests/fixtures/`).

### Task 1.3 — Add debounce helper (~15 min)
- `_debounce_timestamps(ts: list[float], min_gap: float) -> list[float]`
- Trivial linear scan. Unit test with edge cases (empty, single, all
  within gap, none within gap).

### Task 1.4 — Add scene extractor function (~1 h)
- `_extract_by_scene(video, out_path, tmp_dir, ffmpeg_bin, cfg) -> list[float]`
- Builds ffmpeg command, runs it with stderr capture, parses PTS list,
  debounces, returns final timestamp list.
- Reuses `_run_ffmpeg` from Task 1.1.

### Task 1.5 — Add hybrid extractor function (~30 min)
- `_extract_by_hybrid(video, out_path, tmp_dir, ffmpeg_bin, cfg) -> list[float]`
- Same shape as scene, different filter expression.
- Shares PTS parsing + debounce with scene mode (DRY).

### Task 1.6 — Refactor the interval path into `_extract_by_interval` (~30 min)
- Extract current logic into a helper matching the new signature.
- The main `extract_frames()` becomes a thin dispatcher:
  ```python
  _EXTRACTORS = {
      "interval": _extract_by_interval,
      "scene":    _extract_by_scene,
      "hybrid":   _extract_by_hybrid,
  }
  ```

### Task 1.7 — Rename-and-build-result loop factored out (~30 min)
- The tmp-file → `frame_NNNN_XXmYYs.png` rename dance is currently
  hardcoded to compute timestamps from `(N-1) * interval`. Extract into
  `_finalize_frames(tmp_dir, out_path, timestamps: list[float]) -> list[dict]`
  where `timestamps[i]` is the PTS of tmp frame `i+1`.
- Interval mode passes `[i*interval for i in range(count)]`.
- Scene/hybrid mode passes the parsed PTS list.
- One code path for all three modes → DRY win.

### Task 1.8 — Config validation (~15 min)
- Add `extraction_mode` validation to `_validate_inputs` (or a new
  `_validate_extraction_config`).
- Reject invalid modes early with a clear error.
- Validate `scene_config.threshold ∈ [0.0, 1.0]`.

### Task 1.9 — Tests (~1.5 h)
File: `tests/test_frame_extractor.py` (module currently has zero tests
per the reviewer's list — good time to fix that too).

- `test_interval_mode_backward_compat` — mock ffmpeg, verify current
  behavior unchanged.
- `test_scene_mode_parses_pts` — feed a canned stderr fixture, assert
  correct PTS list.
- `test_debounce_drops_close_frames`.
- `test_hybrid_mode_uses_correct_filter` — assert filter string in the
  cmd list.
- `test_invalid_extraction_mode` — raises with helpful message.
- `test_invalid_scene_threshold` — raises.

Mocking strategy: patch `subprocess.run`, don't spawn real ffmpeg in unit
tests. Integration test (real ffmpeg on a tiny fixture video) goes in a
separate `tests/integration/` dir so unit tests stay fast.

### Task 1.10 — Benchmark harness (~45 min)
- New script `benchmark_extraction.py` (mirrors the shape of the
  existing `benchmark_easyocr.py`).
- Runs interval / scene / hybrid on the same video, reports:
  - frame count per mode
  - extraction wall time per mode
  - full-pipeline wall time per mode
  - matched-keyword diff (set equality check)
- Prints a markdown table. Committed alongside the feature so results
  are reproducible.

### Task 1.11 — Docs (~30 min)
- Update `README.md` "Configuration" section with the new keys.
- Update `AGENTS.md` "Configuration Schema" table.
- Add a `config/config.scene.example.json` profile.

---

## Risks & mitigations

| Risk | Likelihood | Mitigation |
|------|-----------|-----------|
| ffmpeg `showinfo` output format varies across versions | Low | Pin the ffmpeg version in AGENTS.md prereqs; regex is lenient about whitespace |
| Scene detection misses subtle graphic swaps (e.g., stock ticker updates within the same shot) | Medium | Hybrid mode with a low `max_gap_seconds` (e.g., 5s) catches these; users can also lower `threshold` |
| Debounce drops a legitimately fast scene change | Low | `min_gap_seconds` defaults to `1` — well below any realistic broadcast pacing. Configurable. |
| PTS parsing breaks on H.265 or unusual containers | Low | Add a fallback: if parsed count ≠ file count, fall back to `(N-1) * frame_interval_seconds` and log a warning |

---

## Definition of done

1. All new + existing tests pass (`pytest tests/`).
2. Benchmark script run on `input_videos/june22zeebiz.mp4` shows ≥5x
   frame reduction in scene mode with no lost matched keywords.
3. `config/config.scene.example.json` exists and works end-to-end.
4. Docs updated.
5. `python main.py ./config/config.json` still works with an unmodified
   old-style config (backward-compat proof).
6. Committed in ≤4 focused commits: (a) refactor extract into strategy
   dispatch, (b) add scene mode, (c) add hybrid mode + debounce, (d)
   tests + docs + benchmark.

**Estimated effort:** 1 focused day (~6-7 hours).
