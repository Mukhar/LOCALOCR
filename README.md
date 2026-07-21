# LOCALOCR — Local Video Screen OCR Pipeline for macOS

A fully local, offline macOS pipeline that extracts frames from videos, runs OCR using Apple Vision Framework and/or EasyOCR, detects keywords, and organizes matching screens into categorized folders.

---

## Features

- **100% Local Processing** — no data leaves your machine
- **Two Modes** — **accurate** (multi-language OCR incl. Hindi) or **context** (English-only OCR + ±N context window; skips slow Hindi OCR entirely)
- **Multi-Engine OCR** — Apple Vision (English, CPU/ANE) + EasyOCR (Hindi/Indic, MPS GPU), auto-selected by language
- **Parallel Processing** — 4 concurrent threads for English-only; Apple Vision and EasyOCR run simultaneously per frame in composite mode
- **Hindi + English Support** — composite engine merges both engines with script-aware deduplication (Devanagari from EasyOCR, Latin from Apple Vision)
- **OCR-Only Mode** — skip video extraction and re-run OCR on already-extracted frames
- **Configurable Frame Intervals** — extract frames every 1, 2, 3, or N seconds
- **Batch Directory Processing** — process every video in a directory sequentially
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

### Batch Directory Mode (process videos one by one)

Point `--input` (or `video_path` in config) at a directory. LOCALOCR processes supported video files directly inside that directory in sorted filename order. Each video gets its own output folder under the configured output directory, which prevents `all_frames/` and metadata files from overwriting each other.

```bash
python main.py --input ./input_videos --output ./output
```

Example output:

```
output/
├── video_one/
│   ├── all_frames/
│   ├── matched/
│   └── metadata/
└── video_two/
  ├── all_frames/
  ├── matched/
  └── metadata/
```

Supported video extensions are `.mp4`, `.webm`, `.mkv`, `.mov`, `.avi`, and `.m4v`.

### OCR-Only Mode (skip extraction, re-run OCR on existing frames)

```bash
python main.py --ocr-only
python main.py --ocr-only --frames-dir ./output/all_frames
python main.py path/to/config.json --ocr-only --frames-dir /path/to/frames
```

`--frames-dir` defaults to `<output_directory>/all_frames` from config if not specified.

### Pipeline Modes

LOCALOCR runs in one of two modes. Set with the `mode` key in config or override with `--mode` on the CLI.

| Mode | OCR languages | Context expansion | When to use |
|---|---|---|---|
| **`accurate`** *(default)* | Whatever `languages` says (multi-language, incl. Hindi via EasyOCR) | Off | You want faithful text extraction from every frame and are willing to pay for Hindi OCR |
| **`context`** | Forced to `["en"]` (overrides `languages`) | On — each match spawns a ±N frame window | You want speed: English-only OCR (Apple Vision) is much faster; surrounding frames capture Hindi content visually so downstream Ollama vision analysis can still read it |

In `context` mode, `languages` and `ocr_engine` are ignored — the pipeline forces English-only Apple Vision. Only English keywords in `match_keywords` will ever match; Hindi keywords are dead in this mode.

#### Context mode examples

```bash
# Context mode with default ±5 window (from config)
python main.py --mode context

# Context mode with symmetric ±3 window (overrides config)
python main.py --mode context --context 3

# Context mode with asymmetric window
python main.py --mode context --context-before 2 --context-after 8

# Context mode on already-extracted frames (fast iteration on window sizing)
python main.py --ocr-only --mode context --context 5
```

CLI flags always override config values. If neither is set, defaults are `mode: accurate` and `context_mode: {frames_before: 5, frames_after: 5}`.

#### Context-mode output

Matched folder filenames carry the video basename as a prefix so screenshots from different videos in the same output tree stay distinguishable. Anchor frames (real OCR matches) keep the source frame name after the prefix; context frames additionally carry a `ctx_` marker at the very front:

```
matched/sethi/
├── june22zeebiz_frame_0037_02m14s.png     ← anchor (OCR matched "Sethi")
├── june22zeebiz_frame_0104_06m14s.png     ← anchor
├── ctx_june22zeebiz_frame_0034_02m08s.png ← context (2 frames before anchor 37)
├── ctx_june22zeebiz_frame_0035_02m10s.png
├── ctx_june22zeebiz_frame_0036_02m12s.png
├── ctx_june22zeebiz_frame_0038_02m16s.png ← context (2 frames after anchor 37)
├── ctx_june22zeebiz_frame_0039_02m18s.png
├── ctx_june22zeebiz_frame_0101_06m08s.png ← context around anchor 104
├── ...
```

The prefix is the video filename minus extension (`june22zeebiz.mp4` → `june22zeebiz`). In `--ocr-only` mode with no `video_path`, the frames-directory name is used instead. Files inside `all_frames/` are NOT prefixed — they stay byte-identical to the source frame naming.

Rules:
- **Anchors keep their real source-frame name** (with prefix); context frames are prefixed with `ctx_`.
- **Within a folder, anchors always win**: if a frame is an anchor for keyword `K`, it never appears as `ctx_` in `matched/K/`.
- **Across folders, the same frame can appear as both**: e.g. an anchor for "Sethi" may show up as a `ctx_` neighbor of a nearby "Jain" anchor in `matched/jain/`.
- **Overlapping windows are deduped**: a frame is copied at most once per keyword folder even if multiple anchors' windows overlap.

---

## Configuration

Edit `config/config.json`:

```json
{
  "video_path": "./input_videos/your_video.mp4",
  "frame_interval_seconds": 3,
  "mode": "accurate",
  "context_mode": {
    "frames_before": 5,
    "frames_after": 5
  },
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
  "log_directory": "./logs",
  "ollama_config": {
    "enabled": false,
    "url": "http://localhost:11434",
    "model": "gemma4",
    "prompt": "Analyze this screenshot...",
    "timeout_seconds": 120,
    "include_context_in_vision_analysis": false
  }
}
```

### Top-Level Options

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `video_path` | string | *required* | Path to the input video file, or a directory of videos to process sequentially |
| `mode` | string | `"accurate"` | `"accurate"` (multi-lang OCR, no context expansion) or `"context"` (English-only OCR + ±N window). See [Pipeline Modes](#pipeline-modes) |
| `context_mode` | dict | `{frames_before: 5, frames_after: 5}` | How many neighboring frames on each side of an anchor to copy as `ctx_*.png`. Only used when `mode == "context"` |
| `frame_interval_seconds` | int | `2` | Seconds between frame captures |
| `languages` | list | `["en"]` | OCR languages — **ignored in `context` mode** (forced to `["en"]`) |
| `ocr_engine` | string | `"auto"` | `"auto"`, `"apple_vision"` (macOS), `"windows_media_ocr"` (Windows), `"rapidocr"` (cross-platform), `"easyocr"`, or `"composite"` — **ignored in `context` mode** |
| `ocr_config` | dict | `{}` | Engine-specific options — see below |
| `match_keywords` | list | *required* | Keywords to search for (supports Unicode/Hindi). Hindi keywords will never match in `context` mode |
| `match_mode` | string | `"contains"` | `"contains"`, `"exact"`, or `"regex"` |
| `output_directory` | string | `"./output"` | Where results are saved |
| `log_directory` | string | `"./logs"` | Where log files are written |
| `ollama_config` | dict | `{"enabled": false}` | Post-OCR vision analysis via Ollama. `include_context_in_vision_analysis` controls whether `ctx_*.png` frames are also sent to the vision model (default `false`) |

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

### Frame Extraction Modes

**TL;DR:** Frame extraction is **fully configurable**. The default is
`interval` mode — the same behavior LOCALOCR has always had, so
**existing configs keep working with byte-identical output**. Two new
opt-in modes (`scene` and `hybrid`) let you skip redundant frames on
visually-static content.

| Mode | Default? | Behavior |
|---|---|---|
| `interval` |  **yes (default)** | Sample one frame every `frame_interval_seconds` — pre-existing baseline. Nothing to configure |
| `scene`    | opt-in            | Extract only frames where ffmpeg detects a scene change above `threshold`. PTS-driven filenames |
| `hybrid`   | opt-in            | Two-pass merge — scene detection PLUS a `max_gap_seconds` fallback tick, debounced together. **Recommended for unknown content** |

#### How to use

**Option A — keep the default (do nothing).** If your `config/config.json`
has no `extraction_mode` key, you get interval mode with the same
behavior as before. No change required.

**Option B — opt into a new mode** by adding two keys to your config:

```jsonc
// scene mode
{
  "video_path": "./input_videos/your_video.mp4",
  "frame_interval_seconds": 2,
  "extraction_mode": "scene",
  "scene_config": {
    "threshold": 0.3,
    "min_gap_seconds": 1.0
  },
  // ...rest of your existing config unchanged
}

// hybrid mode (best default for mixed / unknown content)
{
  "video_path": "./input_videos/your_video.mp4",
  "frame_interval_seconds": 2,
  "extraction_mode": "hybrid",
  "scene_config": {
    "threshold": 0.3,
    "min_gap_seconds": 1.0,
    "max_gap_seconds": 10.0
  },
  // ...rest of your existing config unchanged
}
```

**Option C — start from a working example.** Drop-in ready configs live
under `config/`:

```bash
# Try scene mode
python main.py config/config.scene.example.json

# Try hybrid mode
python main.py config/config.hybrid.example.json
```

Edit `video_path` and `match_keywords` in either file to fit your inputs.

#### All configurable knobs

| Key | Type | Default | Used by | Meaning |
|---|---|---|---|---|
| `extraction_mode` | string | `"interval"` | all | `"interval"`, `"scene"`, or `"hybrid"` |
| `frame_interval_seconds` | int | `2` | `interval` | Seconds between samples |
| `scene_config.threshold` | float [0.0-1.0] | `0.3` | `scene`, `hybrid` | Scene-change score cutoff. Lower = more frames extracted |
| `scene_config.min_gap_seconds` | float ≥ 0 | `1.0` | `scene`, `hybrid` | Debounce — drop scene frames closer together than this |
| `scene_config.max_gap_seconds` | float > 0 | `10.0` | `hybrid` only | Fallback tick — guarantee at least one sample every N seconds even if no scene change fires. Ignored in `scene` mode |

Invalid values (out-of-range `threshold`, negative gaps, non-dict
`scene_config`) fail fast with a clear error **before** any ffmpeg call.

#### When to use which

| Content type | Recommended mode | Why |
|---|---|---|
| Broadcast TV / screen recording | `scene` or `hybrid` | Big frame savings when nothing visual changes |
| Slow-changing / mostly static content | `hybrid` | Guarantees periodic samples during long static shots |
| Unknown / mixed content | `hybrid` | Best safety net — catches both scene changes AND ephemeral overlays |
| Legacy / need exact reproducibility vs pre-Phase-1 | `interval` | Default. Byte-identical to old builds |

> **Note on scene mode alone:** on real broadcast content, ephemeral
> text overlays (breaking-news lower-thirds, tickers) can appear and
> disappear without triggering a scene-change threshold. If keyword
> coverage matters, prefer **hybrid** — its `max_gap_seconds` tick
> catches those overlays that pure scene detection would miss.

#### Compare modes on your own video

```bash
python benchmark_extraction.py --video ./input_videos/your_video.mp4

# Full flag set:
python benchmark_extraction.py \
  --video ./input_videos/your_video.mp4 \
  --interval 2 \
  --threshold 0.3 \
  --min-gap 1.0 \
  --max-gap 10.0 \
  --keywords "FII,BUY,SELL" \
  --ocr-engine apple_vision
```

Prints a markdown table of `(frames, extract_s, ocr_s, match_s, total_s,
unique_matched_keywords)` per mode. Exit codes:

| Exit | Meaning |
|---|---|
| `0` | All three modes ran AND scene mode kept every keyword interval mode found |
| `1` | One of the runs crashed |
| `2` | Scene mode lost ≥ 1 keyword the interval baseline found (missing set printed to stderr) |

Useful as a **pre-commit / CI gate** when tuning `threshold` and
`min_gap_seconds` for a particular corpus — non-zero exit stops the
pipeline when a config regression drops keyword coverage.

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
│   │   ├── <video>_frame_0001_00m00s.png       # anchor (real OCR match)
│   │   └── ctx_<video>_frame_0002_00m02s.png   # context (only in `context` mode)
│   └── login/
│       └── <video>_frame_0012_00m24s.png
└── metadata/
    └── ocr_results.json  # Full OCR + match data (including is_context flag)
```

- **`<video>_frame_*.png`** — anchor frames (an actual OCR keyword match). Prefix is the source video basename (or the frames-dir name in `--ocr-only` mode).
- **`ctx_<video>_frame_*.png`** — context frames (neighbors of an anchor, copied in `context` mode only). See [Pipeline Modes](#pipeline-modes).

### Metadata Format

```json
{
  "frame": "frame_0001_00m00s.png",
  "timestamp": "00m00s",
  "matched": true,
  "matched_keywords": ["dashboard"],
  "ocr_text": "Welcome to Dashboard\nAnalytics Panel",
  "is_context": false
}
```

Context entries additionally carry `context_for_keyword` and `anchor_frame_number` for provenance.

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
│   ├── context/
│   │   └── context_expander.py  # ±N context-window expansion (context mode)
│   ├── organizer/
│   │   └── file_organizer.py    # File organization logic (anchors + ctx_ frames)
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
| **context** (English-only, Apple Vision `fast` + no LC) | 4+ frames concurrently | ~27ms/frame |
| **accurate** English-only (`fast` + no LC) | 4 frames concurrently (Apple Vision threads) | ~27ms/frame |
| **accurate** English-only (`accurate` + LC) | 4 frames concurrently | ~160ms/frame |
| **accurate** Hindi+English composite | Apple Vision + EasyOCR per frame simultaneously | ~300ms/frame (GPU) |

A 1-hour video at 3-second intervals (~1200 frames) in English-only fast mode processes in roughly 2–3 minutes. `context` mode has the same OCR cost as English-only `accurate` mode — the ±N expansion is a cheap file-copy step post-OCR.

**When to pick which mode:**
- `accurate` — you need faithful Hindi text extraction (search for Hindi keywords, index Hindi transcripts).
- `context` — you're using downstream vision analysis (Ollama) and want to feed it a rolling window per anchor. Skips Hindi OCR entirely for a large speedup.

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
