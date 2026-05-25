"""
ocr_engine.py
~~~~~~~~~~~~~
Run OCR on extracted frames using macOS Vision Framework (VNRecognizeTextRequest).
Falls back gracefully with clear error messages if not on macOS.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class OCRError(Exception):
    """Raised when OCR processing fails."""


def _perform_ocr_vision(image_path: str) -> str:
    """
    Use Apple Vision Framework to perform OCR on a single image.
    Returns the recognized text as a single string.
    """
    try:
        import Quartz
        import Vision
    except ImportError as exc:
        raise OCRError(
            "pyobjc-framework-Vision and pyobjc-framework-Quartz are required. "
            "Install with: pip install pyobjc-framework-Vision pyobjc-framework-Quartz"
        ) from exc

    path = Path(image_path).resolve()
    if not path.exists():
        raise OCRError(f"Image file not found: {str(path)!r}")

    # Load image using CoreGraphics
    image_url = Quartz.CFURLCreateWithFileSystemPath(
        None, str(path), Quartz.kCFURLPOSIXPathStyle, False
    )
    image_source = Quartz.CGImageSourceCreateWithURL(image_url, None)
    if image_source is None:
        raise OCRError(f"Cannot read image file: {str(path)!r}")

    cg_image = Quartz.CGImageSourceCreateImageAtIndex(image_source, 0, None)
    if cg_image is None:
        raise OCRError(f"Cannot decode image: {str(path)!r}")

    # Create Vision request
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setUsesLanguageCorrection_(True)

    # Create handler and perform request
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(
        cg_image, None
    )

    success = handler.performRequests_error_([request], None)
    if not success[0]:
        error = success[1]
        raise OCRError(f"Vision OCR failed for {str(path)!r}: {error}")

    # Extract text from results
    results = request.results()
    if not results:
        return ""

    text_lines = []
    for observation in results:
        candidates = observation.topCandidates_(1)
        if candidates:
            text_lines.append(candidates[0].string())

    return "\n".join(text_lines)


def run_ocr(frames: list, languages: list = None) -> list:
    """
    Run OCR on a list of frame dicts.

    Parameters
    ----------
    frames : list[dict]
        Each dict must have 'frame_path' and 'frame_name' keys.
    languages : list[str], optional
        Language codes (currently only 'en' supported in Phase 1).

    Returns
    -------
    list[dict]
        Each dict contains: frame_name, frame_path, timestamp, ocr_text
    """
    if languages is None:
        languages = ["en"]

    logger.info("Starting OCR on %d frame(s) | languages=%s", len(frames), languages)

    results = []
    success_count = 0
    error_count = 0

    for i, frame in enumerate(frames, 1):
        frame_path = frame["frame_path"]
        frame_name = frame["frame_name"]
        timestamp = frame.get("timestamp", "")

        try:
            text = _perform_ocr_vision(frame_path)
            success_count += 1
            logger.debug("OCR completed for %s (%d chars)", frame_name, len(text))
        except OCRError as exc:
            logger.warning("OCR failed for %s: %s", frame_name, exc)
            text = ""
            error_count += 1

        results.append({
            "frame_name": frame_name,
            "frame_path": frame_path,
            "timestamp": timestamp,
            "frame_number": frame.get("frame_number", i),
            "ocr_text": text,
        })

        if i % 10 == 0:
            logger.info("OCR progress: %d/%d frames processed", i, len(frames))

    logger.info(
        "OCR complete: %d success, %d errors out of %d frames",
        success_count, error_count, len(frames)
    )
    return results
