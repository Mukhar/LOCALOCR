# LOCALOCR — Local Video Screen OCR Pipeline for macOS

A fully local, offline macOS pipeline that extracts frames from MP4 videos, runs OCR using Apple's native Vision Framework, detects keywords, and organizes matching screens into categorized folders.

---

## Features

- **100% Local Processing** — no data leaves your machine
- **Apple Vision Framework OCR** — native macOS text recognition, fast and accurate
- **Configurable Frame Intervals** — extract frames every 1, 2, 3, or N seconds
- **Keyword Matching** — case-insensitive contains, exact, or regex matching
- **Auto-Organization** — matched frames sorted into keyword-named folders
- **Metadata Export** — full OCR results stored as JSON for future use
- **Structured Logging** — console + file logging with configurable levels

---

## Requirements

- **macOS** (required for Apple Vision Framework)
- **Python 3.9+**
- **FFmpeg** (for video frame extraction)
- **Homebrew** (for installing ffmpeg)

---

## Installation

### 1. Install FFmpeg

```bash
brew install ffmpeg
```

### 2. Set Up Python Environment

```bash
cd LOCALOCR

# Create virtual environment
python3 -m venv .venv

# Activate it
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

---

## Usage

### Quick Start

1. Place your `.mp4` video in `input_videos/`
2. Edit `config/config.json` with your settings
3. Run the pipeline:

```bash
source .venv/bin/activate
python main.py
```

### Custom Config Path

```bash
python main.py path/to/your/config.json
```

---

## Configuration

Edit `config/config.json`:

```json
{
  "video_path": "./input_videos/your_video.mp4",
  "frame_interval_seconds": 2,
  "languages": ["en"],
  "match_keywords": [
    "dashboard",
    "payment",
    "success",
    "login"
  ],
  "match_mode": "contains",
  "output_directory": "./output",
  "log_directory": "./logs"
}
```

### Configuration Options

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `video_path` | string | *required* | Path to the input .mp4 file |
| `frame_interval_seconds` | int | `2` | Seconds between frame captures |
| `languages` | list | `["en"]` | OCR languages (Phase 1: English only) |
| `match_keywords` | list | *required* | Keywords to search for in OCR text |
| `match_mode` | string | `"contains"` | Matching mode: `contains`, `exact`, or `regex` |
| `output_directory` | string | `"./output"` | Where results are saved |
| `log_directory` | string | `"./logs"` | Where log files are written |

### Match Modes

- **`contains`** — case-insensitive substring match (recommended for MVP)
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
│   ├── payment/
│   │   └── frame_0003_00m06s.png
│   └── login/
│       └── frame_0012_00m24s.png
└── metadata/
    └── ocr_results.json  # Full OCR + match data
```

### Metadata Format

Each entry in `ocr_results.json`:

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
├── main.py                    # Entry point
├── config/
│   └── config.json            # Pipeline configuration
├── src/
│   ├── extractor/
│   │   └── frame_extractor.py # FFmpeg-based frame extraction
│   ├── ocr/
│   │   └── ocr_engine.py      # Apple Vision Framework OCR
│   ├── matcher/
│   │   └── text_matcher.py    # Keyword matching engine
│   ├── organizer/
│   │   └── file_organizer.py  # File organization logic
│   └── pipeline/
│       └── pipeline_runner.py # Pipeline orchestrator
├── input_videos/              # Place input videos here
├── output/                    # Pipeline output (auto-created)
├── logs/                      # Log files (auto-created)
├── requirements.txt           # Python dependencies
├── .gitignore
└── README.md
```

---

## Performance

- A 1-hour video with 2-second intervals (~1800 frames) processes in approximately 10–20 minutes
- OCR uses Apple's optimized Vision Framework (no GPU required)
- Frames are extracted using FFmpeg (highly optimized native binary)

---

## Troubleshooting

### "ffmpeg not found on PATH"

```bash
brew install ffmpeg
```

### "pyobjc-framework-Vision not found"

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

### OCR returns empty text

- Ensure the video contains readable text (not too small, decent contrast)
- Apple Vision Framework works best with English text in Phase 1
- Very small or stylized fonts may not be recognized

### Pipeline fails on non-.mp4 files

Only `.mp4` files are supported in Phase 1. Convert other formats first:

```bash
ffmpeg -i input.avi -c:v libx264 -c:a aac output.mp4
```

---

## Logs

Logs are written to both:
- **Console** — INFO level and above
- **File** (`logs/localocr.log`) — DEBUG level and above

Example log output:
```
[INFO] LOCALOCR Pipeline Started
[INFO] Video: ./input_videos/demo.mp4
[INFO] [Step 1/4] Extracted 120 frames
[INFO] [Step 2/4] OCR complete
[INFO] Matching complete: 15/120 frames matched
[INFO] Processing time: 45.23 seconds
```

---

## License

Private / Personal Use
