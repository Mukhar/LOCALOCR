# AGENTS.md — LOCALOCR Codebase Guide

## Project Overview

LOCALOCR is a fully local, offline-first macOS video-to-text pipeline. It extracts frames from video files, runs OCR using native Apple Vision Framework and/or EasyOCR, matches text against configurable keywords, and organizes matched frames into categorized folders.

## Architecture Summary

```
main.py (CLI entry point)
└── src/pipeline/pipeline_runner.py (orchestrator)
    ├── src/extractor/frame_extractor.py (ffmpeg-based frame extraction)
    ├── src/transcript/pipeline_glue.py (background-thread transcription)
    │   └── src/transcript/audio_extractor.py + whisper_transcriber.py (parallel with OCR)
    ├── src/ocr/ocr_engine.py (parallel OCR dispatcher)
    │   └── src/ocr/engine_factory.py (engine selection)
    │       ├── src/ocr/apple_vision_engine.py (macOS native OCR)
    │       ├── src/ocr/easyocr_engine.py (multilingual OCR)
    │       └── src/ocr/composite_engine.py (multi-engine merge)
    ├── src/matcher/text_matcher.py (keyword matching)
    ├── src/context/context_expander.py (±N context window — context mode only)
    ├── src/transcript/correlator.py (attaches transcript_context to matched frames)
    └── src/organizer/file_organizer.py (file categorization + ctx_ prefix)

post_ocr_pipeline.py (standalone post-OCR pipeline)
├── Phase 1 — Vision Extraction (Ollama vision model → structured JSON picks)
├── Phase 2 — Deduplication (Ollama LLM merge/dedup)
└── Phase 3 — HTML Dashboard (viewer.html, runs in background thread)

src/analyzer/ollama_analyzer.py (lower-level Ollama vision helper)
```

## Pipeline Modes

The pipeline runs in one of two modes (config `mode` key or `--mode` CLI flag):

- **`accurate`** (default): current behavior. Multi-language OCR (incl. Hindi via EasyOCR). No context expansion.
- **`context`**: forces English-only OCR (`languages` and `ocr_engine` overridden). After matching, each anchor spawns a ±N frame window; neighbors are copied into the same `matched/<keyword>/` folder with a `ctx_` filename prefix. Configurable via `context_mode.frames_before` / `frames_after` (defaults 5 each). CLI overrides: `--context N`, `--context-before N`, `--context-after N`.

Context-mode organization rules (enforced in `src/context/context_expander.py` + `src/organizer/file_organizer.py`):
- Anchors keep their real source-frame name; context frames get the `ctx_` prefix.
- **Source prefix**: matched-folder filenames are prepended with a source identifier (video basename minus extension, e.g. `june22zeebiz_frame_NNNN_...`; falls back to the frames-dir name when `video_path` is absent, e.g. in `--ocr-only` mode). Context files become `ctx_<prefix>_frame_...`. `all_frames/` is NOT prefixed.
- **Anchors-win within a folder**: a frame that's an anchor for keyword K never appears as `ctx_` in `matched/K/`.
- **Cross-keyword allowed**: same frame can be an anchor in one folder and `ctx_` in another.
- **Overlap deduped**: per `(keyword, frame_number)`, at most one entry.

`ollama_analyzer` skips `ctx_*.png` frames by default; set `ollama_config.include_context_in_vision_analysis: true` to include them.

## Key Conventions

- **Language**: Python 3.10+
- **No shell=True**: All subprocess calls use list-based args
- **Frame naming**: `frame_NNNN_XXmYYs.png` (4-digit sequential, timestamp)
- **Logging**: Standard `logging` module with module-level loggers
- **Error hierarchy**: Each module defines its own exception (e.g., `FrameExtractionError`, `OCRError`, `PipelineError`)
- **Config**: Single JSON file at `config/config.json`
- **Output**: `output/all_frames/`, `output/matched/<keyword>/`, `output/metadata/`, `output/viewer.html`
- **Hindi/Devanagari folders**: `file_organizer` NFC-normalizes keywords and preserves Unicode combining marks (category `M`) so Devanagari folder names (e.g. `सेठी/`) are intact on disk

## Module Responsibilities

### `main.py`
CLI entry point. Parses args, loads config, sets up logging, dispatches to pipeline.

### `src/pipeline/pipeline_runner.py`
Orchestrates the 4-step pipeline: Extract → OCR → Match → Organize. Exposes `run_pipeline()` and `run_ocr_only_pipeline()`.

### `src/extractor/frame_extractor.py`
Extracts PNG frames from video using ffmpeg/ffprobe subprocess calls. Since Phase 1 uses a **strategy-dispatch** pattern: `_EXTRACTORS = {"interval": _extract_by_interval, "scene": _extract_by_scene, "hybrid": _extract_by_hybrid}`. `extract_frames()` is a thin dispatcher — selects strategy from `cfg["extraction_mode"]` (default `"interval"`, byte-identical to pre-Phase-1). Shared helpers: `_finalize_frames` (rename + build result dicts), `_parse_showinfo_pts` (ffmpeg showinfo → sorted PTS list), `_debounce_timestamps` / `_debounce_pairs` (min-gap filtering). Validates inputs, probes duration, handles temp files.

### `src/ocr/`
Pluggable OCR engine system:
- **`base_engine.py`**: Abstract `OCREngine` base class
- **`engine_factory.py`**: **OS-aware** auto-selection: macOS→Apple Vision, Windows→Windows.Media.Ocr (if `winocr` installed) else RapidOCR, Linux→RapidOCR. Composite pairs the native Latin engine with EasyOCR/RapidOCR for Indic.
- **`apple_vision_engine.py`**: macOS Vision Framework via PyObjC (fast, English-optimized, ANE-accelerated)
- **`windows_media_ocr_engine.py`**: Windows.Media.Ocr WinRT API via `winocr` (native, GPU-accelerated, Windows 10+)
- **`rapidocr_engine.py`**: PP-OCRv4 ONNX models via ONNX Runtime — cross-platform fallback (Linux/Windows). Thread-safe → uses threading path, not multiprocessing.
- **`easyocr_engine.py`**: EasyOCR for Hindi/Indic scripts (PyTorch-backed)
- **`composite_engine.py`**: Runs multiple engines in parallel, merges with script-aware deduplication
- **`ocr_engine.py`**: Public `run_ocr()` function with multiprocessing/threading/serial execution paths

### `src/matcher/text_matcher.py`
Matches OCR text against keywords. Supports `contains`, `exact`, and `regex` modes. Case-insensitive.

### `src/transcript/`
Whisper.cpp integration (Phase 2). Runs audio transcription in a background thread parallel to OCR:
- **`audio_extractor.py`**: `extract_audio()` — ffmpeg-driven WAV extraction (16 kHz mono PCM_S16LE); raises `NoAudioStreamError` for silent-film videos.
- **`whisper_transcriber.py`**: `transcribe()` — whisper.cpp subprocess wrapper. Binary alias fallback (`whisper-cli` → `main` → `whisper`). `Segment` is a frozen dataclass with seconds-normalized times.
- **`correlator.py`**: Pure functions. `enrich_ocr_results()` bolts a `transcript_context` (`before`/`at`/`after`/`speaker`) onto every matched OCR result within a configurable window. Unmatched results pass through by identity.
- **`pipeline_glue.py`**: `kickoff_transcription()` — `ThreadPoolExecutor(max_workers=1)` that runs `extract_audio` + `transcribe` and NEVER raises. Every degradation-matrix failure resolves the Future to `None` with a clear `WARNING` log.

### `src/organizer/file_organizer.py`
Copies matched frames into `output/matched/<keyword>/` folders. Sanitizes keyword to safe folder names while preserving Unicode combining marks so Devanagari/Indic script keywords produce readable folder names.

## Post-OCR Pipeline (`post_ocr_pipeline.py`)

Standalone LLM-powered analysis layer that runs **after** the main OCR pipeline. Operates on the `output/matched/` folders produced by the main pipeline.

### Phases

| Phase | Name | What it does |
|-------|------|-------------|
| 1 | Vision Extraction | Iterates `matched/<keyword>/` frames, sends each image to an Ollama vision model, and extracts structured JSON stock-pick records (`analyst`, `stockPick`, `recommended_price`, `current_price`, `stop_loss`, `target`). Two-pass extraction: pass 2 retries only if required fields are null. |
| 2 | Deduplication | Aggregates all Phase-1 picks and sends them to Ollama as a single prompt. Ollama merges partial duplicates, fills nulls, and returns a deduplicated JSON array. Falls back to undeduped data on failure. |
| 3 | HTML Dashboard | Builds `output/viewer.html` — a self-contained, filterable/sortable dark-theme stock picks dashboard. Runs in a `ThreadPoolExecutor` daemon thread so it never blocks the pipeline. |

### Key features
- **Two-pass extraction**: Pass 1 extracts, Pass 2 retries with partial result if fields are null.
- **Transcript-aware prompts**: When Phase 2 transcription ran, `_build_transcript_addendum()` appends a `Spoken context (±8s around this frame):` block to each vision prompt with quoted before/at/after snippets. Helps the model disambiguate ambiguous screens.
- **Server-side XSS defense**: `_escape_pick_strings()` recursively HTML-escapes every string in every pick before it lands in the dashboard's embedded JSON, PLUS the emitted JSON has `</` → `<\/` as defense-in-depth against `<script>` breakout.
- **Graceful interrupt**: `threading.Event(_stop_event)` checked before every Ollama call; partial Phase-1 data is always saved.
- **Frame path enrichment**: `_enrich_with_frame_paths()` maps deduplicated picks back to source frames (by `stockPick` name) so the dashboard can show screenshot links AND recovers dropped `transcript_context` if the dedup pass ignored it.
- **Upside calculation**: Dashboard computes gain% from `current_price` to `target` inline in JS.
- **Folder-analyst override**: `_apply_folder_analyst()` tags each pick with the matched keyword (folder name) as the analyst field if the model doesn't detect one.
- **Testable rendering**: `build_dashboard_html(picks, timestamp)` is a pure function that returns the HTML string; `_build_html()` delegates to it before writing to disk.

### Metadata outputs

| File | Contents |
|------|---------|
| `output/metadata/phase1_extractions.json` | Full per-frame extraction results including raw LLM responses, parse errors, and `transcript_context` per pick when transcription ran |
| `output/metadata/phase2_deduplicated.json` | Final deduplicated picks array (transcript_context preserved via `_enrich_with_frame_paths` first-frame-wins) |
| `output/metadata/transcript.json` | (Phase 2) Full whisper transcript segments: `{start, end, text, speaker}` array. Absent when transcription disabled/failed |
| `output/viewer.html` | Self-contained HTML dashboard with collapsible "Spoken context" section per pick card |

### CLI usage
```bash
python post_ocr_pipeline.py
python post_ocr_pipeline.py --matched-dir ./output/matched --output-dir ./output
python post_ocr_pipeline.py --model gemma4 --url http://localhost:11434
python post_ocr_pipeline.py --skip-dedup   # skip Phase 2
```

## `src/analyzer/ollama_analyzer.py`

Lower-level helper for sending individual matched frames to an Ollama vision model. Used for ad-hoc analysis and testing. Returns per-frame result dicts with `frame_name`, `frame_path`, `model`, `prompt`, `analysis`, `error`, and `elapsed_seconds`. Not used by the main pipeline — the production path goes through `post_ocr_pipeline.py`.

## Configuration Schema (`config/config.json`)

| Key | Type | Description |
|-----|------|-------------|
| `video_path` | string | Path to input video file |
| `frame_interval_seconds` | int | Seconds between frame captures (default: 2). Used by `interval` mode and as the hybrid-mode fallback tick (`frame_interval_seconds` if `scene_config.max_gap_seconds` is absent) |
| `extraction_mode` | string | `"interval"` (default), `"scene"`, or `"hybrid"`. See README “Frame Extraction Modes” |
| `scene_config.threshold` | float | Scene-change score cutoff for ffmpeg's `select='gt(scene,T)'` filter. Range [0.0, 1.0], default 0.3. Required for `scene` and `hybrid` modes |
| `scene_config.min_gap_seconds` | float | Debounce window — drop scene frames closer together than this. Default 1.0, must be ≥ 0 |
| `scene_config.max_gap_seconds` | float | Hybrid-mode fallback tick — guarantees at least one sample every N seconds even if no scene change fires. Default 10.0, must be > 0. Ignored in `scene` mode |
| `languages` | list[str] | OCR languages (e.g., `["en"]`, `["hi", "en"]`) |
| `ocr_engine` | string | `"auto"`, `"apple_vision"` (macOS), `"windows_media_ocr"` (Windows), `"rapidocr"` (cross-platform), `"easyocr"`, `"composite"` |
| `ocr_config` | object | Engine-specific settings (workers, GPU, confidence) |
| `match_keywords` | list[str] | Keywords to search in OCR text |
| `match_mode` | string | `"contains"`, `"exact"`, `"regex"` |
| `output_directory` | string | Base output path |
| `log_directory` | string | Log file destination |
| `transcript_config.enabled` | bool | Turn whisper.cpp transcription on/off. Default `false`. When `false`, pipeline behaves identically to Phase 1. |
| `transcript_config.model` | string | Whisper model shortname; resolves to `ggml-<model>.bin`. Default `"base.en"` |
| `transcript_config.model_dir` | string | Directory containing `ggml-*.bin` files. `~` expanded. Default `"~/.whisper.cpp/models"` |
| `transcript_config.binary` | string | Explicit whisper.cpp CLI path/name. Auto-detect if unset (tries `whisper-cli`, `main`, `whisper`) |
| `transcript_config.context_window_seconds` | float | Half-width of the transcript context window around each matched frame. Default `8` (±8s) |
| `transcript_config.language` | string | Whisper `-l` flag. Default `"en"`. Use `"auto"` for auto-detect |

## Running

```bash
# Full pipeline (video → frames → OCR → match → organize)
python main.py ./config/config.json

# OCR-only mode (skip frame extraction, use existing frames)
python main.py --ocr-only --frames-dir ./output/all_frames

# Post-OCR pipeline (LLM analysis of matched frames → HTML dashboard)
python post_ocr_pipeline.py
python post_ocr_pipeline.py --matched-dir ./output/matched --model gemma4
```

## Dependencies

- `ffmpeg` / `ffprobe` (system binary, installed via Homebrew)
- `pyobjc-framework-Vision` + `pyobjc-framework-Quartz` (Apple Vision OCR)
- `Pillow` (image handling)
- `easyocr` (multilingual OCR, optional for Hindi support)
- `requests` (Ollama API calls in post-OCR pipeline)
- `whisper.cpp` (optional; enables Phase 2 transcription — `brew install whisper-cpp` + `ggml-base.en.bin` model; see `docs/setup_whisper.md`)

## Parallelism Model

- **Apple Vision**: `ProcessPoolExecutor` with `spawn` context — true multi-process parallelism bypassing GIL and PyObjC serialization. Controlled by `apple_vision_workers` config.
- **EasyOCR**: Thread-safe via global `_readtext_lock` — single reader instance, serial execution.
- **Composite**: Sub-engines run concurrently via `ThreadPoolExecutor` (different hardware: CPU/ANE vs MPS GPU).
- **Transcription**: `ThreadPoolExecutor(max_workers=1)` in `src/transcript/pipeline_glue.py`. Kicked off right after frame extraction, awaited after Ollama analysis — whisper.cpp on base.en typically finishes before OCR on M-series hardware, so the await is often a no-op. Wall-time overhead target: <=30%.

## When Modifying Code

- Keep modules decoupled — engines implement `OCREngine` ABC
- New OCR engines: subclass `base_engine.OCREngine`, register in `engine_factory.py`
- New match modes: add branch in `text_matcher._is_match()`
- Pipeline steps: add to `pipeline_runner.run_pipeline()` sequence
- Always handle `KeyboardInterrupt` gracefully at pipeline level
