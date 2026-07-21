"""
pipeline_runner.py
~~~~~~~~~~~~~~~~~~
Orchestrates the full LOCALOCR pipeline:
    Video → Frames → OCR → Match → Organize → Metadata

Also provides run_ocr_only_pipeline for running OCR on already-extracted
frames without re-running video extraction.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

from src.extractor import extract_frames, FrameExtractionError
from src.ocr import run_ocr, OCRError
from src.matcher import match_text
from src.context import expand_context_windows
from src.organizer import organize_frames
from src.analyzer import analyze_with_ollama, OllamaAnalysisError
from src.transcript import enrich_ocr_results, kickoff_transcription

logger = logging.getLogger(__name__)


class PipelineError(Exception):
    """Raised when the pipeline encounters a fatal error."""


# Step totals for consistent "[Step N/TOTAL]" log labels.
# Fix for review finding B2: previously the full pipeline mixed
# "[Step 1/5]" (steps 1-3) with "[Step 4/5]" (steps 4-5). Now every
# label reads from these single-source-of-truth constants.
_FULL_PIPELINE_STEPS = 5      # Extract, OCR, Match, Organize, Ollama
_OCR_ONLY_PIPELINE_STEPS = 4  # OCR, Match, Organize, Ollama


# ── Modes ────────────────────────────────────────────────────────────────────
MODE_ACCURATE = "accurate"
MODE_CONTEXT = "context"
_VALID_MODES = {MODE_ACCURATE, MODE_CONTEXT}


def _resolve_mode(config: dict) -> tuple[str, int, int]:
    """
    Return ``(mode, frames_before, frames_after)`` from config with validation.

    - ``mode`` defaults to ``"accurate"`` (current behavior; no context expansion).
    - In ``"context"`` mode, we force English-only OCR and apply a ±N window.
    - ``context_mode.frames_before`` / ``frames_after`` default to 5 each.
    """
    mode = str(config.get("mode", MODE_ACCURATE)).lower()
    if mode not in _VALID_MODES:
        raise PipelineError(
            f"Invalid mode {mode!r}. Must be one of: {sorted(_VALID_MODES)}"
        )

    ctx_cfg = config.get("context_mode") or {}
    frames_before = int(ctx_cfg.get("frames_before", 5))
    frames_after = int(ctx_cfg.get("frames_after", 5))
    if frames_before < 0 or frames_after < 0:
        raise PipelineError(
            f"context_mode.frames_before/frames_after must be >= 0, got "
            f"before={frames_before}, after={frames_after}"
        )
    return mode, frames_before, frames_after


def _apply_mode_to_config(config: dict, mode: str) -> list[str]:
    """
    Apply mode-specific overrides to ``config`` in place and return the
    effective OCR languages.

    In ``"context"`` mode, ``languages`` is forced to ``["en"]`` regardless of
    what the user configured — Hindi OCR is skipped entirely in this mode.
    """
    if mode == MODE_CONTEXT:
        configured = config.get("languages") or ["en"]
        if configured != ["en"]:
            logger.info(
                "Context mode: overriding languages=%s → ['en'] (English-only OCR)",
                configured,
            )
        config["languages"] = ["en"]
        # Force the auto-selector down the Apple Vision path even if the user
        # hardcoded ocr_engine to composite or easyocr for accurate mode.
        if config.get("ocr_engine") in ("composite", "easyocr"):
            logger.info(
                "Context mode: overriding ocr_engine=%r → 'apple_vision'",
                config.get("ocr_engine"),
            )
            config["ocr_engine"] = "apple_vision"
        return ["en"]
    return config.get("languages", ["en"])


def run_pipeline(config: dict) -> dict:
    """
    Execute the full LOCALOCR pipeline.

    Parameters
    ----------
    config : dict
        Configuration with keys:
        - video_path: str
        - frame_interval_seconds: int (default 2)
        - languages: list[str] (default ["en"])
        - match_keywords: list[str]
        - match_mode: str (default "contains")
        - output_directory: str (default "./output")

    Returns
    -------
    dict with pipeline summary
    """
    start_time = time.time()

    video_path = config.get("video_path")
    if not video_path:
        raise PipelineError("'video_path' is required in config")

    interval = config.get("frame_interval_seconds", 2)
    keywords = config.get("match_keywords", [])
    match_mode = config.get("match_mode", "contains")
    output_dir = config.get("output_directory", "./output")

    mode, ctx_before, ctx_after = _resolve_mode(config)
    languages = _apply_mode_to_config(config, mode)

    out_path = Path(output_dir).resolve()
    frames_dir = out_path / "all_frames"
    metadata_dir = out_path / "metadata"

    frames_dir.mkdir(parents=True, exist_ok=True)
    metadata_dir.mkdir(parents=True, exist_ok=True)

    logger.info("=" * 60)
    logger.info("LOCALOCR Pipeline Started")
    logger.info("=" * 60)
    logger.info("Video: %s", video_path)
    logger.info("Interval: %d seconds", interval)
    logger.info("Mode: %s", mode)
    if mode == MODE_CONTEXT:
        logger.info("Context window: -%d / +%d frames", ctx_before, ctx_after)
    logger.info("Languages: %s", languages)
    logger.info("Keywords: %s", keywords)
    logger.info("Output: %s", output_dir)
    logger.info("-" * 60)

    # Step 1: Frame Extraction
    logger.info("[Step 1/5] Extracting frames...")
    try:
        frames = extract_frames(video_path, str(frames_dir), interval, cfg=config)
    except FrameExtractionError as exc:
        raise PipelineError(f"Frame extraction failed: {exc}") from exc

    logger.info("[Step 1/5] Extracted %d frames", len(frames))

    if not frames:
        raise PipelineError("No frames extracted from video")

    # Kick off whisper transcription in a background thread NOW - before
    # OCR starts - so both run in parallel and total wall time stays
    # close to max(OCR, whisper) rather than OCR + whisper. Never raises;
    # any failure (missing binary/model, audio-less video) resolves the
    # Future to None with a clear warning log.
    transcript_cfg = dict(config.get("transcript_config", {}) or {})
    transcript_start = time.time()
    transcript_future = kickoff_transcription(video_path, transcript_cfg, metadata_dir)

    # Step 2: OCR Processing
    logger.info("[Step 2/5] Running OCR on %d frames...", len(frames))
    try:
        ocr_results = run_ocr(frames, languages, config)
    except OCRError as exc:
        raise PipelineError(f"OCR processing failed: {exc}") from exc

    logger.info("[Step 2/5] OCR complete")

    # Step 3: Text Matching
    logger.info("[Step 3/5] Matching text against %d keywords...", len(keywords))
    matched_results = match_text(ocr_results, keywords, match_mode)
    logger.info("[Step 3/5] Matching complete")

    # Step 3b: Context Window Expansion (context mode only)
    if mode == MODE_CONTEXT:
        logger.info(
            "[Step 3b] Expanding context windows (-%d/+%d)...",
            ctx_before, ctx_after,
        )
        matched_results = expand_context_windows(
            matched_results, ctx_before, ctx_after
        )
        logger.info("[Step 3b] Context expansion complete")

    # Step 4: File Organization
    source_prefix = Path(video_path).stem
    logger.info("[Step 4/5] Organizing files (source_prefix=%r)...", source_prefix)
    org_summary = organize_frames(matched_results, output_dir, source_prefix=source_prefix)
    logger.info("[Step 4/5] Organization complete")

    # Step 5: Ollama Vision Analysis (optional)
    ollama_cfg = dict(config.get("ollama_config", {}))
    ollama_summary = None
    if ollama_cfg.get("enabled", False):
        matched_dir = str(out_path / "matched")
        logger.info("[Step 5/5] Running Ollama vision analysis...")
        try:
            ollama_summary = analyze_with_ollama(matched_dir, output_dir, ollama_cfg)
            logger.info(
                "[Step 5/5] Ollama analysis complete: %d/%d succeeded",
                ollama_summary["succeeded"], ollama_summary["total"],
            )
        except OllamaAnalysisError as exc:
            logger.error("[Step 5/5] Ollama analysis failed: %s", exc)
    else:
        logger.info("[Step 5/5] Ollama analysis skipped (set ollama_config.enabled=true to enable)")

    # ----- Await background transcription and enrich matched results -----
    # Runs AFTER organize / Ollama so the transcript thread has maximum
    # parallel headroom. Whisper on base.en typically finishes before OCR,
    # so this is usually a no-op wait. Timeout matches whisper's own cap.
    transcript_segments: list = []
    if transcript_future is not None:
        try:
            result = transcript_future.result(timeout=3600)
            if result:
                transcript_segments = result
                transcript_elapsed = time.time() - transcript_start
                logger.info(
                    "Transcription complete: %d segments in %.2fs (background)",
                    len(transcript_segments), transcript_elapsed,
                )
        except Exception as exc:  # noqa: BLE001 - background failures must not crash the run
            logger.warning("Transcription future failed unexpectedly: %s", exc)

    if transcript_segments:
        window = float(transcript_cfg.get("context_window_seconds", 8))
        matched_results = enrich_ocr_results(matched_results, transcript_segments, window)
        logger.info(
            "Enriched matched frames with transcript_context (window=±%.1fs)", window,
        )

    # Generate Metadata
    logger.info("Generating metadata...")
    metadata = _generate_metadata(matched_results, metadata_dir)

    elapsed = time.time() - start_time

    summary = {
        "video_path": video_path,
        "mode": mode,
        "total_frames": len(frames),
        "ocr_processed": len(ocr_results),
        "matched_frames": org_summary["matched_count"],
        "context_frames": org_summary.get("context_count", 0),
        "unmatched_frames": org_summary["unmatched_count"],
        "categories": org_summary["categories"],
        "processing_time_seconds": round(elapsed, 2),
        "metadata_file": str(metadata),
        "ollama_analysis": ollama_summary,
        "transcript_segments_count": len(transcript_segments),
    }

    logger.info("=" * 60)
    logger.info("LOCALOCR Pipeline Complete")
    logger.info("Mode: %s", mode)
    logger.info("Total frames: %d", summary["total_frames"])
    logger.info("Matched frames: %d", summary["matched_frames"])
    if summary["context_frames"]:
        logger.info("Context frames: %d", summary["context_frames"])
    logger.info("Categories: %s", summary["categories"])
    logger.info("Processing time: %.2f seconds", elapsed)
    logger.info("=" * 60)

    return summary


_FRAME_NAME_RE = re.compile(r"^frame_(\d{4})_(\d{2})m(\d{2})s\.\w+$")
_SUPPORTED_IMAGE_EXTS = {".png", ".jpg", ".jpeg"}


def run_ocr_only_pipeline(config: dict, frames_dir: Optional[str] = None) -> dict:
    """
    Run OCR + matching + organizing on already-extracted frames.

    Skips video extraction entirely. Reads images from *frames_dir*
    (or ``<output_directory>/all_frames`` when not specified), then
    runs the same OCR → match → organize → metadata steps as the full
    pipeline.

    Parameters
    ----------
    config : dict
        Same config dict as ``run_pipeline``. ``video_path`` is not
        required.  ``match_keywords`` must be present.
    frames_dir : str | None
        Directory containing the pre-extracted frame images.  When
        ``None``, defaults to ``<output_directory>/all_frames``.

    Returns
    -------
    dict with pipeline summary (same shape as ``run_pipeline`` minus
    ``video_path``).
    """
    start_time = time.time()

    keywords = config.get("match_keywords", [])
    match_mode = config.get("match_mode", "contains")
    output_dir = config.get("output_directory", "./output")

    mode, ctx_before, ctx_after = _resolve_mode(config)
    languages = _apply_mode_to_config(config, mode)

    # Resolve frames source directory
    if frames_dir:
        src_dir = Path(frames_dir).resolve()
    else:
        src_dir = (Path(output_dir) / "all_frames").resolve()

    if not src_dir.exists():
        raise PipelineError(
            f"Frames directory does not exist: {src_dir}\n"
            "Run the full pipeline first or pass --frames-dir with a valid path."
        )

    image_files = sorted(
        p for p in src_dir.iterdir()
        if p.is_file() and p.suffix.lower() in _SUPPORTED_IMAGE_EXTS
    )

    if not image_files:
        raise PipelineError(
            f"No image files found in: {src_dir}\n"
            f"Supported extensions: {', '.join(_SUPPORTED_IMAGE_EXTS)}"
        )

    # Reconstruct frame dicts from filenames
    frames = []
    for p in image_files:
        m = _FRAME_NAME_RE.match(p.name)
        if m:
            frames.append({
                "frame_path": str(p),
                "frame_name": p.name,
                "timestamp": f"{m.group(2)}m{m.group(3)}s",
                "frame_number": int(m.group(1)),
            })
        else:
            frames.append({
                "frame_path": str(p),
                "frame_name": p.name,
                "timestamp": "",
                "frame_number": 0,
            })

    metadata_dir = (Path(output_dir) / "metadata").resolve()
    metadata_dir.mkdir(parents=True, exist_ok=True)

    logger.info("=" * 60)
    logger.info("LOCALOCR OCR-Only Pipeline Started")
    logger.info("=" * 60)
    logger.info("Frames directory: %s", src_dir)
    logger.info("Frames found: %d", len(frames))
    logger.info("Mode: %s", mode)
    if mode == MODE_CONTEXT:
        logger.info("Context window: -%d / +%d frames", ctx_before, ctx_after)
    logger.info("Languages: %s", languages)
    logger.info("Keywords: %s", keywords)
    logger.info("Output: %s", output_dir)
    logger.info("-" * 60)

    # OCR-only mode has no video path, so there's nothing to transcribe.
    # Log an explicit skip so users know why transcript.json isn't produced
    # (TRANSCRIPT-07). Do this even when transcript_config is disabled -
    # the log line is about the mode, not the config.
    if config.get("transcript_config", {}).get("enabled"):
        logger.info(
            "Transcription skipped: --ocr-only mode has no video to extract audio from"
        )

    # Step 1: OCR Processing
    logger.info("[Step 1/4] Running OCR on %d frames...", len(frames))
    try:
        ocr_results = run_ocr(frames, languages, config)
    except OCRError as exc:
        raise PipelineError(f"OCR processing failed: {exc}") from exc
    logger.info("[Step 1/4] OCR complete")

    # Step 2: Text Matching
    logger.info("[Step 2/4] Matching text against %d keywords...", len(keywords))
    matched_results = match_text(ocr_results, keywords, match_mode)
    logger.info("[Step 2/4] Matching complete")

    # Step 2b: Context Window Expansion (context mode only)
    if mode == MODE_CONTEXT:
        logger.info(
            "[Step 2b] Expanding context windows (-%d/+%d)...",
            ctx_before, ctx_after,
        )
        matched_results = expand_context_windows(
            matched_results, ctx_before, ctx_after
        )
        logger.info("[Step 2b] Context expansion complete")

    # Step 3: File Organization
    # OCR-only mode has no video_path; fall back to the frames directory name
    # so matched screenshots still carry a source identifier.
    source_prefix = config.get("video_path")
    source_prefix = Path(source_prefix).stem if source_prefix else src_dir.name
    logger.info("[Step 3/4] Organizing files (source_prefix=%r)...", source_prefix)
    org_summary = organize_frames(matched_results, output_dir, source_prefix=source_prefix)
    logger.info("[Step 3/4] Organization complete")

    # Step 4: Ollama Vision Analysis (optional)
    ollama_cfg = config.get("ollama_config", {})
    ollama_summary = None
    out_path = Path(output_dir).resolve()
    if ollama_cfg.get("enabled", False):
        matched_dir = str(out_path / "matched")
        logger.info("[Step 4/4] Running Ollama vision analysis...")
        try:
            ollama_summary = analyze_with_ollama(matched_dir, output_dir, ollama_cfg)
            logger.info(
                "[Step 4/4] Ollama analysis complete: %d/%d succeeded",
                ollama_summary["succeeded"], ollama_summary["total"],
            )
        except OllamaAnalysisError as exc:
            logger.error("[Step 4/4] Ollama analysis failed: %s", exc)
    else:
        logger.info("[Step 4/4] Ollama analysis skipped (set ollama_config.enabled=true to enable)")

    # Metadata
    logger.info("Generating metadata...")
    metadata_file = _generate_metadata(matched_results, metadata_dir)

    elapsed = time.time() - start_time

    summary = {
        "frames_dir": str(src_dir),
        "mode": mode,
        "total_frames": len(frames),
        "ocr_processed": len(ocr_results),
        "matched_frames": org_summary["matched_count"],
        "context_frames": org_summary.get("context_count", 0),
        "unmatched_frames": org_summary["unmatched_count"],
        "categories": org_summary["categories"],
        "processing_time_seconds": round(elapsed, 2),
        "metadata_file": str(metadata_file),
        "ollama_analysis": ollama_summary,
    }

    logger.info("=" * 60)
    logger.info("LOCALOCR OCR-Only Pipeline Complete")
    logger.info("Mode: %s", mode)
    logger.info("Total frames: %d", summary["total_frames"])
    logger.info("Matched frames: %d", summary["matched_frames"])
    if summary["context_frames"]:
        logger.info("Context frames: %d", summary["context_frames"])
    logger.info("Categories: %s", summary["categories"])
    logger.info("Processing time: %.2f seconds", elapsed)
    logger.info("=" * 60)

    return summary


def _generate_metadata(results: list, metadata_dir: Path) -> Path:
    """Write per-frame metadata JSON and a summary file."""
    metadata_dir.mkdir(parents=True, exist_ok=True)

    all_metadata = []

    for result in results:
        entry = {
            "frame": result.get("frame_name", ""),
            "timestamp": result.get("timestamp", ""),
            "matched": result.get("matched", False),
            "matched_keywords": result.get("matched_keywords", []),
            "ocr_text": result.get("ocr_text", ""),
            "is_context": bool(result.get("is_context", False)),
        }
        if entry["is_context"]:
            entry["context_for_keyword"] = result.get("context_for_keyword")
            entry["anchor_frame_number"] = result.get("anchor_frame_number")
        # Optional: transcript_context is added by src.transcript.correlator
        # only for matched frames when transcription ran. Missing key ->
        # dashboard falls back to "no spoken context available".
        if "transcript_context" in result:
            entry["transcript_context"] = result["transcript_context"]
        all_metadata.append(entry)

    output_file = metadata_dir / "ocr_results.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(all_metadata, f, indent=2, ensure_ascii=False)

    logger.info("Metadata written to %s", output_file)
    return output_file
