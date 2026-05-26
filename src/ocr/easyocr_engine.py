"""
easyocr_engine.py
~~~~~~~~~~~~~~~~~
OCR engine using EasyOCR for multilingual text recognition.
Primary engine for Hindi (Devanagari) and mixed Hindi+English text.

EasyOCR models are downloaded on first use (~100MB) and cached in ~/.EasyOCR/.
"""

import logging
import threading
from pathlib import Path

from .base_engine import OCREngine

logger = logging.getLogger(__name__)

# Lazy-loaded EasyOCR reader instances (cached per language combo)
_readers = {}

# EasyOCR's underlying PyTorch model is not thread-safe for concurrent readtext()
# calls on the same reader instance. This lock serialises all readtext() calls
# globally, making it safe to use EasyOCREngine from multiple threads.
_readtext_lock = threading.Lock()


class EasyOCREngine(OCREngine):
    """OCR engine backed by EasyOCR (supports Hindi, English, and 80+ languages)."""

    def __init__(self, gpu: bool = False, confidence_threshold: float = 0.3):
        """
        Initialize EasyOCR engine.

        Parameters
        ----------
        gpu : bool
            Whether to use GPU acceleration (MPS on Apple Silicon, CUDA on NVIDIA).
        confidence_threshold : float
            Minimum confidence score [0–1] to accept a recognised text line.
            Lines below this score are discarded. Default 0.3.
            Raise to 0.5+ for cleaner output; lower to 0.1 to capture faint text.
        """
        self._gpu = gpu
        self._confidence_threshold = confidence_threshold

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
            with _readtext_lock:
                results = reader.readtext(str(path), detail=1, paragraph=False)
        except Exception as exc:
            from .ocr_engine import OCRError
            raise OCRError(f"EasyOCR failed for {str(path)!r}: {exc}") from exc

        if not results:
            return ""

        # results is list of (bbox, text, confidence)
        accepted = [
            (text, conf) for _, text, conf in results
            if text.strip() and conf >= self._confidence_threshold
        ]
        if len(results) != len(accepted):
            logger.debug(
                "%s: kept %d/%d lines (threshold=%.2f)",
                Path(image_path).name, len(accepted), len(results),
                self._confidence_threshold,
            )
        text_lines = [text for text, _ in accepted]
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
