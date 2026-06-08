# AGENTS.md — LOCALOCR Codebase Guide

## Project Overview

LOCALOCR is a fully local, offline-first macOS video-to-text pipeline. It extracts frames from video files, runs OCR using native Apple Vision Framework and/or EasyOCR, matches text against configurable keywords, and organizes matched frames into categorized folders.

## Architecture Summary

```
main.py (CLI entry point)
└── src/pipeline/pipeline_runner.py (orchestrator)
    ├── src/extractor/frame_extractor.py (ffmpeg-based frame extraction)
    ├── src/ocr/ocr_engine.py (parallel OCR dispatcher)
    │   └── src/ocr/engine_factory.py (engine selection)
    │       ├── src/ocr/apple_vision_engine.py (macOS native OCR)
    │       ├── src/ocr/easyocr_engine.py (multilingual OCR)
    │       └── src/ocr/composite_engine.py (multi-engine merge)
    ├── src/matcher/text_matcher.py (keyword matching)
    └── src/organizer/file_organizer.py (file categorization)

post_ocr_pipeline.py (standalone post-OCR pipeline)
├── Phase 1 — Vision Extraction (Ollama vision model → structured JSON picks)
├── Phase 2 — Deduplication (Ollama LLM merge/dedup)
└── Phase 3 — HTML Dashboard (viewer.html, runs in background thread)

src/analyzer/ollama_analyzer.py (lower-level Ollama vision helper)
```

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
Extracts PNG frames from video at configurable intervals using ffmpeg/ffprobe subprocess calls. Validates inputs, probes duration, handles temp files.

### `src/ocr/`
Pluggable OCR engine system:
- **`base_engine.py`**: Abstract `OCREngine` base class
- **`engine_factory.py`**: Auto-selects engine based on language config
- **`apple_vision_engine.py`**: macOS Vision Framework via PyObjC (fast, English-optimized)
- **`easyocr_engine.py`**: EasyOCR for Hindi/Indic scripts (PyTorch-backed)
- **`composite_engine.py`**: Runs multiple engines in parallel, merges with script-aware deduplication
- **`ocr_engine.py`**: Public `run_ocr()` function with multiprocessing/threading/serial execution paths

### `src/matcher/text_matcher.py`
Matches OCR text against keywords. Supports `contains`, `exact`, and `regex` modes. Case-insensitive.

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
- **Graceful interrupt**: `threading.Event(_stop_event)` checked before every Ollama call; partial Phase-1 data is always saved.
- **Frame path enrichment**: `_enrich_with_frame_paths()` maps deduplicated picks back to source frames (by `stockPick` name) so the dashboard can show screenshot links.
- **Upside calculation**: Dashboard computes gain% from `current_price` to `target` inline in JS.
- **Folder-analyst override**: `_apply_folder_analyst()` tags each pick with the matched keyword (folder name) as the analyst field if the model doesn't detect one.

### Metadata outputs

| File | Contents |
|------|---------|
| `output/metadata/phase1_extractions.json` | Full per-frame extraction results including raw LLM responses and parse errors |
| `output/metadata/phase2_deduplicated.json` | Final deduplicated picks array |
| `output/viewer.html` | Self-contained HTML dashboard |

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
| `frame_interval_seconds` | int | Seconds between frame captures (default: 2) |
| `languages` | list[str] | OCR languages (e.g., `["en"]`, `["hi", "en"]`) |
| `ocr_engine` | string | `"auto"`, `"apple_vision"`, `"easyocr"`, `"composite"` |
| `ocr_config` | object | Engine-specific settings (workers, GPU, confidence) |
| `match_keywords` | list[str] | Keywords to search in OCR text |
| `match_mode` | string | `"contains"`, `"exact"`, `"regex"` |
| `output_directory` | string | Base output path |
| `log_directory` | string | Log file destination |

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

## Parallelism Model

- **Apple Vision**: `ProcessPoolExecutor` with `spawn` context — true multi-process parallelism bypassing GIL and PyObjC serialization. Controlled by `apple_vision_workers` config.
- **EasyOCR**: Thread-safe via global `_readtext_lock` — single reader instance, serial execution.
- **Composite**: Sub-engines run concurrently via `ThreadPoolExecutor` (different hardware: CPU/ANE vs MPS GPU).

## When Modifying Code

- Keep modules decoupled — engines implement `OCREngine` ABC
- New OCR engines: subclass `base_engine.OCREngine`, register in `engine_factory.py`
- New match modes: add branch in `text_matcher._is_match()`
- Pipeline steps: add to `pipeline_runner.run_pipeline()` sequence
- Always handle `KeyboardInterrupt` gracefully at pipeline level
