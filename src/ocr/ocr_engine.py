"""
ocr_engine.py
~~~~~~~~~~~~~
Run OCR on extracted frames using a pluggable engine system.
Supports Apple Vision Framework (default for English) and EasyOCR (for Hindi and mixed scripts).
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

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
    ocr_workers = engine_config.get("ocr_workers", 1)

    logger.info(
        "Starting OCR on %d frame(s) | languages=%s | engine=%s | workers=%d",
        len(frames), languages, engine.name, ocr_workers
    )

    def _process_frame(frame):
        frame_path = frame["frame_path"]
        frame_name = frame["frame_name"]
        try:
            text = engine.recognize(frame_path, languages)
            logger.debug("OCR completed for %s (%d chars)", frame_name, len(text))
            return frame_name, frame.get("timestamp", ""), frame.get("frame_number", 0), text, None
        except Exception as exc:
            logger.warning("OCR failed for %s: %s", frame_name, exc)
            return frame_name, frame.get("timestamp", ""), frame.get("frame_number", 0), "", exc

    if ocr_workers > 1:
        ordered = [None] * len(frames)
        with ThreadPoolExecutor(max_workers=ocr_workers) as executor:
            future_to_idx = {executor.submit(_process_frame, frame): i for i, frame in enumerate(frames)}
            done = 0
            for future in as_completed(future_to_idx):
                ordered[future_to_idx[future]] = future.result()
                done += 1
                if done % 10 == 0 or done == len(frames):
                    logger.info("OCR progress: %d/%d frames processed", done, len(frames))
        raw = ordered
    else:
        raw = []
        for i, frame in enumerate(frames, 1):
            raw.append(_process_frame(frame))
            if i % 10 == 0:
                logger.info("OCR progress: %d/%d frames processed", i, len(frames))

    results = []
    success_count = error_count = 0
    for i, (frame_name, timestamp, frame_number, text, err) in enumerate(raw):
        if err is None:
            success_count += 1
        else:
            error_count += 1
        results.append({
            "frame_name": frame_name,
            "frame_path": frames[i]["frame_path"],
            "timestamp": timestamp,
            "frame_number": frame_number,
            "ocr_text": text,
            "ocr_engine": engine.name,
        })

    logger.info(
        "OCR complete: %d success, %d errors out of %d frames (engine=%s)",
        success_count, error_count, len(frames), engine.name
    )
    return results
