# Product Requirements Document (PRD)

## Project Name

Local Video Screen OCR Pipeline for macOS

---

# 1. Overview

Build a fully local macOS-based pipeline that:

1. Takes an `.mp4` video as input.
2. Extracts screenshots/screens (frames) at configurable intervals (e.g., every 2 or 3 seconds).
3. Runs OCR on each extracted screen locally using macOS-native OCR capabilities.
4. Detects configured keywords/text patterns from the extracted OCR text.
5. Moves matching screens into categorized folders.
6. Stores metadata locally for future extensions.

The system should work completely offline/local-first.

Future phases may include:

* Local LLM analysis using Ollama
* Semantic screen understanding
* UI flow detection
* Scene grouping
* Hindi OCR support
* AI-powered tagging

---

# 2. Problem Statement

Users often have long videos containing important screens, UI flows, presentations, subtitles, or text snippets.

Manually scanning videos is time-consuming.

The goal is to automatically:

* extract screens,
* identify text present,
* filter useful screens,
* organize them automatically.

---

# 3. Goals

## Primary Goals

* Fully local execution
* Configurable screenshot interval
* OCR-based filtering
* Simple folder-based output
* Lightweight and fast

## Secondary Goals

* Modular pipeline
* Extensible for AI/LLM integration
* Support multilingual OCR later
* Batch processing capability

---

# 4. Non-Goals (Phase 1)

* Cloud processing
* Real-time video streaming
* GPU optimization
* Advanced semantic understanding
* Face recognition
* Object detection
* OCR accuracy optimization for all languages
* Web UI

---

# 5. User Stories

## Core User Stories

### Video Processing

As a user,
I want to provide an mp4 file,
so that the system can process it automatically.

### Configurable Sampling

As a user,
I want to configure frame extraction intervals,
so that I can balance speed vs accuracy.

### OCR Extraction

As a user,
I want OCR text extracted locally,
so that no data leaves my machine.

### Filtering

As a user,
I want screens containing specific text,
so that I can quickly find relevant moments.

### Organization

As a user,
I want matching screens moved into folders,
so that results remain organized.

---

# 6. Functional Requirements

## FR1 — Video Input

### Requirements

* Accept `.mp4` files.
* Validate file existence.
* Validate supported format.
* Support single-file processing initially.

### Inputs

* Video file path
* Config file

---

## FR2 — Frame Extraction

### Requirements

* Extract screenshots at configurable intervals.
* Default interval: 2 seconds.
* Support:

  * 1 sec
  * 2 sec
  * 3 sec
  * custom intervals

### Output Naming Convention

Example:

```text
frame_0001_00m02s.png
frame_0002_00m04s.png
```

### Suggested Tool

* `ffmpeg`

### Local Processing

Everything stored locally.

---

## FR3 — OCR Processing

### Requirements

* Run OCR on every extracted frame.
* OCR must run locally on macOS.
* Phase 1 language:

  * English
* Phase 2:

  * Hindi + English

### OCR Engine Options

#### Preferred (Phase 1)

macOS Vision Framework

* Native
* Fast
* Optimized for Mac

### Alternative

* Tesseract OCR

### OCR Output

For every frame:

```json
{
  "frame": "frame_0001.png",
  "timestamp": "00:00:02",
  "text": "Welcome to Dashboard"
}
```

---

## FR4 — Text Matching Engine

### Requirements

* Match extracted OCR text against configured conditions.

### Supported Conditions

#### Exact Match

```json
["Login", "Payment Success"]
```

#### Contains

```json
["dashboard", "settings"]
```

#### Regex (Optional Phase 1.5)

```json
["otp\\s*:\\s*\\d+"]
```

### Matching Behavior

* Case-insensitive
* Trim whitespace
* Partial matching supported

---

## FR5 — File Organization

### Requirements

Move matching screens into categorized folders.

### Example Structure

```text
output/
 ├── all_frames/
 ├── matched/
 │    ├── dashboard/
 │    ├── payment/
 │    └── login/
 └── metadata/
```

### Example

If frame contains `"dashboard"`:

```text
matched/dashboard/frame_0012.png
```

---

## FR6 — Metadata Storage

### Requirements

Store OCR results locally.

### Suggested Format

JSON

### Example

```json
{
  "frame": "frame_0012.png",
  "timestamp": "00:00:24",
  "matched": true,
  "matched_keywords": ["dashboard"],
  "ocr_text": "Dashboard Analytics"
}
```

---

# 7. Configuration Requirements

## Config File

### Example

```json
{
  "video_path": "./videos/demo.mp4",
  "frame_interval_seconds": 2,
  "languages": ["en"],
  "match_keywords": [
    "dashboard",
    "payment",
    "success"
  ],
  "output_directory": "./output"
}
```

---

# 8. Technical Architecture

## High-Level Pipeline

```text
MP4 Video
    ↓
Frame Extraction
    ↓
OCR Engine
    ↓
Text Matching
    ↓
Folder Classification
    ↓
Metadata Generation
```

---

# 9. Suggested Tech Stack

## Language

### Preferred

* Python

Reason:

* Strong OCR ecosystem
* Easy filesystem handling
* Easy ffmpeg integration

---

## Video Processing

* FFmpeg

---

## OCR

### Preferred

* Apple Vision Framework

### Backup

* Tesseract OCR

---

## Local LLM (Future)

* Ollama

---

# 10. Proposed Folder Structure

```text
project/
 ├── input_videos/
 ├── output/
 │    ├── all_frames/
 │    ├── matched/
 │    └── metadata/
 ├── config/
 ├── logs/
 ├── src/
 │    ├── extractor/
 │    ├── ocr/
 │    ├── matcher/
 │    ├── organizer/
 │    └── pipeline/
 └── README.md
```

---

# 11. Performance Expectations

## Initial Target

* 1-hour video processed within 10–20 minutes locally.

## Optimization Opportunities

* Parallel OCR
* Skip duplicate frames
* Scene-change detection
* Image compression

---

# 12. Error Handling

## Cases

* Invalid mp4
* Corrupted frame
* OCR failure
* Empty OCR text
* Missing ffmpeg
* Insufficient disk space

## Expected Behavior

* Continue pipeline when possible
* Log failures
* Skip bad frames gracefully

---

# 13. Logging Requirements

## Logs Should Include

* Video start/end
* Frame extraction count
* OCR progress
* Match count
* Errors
* Processing time

### Suggested Format

```text
[INFO] Extracted 120 frames
[INFO] OCR completed for frame_0021.png
[MATCH] Keyword "dashboard" found
```

---

# 14. Future Roadmap

## Phase 2

* Hindi OCR
* Dual-language OCR
* GUI application
* Batch video processing
* OCR confidence scoring

## Phase 3

* Local LLM analysis using Ollama
* Semantic classification
* Screen summarization
* Duplicate screen removal
* Video scene segmentation

## Phase 4

* Search UI
* Timeline navigation
* Indexed OCR database
* Natural language querying

---

# 15. MVP Definition

The MVP is complete when:

* User can provide an mp4 file.
* Frames are extracted every configurable N seconds.
* OCR runs locally on macOS.
* Matching text frames are identified.
* Matching screens move into folders.
* Metadata JSON is generated.
* Entire pipeline works offline.

---

# 16. Open Technical Decisions

## Decision 1

Should OCR use:

* Apple Vision Framework
  OR
* Tesseract?

Recommendation:

* Start with Apple Vision Framework.

---

## Decision 2

Should frame extraction:

* save all frames permanently
  OR
* process temp frames and delete later?

Recommendation:

* Keep frames initially for debugging.

---

## Decision 3

Should matching support regex in MVP?

Recommendation:

* Start with simple contains matching.

---

# 17. Recommended Initial Milestones

## Milestone 1

Basic frame extraction using FFmpeg

## Milestone 2

Local OCR integration

## Milestone 3

Keyword matching engine

## Milestone 4

Folder organization

## Milestone 5

Config-driven execution

## Milestone 6

Metadata + logging

---

# 18. Success Metrics

## Functional Metrics

* OCR runs locally
* Screens extracted successfully
* Matching accuracy acceptable

## Performance Metrics

* <20 mins for 1-hour video
* Low memory usage
* Stable local execution

## User Metrics

* Reduced manual video scanning time
* Easy retrieval of important screens
