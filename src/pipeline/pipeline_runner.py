"""
pipeline_runner.py
~~~~~~~~~~~~~~~~~~
Orchestrates the full LOCALOCR pipeline:
    Video → Frames → OCR → Match → Organize → Metadata

Also provides run_ocr_only_pipeline for running OCR on already-extracted
frames without re-running video extraction.
"""

import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

from src.extractor import extract_frames, FrameExtractionError
from src.ocr import run_ocr, OCRError
from src.matcher import match_text
from src.organizer import organize_frames
from src.analyzer import analyze_with_ollama, OllamaAnalysisError

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
    logger.info("[Step 4/5] Organizing files...")
    org_summary = organize_frames(matched_results, output_dir)
    logger.info("[Step 4/5] Organization complete")

    # Step 5: Ollama Vision Analysis (optional)
    ollama_cfg = config.get("ollama_config", {})
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
        "ollama_analysis": ollama_summary,
    }

    logger.info("=" * 60)
    logger.info("LOCALOCR Pipeline Complete")
    logger.info("Total frames: %d", summary["total_frames"])
    logger.info("Matched frames: %d", summary["matched_frames"])
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
    languages = config.get("languages", ["en"])
    output_dir = config.get("output_directory", "./output")

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
    logger.info("Keywords: %s", keywords)
    logger.info("Output: %s", output_dir)
    logger.info("-" * 60)

    # Step 1: OCR Processing
    logger.info("[Step 1/3] Running OCR on %d frames...", len(frames))
    try:
        ocr_results = run_ocr(frames, languages, config)
    except OCRError as exc:
        raise PipelineError(f"OCR processing failed: {exc}") from exc
    logger.info("[Step 1/3] OCR complete")

    # Step 2: Text Matching
    logger.info("[Step 2/3] Matching text against %d keywords...", len(keywords))
    matched_results = match_text(ocr_results, keywords, match_mode)
    logger.info("[Step 2/3] Matching complete")

    # Step 3: File Organization
    logger.info("[Step 3/4] Organizing files...")
    org_summary = organize_frames(matched_results, output_dir)
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
        "total_frames": len(frames),
        "ocr_processed": len(ocr_results),
        "matched_frames": org_summary["matched_count"],
        "unmatched_frames": org_summary["unmatched_count"],
        "categories": org_summary["categories"],
        "processing_time_seconds": round(elapsed, 2),
        "metadata_file": str(metadata_file),
        "ollama_analysis": ollama_summary,
    }

    logger.info("=" * 60)
    logger.info("LOCALOCR OCR-Only Pipeline Complete")
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
