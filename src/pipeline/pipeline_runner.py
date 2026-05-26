"""
pipeline_runner.py
~~~~~~~~~~~~~~~~~~
Orchestrates the full LOCALOCR pipeline:
    Video → Frames → OCR → Match → Organize → Metadata
"""

import json
import logging
import time
from pathlib import Path

from src.extractor import extract_frames, FrameExtractionError
from src.ocr import run_ocr, OCRError
from src.matcher import match_text
from src.organizer import organize_frames

logger = logging.getLogger(__name__)


class PipelineError(Exception):
    """Raised when the pipeline encounters a fatal error."""


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
    languages = config.get("languages", ["en"])
    keywords = config.get("match_keywords", [])
    match_mode = config.get("match_mode", "contains")
    output_dir = config.get("output_directory", "./output")

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
    logger.info("Keywords: %s", keywords)
    logger.info("Output: %s", output_dir)
    logger.info("-" * 60)

    # Step 1: Frame Extraction
    logger.info("[Step 1/4] Extracting frames...")
    try:
        frames = extract_frames(video_path, str(frames_dir), interval)
    except FrameExtractionError as exc:
        raise PipelineError(f"Frame extraction failed: {exc}") from exc

    logger.info("[Step 1/4] Extracted %d frames", len(frames))

    if not frames:
        raise PipelineError("No frames extracted from video")

    # Step 2: OCR Processing
    logger.info("[Step 2/4] Running OCR on %d frames...", len(frames))
    try:
        ocr_results = run_ocr(frames, languages, config)
    except OCRError as exc:
        raise PipelineError(f"OCR processing failed: {exc}") from exc

    logger.info("[Step 2/4] OCR complete")

    # Step 3: Text Matching
    logger.info("[Step 3/4] Matching text against %d keywords...", len(keywords))
    matched_results = match_text(ocr_results, keywords, match_mode)
    logger.info("[Step 3/4] Matching complete")

    # Step 4: File Organization
    logger.info("[Step 4/4] Organizing files...")
    org_summary = organize_frames(matched_results, output_dir)
    logger.info("[Step 4/4] Organization complete")

    # Generate Metadata
    logger.info("Generating metadata...")
    metadata = _generate_metadata(matched_results, metadata_dir)

    elapsed = time.time() - start_time

    summary = {
        "video_path": video_path,
        "total_frames": len(frames),
        "ocr_processed": len(ocr_results),
        "matched_frames": org_summary["matched_count"],
        "unmatched_frames": org_summary["unmatched_count"],
        "categories": org_summary["categories"],
        "processing_time_seconds": round(elapsed, 2),
        "metadata_file": str(metadata),
    }

    logger.info("=" * 60)
    logger.info("LOCALOCR Pipeline Complete")
    logger.info("Total frames: %d", summary["total_frames"])
    logger.info("Matched frames: %d", summary["matched_frames"])
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
        }
        all_metadata.append(entry)

    output_file = metadata_dir / "ocr_results.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(all_metadata, f, indent=2, ensure_ascii=False)

    logger.info("Metadata written to %s", output_file)
    return output_file
