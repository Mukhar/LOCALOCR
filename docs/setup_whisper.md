# Setting Up whisper.cpp for LOCALOCR

LOCALOCR uses [whisper.cpp](https://github.com/ggerganov/whisper.cpp) for local audio
transcription (Phase 2 feature). This guide walks through installation and verification
on macOS. Everything runs locally — no audio ever leaves your machine.

## macOS (Apple Silicon)

```bash
brew install whisper-cpp
```

The Homebrew formula installs the `whisper-cli` binary on your `PATH`. LOCALOCR also
falls back to the legacy `main` / `whisper` binary names if you built from source
against an older release.

## Download a Model

The `base.en` model is the recommended default for English broadcast content
(~150 MB, ~5-10x realtime on M-series Macs):

```bash
mkdir -p ~/.whisper.cpp/models
cd ~/.whisper.cpp/models
curl -L -O https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin
```

Other model sizes (all live under the same URL prefix):

| File | Size | Notes |
|------|------|-------|
| `ggml-tiny.en.bin` | ~75 MB | Fastest, lowest quality; useful for quick smoke tests |
| `ggml-base.en.bin` | ~150 MB | **Recommended default** for English |
| `ggml-small.en.bin` | ~460 MB | Best English quality that still fits in memory comfortably |
| `ggml-base.bin` | ~150 MB | Multilingual; use for Hindi / other non-English content |
| `ggml-medium.bin` | ~1.5 GB | Best multilingual quality; slower |

## Verify

```bash
whisper-cli --help | head -5
ls -lh ~/.whisper.cpp/models/ggml-base.en.bin
```

You should see help output from `whisper-cli` and a ~150 MB file listing.

## Enable in Config

Add a `transcript_config` block to your `config/config.json`:

```json
{
  "transcript_config": {
    "enabled": true,
    "model": "base.en",
    "model_dir": "~/.whisper.cpp/models",
    "binary": "whisper-cli",
    "context_window_seconds": 8,
    "language": "en"
  }
}
```

Or copy the ready-made example:

```bash
cp config/config.transcript.example.json config/config.json
```

### Config keys

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `enabled` | bool | `false` | Turn transcription on/off. When `false`, the pipeline behaves exactly like Phase 1. |
| `model` | string | `"base.en"` | Model shortname (resolved to `ggml-<model>.bin` inside `model_dir`). |
| `model_dir` | string | `"~/.whisper.cpp/models"` | Directory containing the `ggml-*.bin` files. `~` is expanded. |
| `binary` | string | *(auto)* | Explicit path or name of the whisper.cpp CLI. If unset, LOCALOCR tries `whisper-cli` then `main` then `whisper` on `PATH`. |
| `context_window_seconds` | float | `8` | Half-width of the transcript context window around each matched frame. `4` means "±4s". |
| `language` | string | `"en"` | Passed as `-l` to whisper-cli. Use `"auto"` for automatic detection. |

## What You Get

Every matched frame in `output/metadata/ocr_results.json` gains a
`transcript_context` block:

```json
{
  "frame": "june22zeebiz_frame_0150_05m00s.png",
  "matched": true,
  "matched_keywords": ["sethi"],
  "ocr_text": "SETHI SAYS: RELIANCE TARGET 2900",
  "transcript_context": {
    "before": "Now for our top pick of the day.",
    "at":     "Reliance target 2900 with stop loss 2750.",
    "after":  "Move on to our next pick.",
    "speaker": null
  }
}
```

The Ollama vision pass in `post_ocr_pipeline.py` picks this up and includes it
in the prompt to the vision model, and the generated `viewer.html` dashboard
renders it as a collapsible "Spoken context" section per pick card.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `whisper.cpp binary not found` | `brew install whisper-cpp` (or set `transcript_config.binary` to an explicit path) |
| `whisper model not found` | Run the `curl` command from the [Download a Model](#download-a-model) section |
| Transcription is slow | Try the smaller `tiny.en` model; check that whisper-cli is using Metal (`--print-progress` shows GPU acceleration) |
| Non-English broadcast content | Change `language` to your ISO code and use a multilingual model file (e.g. `ggml-base.bin` instead of `ggml-base.en.bin`) |
| `Transcription skipped: --ocr-only mode has no video to extract audio from` | Expected: `--ocr-only` runs on pre-extracted frames only. Run the full pipeline (with `video_path`) to get transcription. |
| Pipeline finishes with `Transcription skipped: ...` warning | The main OCR pipeline succeeded but transcription hit a graceful failure. Check the log message for the exact reason (missing binary, missing model, no audio stream, etc.) and re-run after fixing. |

## Removing / Disabling

To disable transcription without uninstalling whisper.cpp:

```json
{ "transcript_config": { "enabled": false } }
```

To uninstall whisper.cpp entirely:

```bash
brew uninstall whisper-cpp
rm -rf ~/.whisper.cpp/models
```

The main OCR pipeline continues to work with `enabled: false` or with whisper.cpp
completely absent — transcription is strictly additive.
