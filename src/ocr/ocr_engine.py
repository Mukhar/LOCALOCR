"""
ocr_engine.py
~~~~~~~~~~~~~
Run OCR on extracted frames using a pluggable engine system.
Supports Apple Vision Framework (default for English) and EasyOCR (for Hindi and mixed scripts).
"""

import logging

from .engine_factory import get_engine

logger = logging.getLogger(__name__)


class OCRError(Exception):
    """Raised when OCR processing fails."""


def run_ocr(frames: list, languages: list = None, config: dict = None) -> list:
    """
    Run OCR on a list of frame dicts using the configured engine.

    Parameters
    ----------
    frames : list[dict]
        Each dict must have 'frame_path' and 'frame_name' keys.
    languages : list[str], optional
        Language codes (e.g. ['en'], ['hi', 'en'] for Hindi+English).
    config : dict, optional
        Full pipeline config for engine selection. If None, uses auto-detection.

    Returns
    -------
    list[dict]
        Each dict contains: frame_name, frame_path, timestamp, ocr_text, ocr_engine
    """
    if languages is None:
        languages = ["en"]

    # Build engine config from parameters
    engine_config = dict(config) if config else {}
    if "languages" not in engine_config:
        engine_config["languages"] = languages

    engine = get_engine(engine_config)

    logger.info(
        "Starting OCR on %d frame(s) | languages=%s | engine=%s",
        len(frames), languages, engine.name
    )

    results = []
    success_count = 0
    error_count = 0

    for i, frame in enumerate(frames, 1):
        frame_path = frame["frame_path"]
        frame_name = frame["frame_name"]
        timestamp = frame.get("timestamp", "")

        try:
            text = engine.recognize(frame_path, languages)
            success_count += 1
            logger.debug("OCR completed for %s (%d chars)", frame_name, len(text))
        except OCRError as exc:
            logger.warning("OCR failed for %s: %s", frame_name, exc)
            text = ""
            error_count += 1
        except Exception as exc:
            logger.warning("OCR failed for %s: %s", frame_name, exc)
            text = ""
            error_count += 1

        results.append({
            "frame_name": frame_name,
            "frame_path": frame_path,
            "timestamp": timestamp,
            "frame_number": frame.get("frame_number", i),
            "ocr_text": text,
            "ocr_engine": engine.name,
        })

        if i % 10 == 0:
            logger.info("OCR progress: %d/%d frames processed", i, len(frames))

    logger.info(
        "OCR complete: %d success, %d errors out of %d frames (engine=%s)",
        success_count, error_count, len(frames), engine.name
    )
    return results
