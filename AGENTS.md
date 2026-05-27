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
```

## Key Conventions

- **Language**: Python 3.10+
- **No shell=True**: All subprocess calls use list-based args
- **Frame naming**: `frame_NNNN_XXmYYs.png` (4-digit sequential, timestamp)
- **Logging**: Standard `logging` module with module-level loggers
- **Error hierarchy**: Each module defines its own exception (e.g., `FrameExtractionError`, `OCRError`, `PipelineError`)
- **Config**: Single JSON file at `config/config.json`
- **Output**: `output/all_frames/`, `output/matched/<keyword>/`, `output/metadata/`

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
Copies matched frames into `output/matched/<keyword>/` folders. Sanitizes keyword to safe folder names.

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
```

## Dependencies

- `ffmpeg` / `ffprobe` (system binary, installed via Homebrew)
- `pyobjc-framework-Vision` + `pyobjc-framework-Quartz` (Apple Vision OCR)
- `Pillow` (image handling)
- `easyocr` (multilingual OCR, optional for Hindi support)

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
