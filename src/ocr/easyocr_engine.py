"""
easyocr_engine.py
~~~~~~~~~~~~~~~~~
OCR engine using EasyOCR for multilingual text recognition.
Primary engine for Hindi (Devanagari) and mixed Hindi+English text.

EasyOCR models are downloaded on first use (~100MB) and cached in ~/.EasyOCR/.
"""

import logging
from pathlib import Path

from .base_engine import OCREngine

logger = logging.getLogger(__name__)

# Lazy-loaded EasyOCR reader instances (cached per language combo)
_readers = {}


class EasyOCREngine(OCREngine):
    """OCR engine backed by EasyOCR (supports Hindi, English, and 80+ languages)."""

    def __init__(self, gpu: bool = False):
        """
        Initialize EasyOCR engine.

        Parameters
        ----------
        gpu : bool
            Whether to use GPU acceleration (requires CUDA). Default: False (CPU).
        """
        self._gpu = gpu

    @property
    def name(self) -> str:
        return "easyocr"

    def supported_languages(self) -> list:
        """Return commonly used language codes supported by EasyOCR."""
        return ["en", "hi", "mr", "ne", "fr", "de", "es", "it", "pt", "zh", "ja", "ko", "ar", "ta", "te", "bn"]

    def recognize(self, image_path: str, languages: list = None) -> str:
        """
        Perform OCR using EasyOCR.

        Parameters
        ----------
        image_path : str
            Path to the image file.
        languages : list[str], optional
            Language codes (e.g. ['hi', 'en'] for Hindi+English).
            Default: ['en']

        Returns
        -------
        str
            Recognized text, lines joined by newline.
        """
        if languages is None:
            languages = ["en"]

        path = Path(image_path).resolve()
        if not path.exists():
            from .ocr_engine import OCRError
            raise OCRError(f"Image file not found: {str(path)!r}")

        reader = self._get_reader(languages)

        try:
            results = reader.readtext(str(path), detail=1, paragraph=False)
        except Exception as exc:
            from .ocr_engine import OCRError
            raise OCRError(f"EasyOCR failed for {str(path)!r}: {exc}") from exc

        if not results:
            return ""

        # results is list of (bbox, text, confidence)
        text_lines = [entry[1] for entry in results if entry[1].strip()]
        return "\n".join(text_lines)

    def _get_reader(self, languages: list):
        """Get or create a cached EasyOCR Reader for the given language combination."""
        global _readers

        try:
            import easyocr
        except ImportError as exc:
            from .ocr_engine import OCRError
            raise OCRError(
                "easyocr is required for Hindi OCR support. "
                "Install with: pip install easyocr"
            ) from exc

        # Cache key is the sorted tuple of languages
        key = tuple(sorted(languages))

        if key not in _readers:
            logger.info(
                "Initializing EasyOCR reader for languages: %s (first run downloads models ~100MB)",
                languages
            )
            _readers[key] = easyocr.Reader(
                list(languages),
                gpu=self._gpu,
                verbose=False,
            )
            logger.info("EasyOCR reader initialized for %s", languages)

        return _readers[key]
