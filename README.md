# LOCALOCR — Local Video Screen OCR Pipeline for macOS

A fully local, offline macOS pipeline that extracts frames from videos, runs OCR using Apple Vision Framework and/or EasyOCR, detects keywords, and organizes matching screens into categorized folders.

---

## Features

- **100% Local Processing** — no data leaves your machine
- **Multi-Engine OCR** — Apple Vision (English, CPU/ANE) + EasyOCR (Hindi/Indic, MPS GPU), auto-selected by language
- **Parallel Processing** — 4 concurrent threads for English-only; Apple Vision and EasyOCR run simultaneously per frame in composite mode
- **Hindi + English Support** — composite engine merges both engines with script-aware deduplication (Devanagari from EasyOCR, Latin from Apple Vision)
- **OCR-Only Mode** — skip video extraction and re-run OCR on already-extracted frames
- **Configurable Frame Intervals** — extract frames every 1, 2, 3, or N seconds
- **Keyword Matching** — contains, exact, or regex matching; supports Unicode (Hindi keywords)
- **Auto-Organization** — matched frames sorted into keyword-named folders
- **Metadata Export** — full OCR results stored as JSON
- **Structured Logging** — console (INFO) + file (DEBUG) logging

---

## Requirements

- **macOS** (required for Apple Vision Framework)
- **Python 3.9+**
- **FFmpeg** (for video frame extraction)

---

## Installation

### 1. Install FFmpeg

```bash
brew install ffmpeg
```

### 2. Set Up Python Environment

```bash
cd LOCALOCR

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## Usage

### Full Pipeline (extract frames + OCR)

```bash
source .venv/bin/activate
python main.py                          # uses ./config/config.json
python main.py path/to/config.json      # custom config
```

### OCR-Only Mode (skip extraction, re-run OCR on existing frames)

```bash
python main.py --ocr-only
python main.py --ocr-only --frames-dir ./output/all_frames
python main.py path/to/config.json --ocr-only --frames-dir /path/to/frames
```

`--frames-dir` defaults to `<output_directory>/all_frames` from config if not specified.

---

## Configuration

Edit `config/config.json`:

```json
{
  "video_path": "./input_videos/your_video.mp4",
  "frame_interval_seconds": 3,
  "languages": ["en"],
  "ocr_engine": "auto",
  "ocr_config": {
    "recognition_level": "fast",
    "use_language_correction": false,
    "apple_vision_workers": 4,
    "easyocr_gpu": true,
    "easyocr_confidence_threshold": 0.3
  },
  "match_keywords": ["dashboard", "login"],
  "match_mode": "contains",
  "output_directory": "./output",
  "log_directory": "./logs"
}
```

### Top-Level Options

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `video_path` | string | *required* | Path to the input video file (mp4, webm, etc.) |
| `frame_interval_seconds` | int | `2` | Seconds between frame captures |
| `languages` | list | `["en"]` | OCR languages — see Engine Selection below |
| `ocr_engine` | string | `"auto"` | `"auto"`, `"apple_vision"`, `"easyocr"`, or `"composite"` |
| `ocr_config` | dict | `{}` | Engine-specific options — see below |
| `match_keywords` | list | *required* | Keywords to search for (supports Unicode/Hindi) |
| `match_mode` | string | `"contains"` | `"contains"`, `"exact"`, or `"regex"` |
| `output_directory` | string | `"./output"` | Where results are saved |
| `log_directory` | string | `"./logs"` | Where log files are written |

### `ocr_config` Options

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `recognition_level` | string | `"accurate"` | `"fast"` (~27ms/frame) or `"accurate"` (~160ms/frame) — Apple Vision only |
| `use_language_correction` | bool | `true` | Linguistic post-processing (+50ms). Set `false` for speed |
| `apple_vision_workers` | int | `4` | Parallel frame threads for English-only mode (Apple Vision is thread-safe) |
| `easyocr_gpu` | bool | `false` | Use MPS GPU for EasyOCR on Apple Silicon |
| `easyocr_confidence_threshold` | float | `0.3` | Min confidence to accept an EasyOCR text line (0–1). Raise to 0.5+ for cleaner output |

### Engine Selection (auto mode)

| `languages` | Engine selected | Hardware |
|---|---|---|
| `["en"]` | Apple Vision | CPU + Apple Neural Engine |
| `["hi"]` | EasyOCR | MPS GPU (Apple Silicon) |
| `["en", "hi"]` | Composite | Both run in parallel per frame |

### Match Modes

- **`contains`** — case-insensitive substring match (recommended)
- **`exact`** — case-insensitive full-line match
- **`regex`** — Python regex pattern matching

---

## Output Structure

```
output/
├── all_frames/          # All extracted frames
│   ├── frame_0001_00m00s.png
│   ├── frame_0002_00m02s.png
│   └── ...
├── matched/             # Frames matching keywords
│   ├── dashboard/
│   │   └── frame_0001_00m00s.png
│   └── login/
│       └── frame_0012_00m24s.png
└── metadata/
    └── ocr_results.json  # Full OCR + match data
```

### Metadata Format

```json
{
  "frame": "frame_0001_00m00s.png",
  "timestamp": "00m00s",
  "matched": true,
  "matched_keywords": ["dashboard"],
  "ocr_text": "Welcome to Dashboard\nAnalytics Panel"
}
```

---

## Project Structure

```
LOCALOCR/
├── main.py                      # CLI entry point (argparse)
├── config/
│   └── config.json              # Pipeline configuration
├── src/
│   ├── extractor/
│   │   └── frame_extractor.py   # FFmpeg-based frame extraction
│   ├── ocr/
│   │   ├── base_engine.py       # Abstract OCR engine base class
│   │   ├── engine_factory.py    # Auto-selects engine by language
│   │   ├── ocr_engine.py        # Runs OCR with ThreadPoolExecutor
│   │   ├── apple_vision_engine.py  # Apple Vision Framework (English)
│   │   ├── easyocr_engine.py    # EasyOCR (Hindi/Indic, 80+ languages)
│   │   └── composite_engine.py  # Parallel multi-engine merge
│   ├── matcher/
│   │   └── text_matcher.py      # Keyword matching engine
│   ├── organizer/
│   │   └── file_organizer.py    # File organization logic
│   └── pipeline/
│       └── pipeline_runner.py   # Full pipeline + OCR-only orchestrator
├── input_videos/                # Place input videos here
├── output/                      # Pipeline output (auto-created)
├── logs/                        # Log files (auto-created)
└── requirements.txt
```

---

## Performance

| Mode | Parallelism | Approx. speed |
|---|---|---|
| English-only (`fast` + no LC) | 4 frames concurrently (Apple Vision threads) | ~27ms/frame |
| English-only (`accurate` + LC) | 4 frames concurrently | ~160ms/frame |
| Hindi+English composite | Apple Vision + EasyOCR per frame simultaneously | ~300ms/frame (GPU) |

A 1-hour video at 3-second intervals (~1200 frames) in English-only fast mode processes in roughly 2–3 minutes.

---

## Troubleshooting

### "ffmpeg not found on PATH"

```bash
brew install ffmpeg
```

### "pyobjc-framework-Vision not found" or "easyocr not found"

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

### OCR returns empty or garbled text

- Use `recognition_level: "accurate"` for small or stylized fonts
- Raise `easyocr_confidence_threshold` to `0.5` to discard low-confidence lines
- Apple Vision works best with clear, high-contrast text

### EasyOCR models not downloading

EasyOCR downloads models (~100MB) on first use to `~/.EasyOCR/`. Ensure internet access on first run; subsequent runs are fully offline.

---

## Logs

Logs are written to both:
- **Console** — INFO level and above
- **File** (`logs/localocr.log`) — DEBUG level and above

---

## License

Private / Personal Use
