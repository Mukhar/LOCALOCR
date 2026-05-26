"""
apple_vision_engine.py
~~~~~~~~~~~~~~~~~~~~~~
OCR engine using macOS Apple Vision Framework (VNRecognizeTextRequest).
Fast, native, optimized for English. Supports Hindi on macOS 13+.
"""

import logging
from pathlib import Path

from .base_engine import OCREngine

logger = logging.getLogger(__name__)


class AppleVisionEngine(OCREngine):
    """OCR engine backed by Apple's Vision Framework."""

    def __init__(
        self,
        recognition_level: str = "accurate",
        use_language_correction: bool = True,
        workers: int = 1,
    ):
        """
        Parameters
        ----------
        recognition_level : str
            "accurate" (default, higher quality) or "fast" (3-6× faster, good
            enough for broadcast/news screens with large clear text).
        use_language_correction : bool
            Apply linguistic post-processing. Adds ~50-80ms per frame.
            Set False for speed when text is unambiguous (tickers, lower-thirds).
        workers : int
            Number of frames that can be processed concurrently. Apple Vision
            submits to the Apple Neural Engine (ANE) which serializes requests
            internally — benchmarks show no throughput gain beyond 2 workers.
            Worker 2 overlaps CPU image-decode of the next frame with ANE
            inference on the current one. Default 2.
            Controlled by ``apple_vision_workers`` in ocr_config.
        """
        self._recognition_level = recognition_level
        self._use_language_correction = use_language_correction
        self._workers = workers

    @property
    def name(self) -> str:
        return "apple_vision"

    @property
    def max_parallel_frames(self) -> int:
        """Returns configured worker count; used as process-pool size."""
        return self._workers

    @property
    def supports_multiprocessing(self) -> bool:
        """Apple Vision can run in separate worker processes for true ANE parallelism."""
        return True

    def worker_init_args(self) -> dict:
        """Config needed to reconstruct this engine in a worker process."""
        return {
            "recognition_level": self._recognition_level,
            "use_language_correction": self._use_language_correction,
        }

    def supported_languages(self) -> list:
        """Query Vision Framework for supported languages."""
        try:
            import Vision
            request = Vision.VNRecognizeTextRequest.alloc().init()
            result = request.supportedRecognitionLanguagesAndReturnError_(None)
            if result and result[0]:
                return list(result[0])
        except Exception:
            pass
        # Fallback: known defaults for Revision 3 (macOS 13+)
        return ["en-US", "fr-FR", "it-IT", "de-DE", "es-ES", "pt-BR", "zh-Hans", "zh-Hant", "hi-Deva"]

    def recognize(self, image_path: str, languages: list = None) -> str:
        """
        Perform OCR using Apple Vision Framework.

        Parameters
        ----------
        image_path : str
            Path to the image file.
        languages : list[str], optional
            Language codes. Maps 'hi' → 'hi-Deva', 'en' → 'en-US'.

        Returns
        -------
        str
            Recognized text.
        """
        try:
            import Quartz
            import Vision
        except ImportError as exc:
            from .ocr_engine import OCRError
            raise OCRError(
                "pyobjc-framework-Vision and pyobjc-framework-Quartz are required. "
                "Install with: pip install pyobjc-framework-Vision pyobjc-framework-Quartz"
            ) from exc

        path = Path(image_path).resolve()
        if not path.exists():
            from .ocr_engine import OCRError
            raise OCRError(f"Image file not found: {str(path)!r}")

        # Load image
        image_url = Quartz.CFURLCreateWithFileSystemPath(
            None, str(path), Quartz.kCFURLPOSIXPathStyle, False
        )
        image_source = Quartz.CGImageSourceCreateWithURL(image_url, None)
        if image_source is None:
            from .ocr_engine import OCRError
            raise OCRError(f"Cannot read image file: {str(path)!r}")

        cg_image = Quartz.CGImageSourceCreateImageAtIndex(image_source, 0, None)
        if cg_image is None:
            from .ocr_engine import OCRError
            raise OCRError(f"Cannot decode image: {str(path)!r}")

        # Create request
        request = Vision.VNRecognizeTextRequest.alloc().init()
        if self._recognition_level == "fast":
            request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelFast)
        else:
            request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        request.setUsesLanguageCorrection_(self._use_language_correction)

        # Set languages if provided
        if languages:
            vision_langs = self._map_languages(languages)
            if vision_langs:
                request.setRecognitionLanguages_(vision_langs)

        # Perform request
        handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(
            cg_image, None
        )

        success = handler.performRequests_error_([request], None)
        if not success[0]:
            from .ocr_engine import OCRError
            raise OCRError(f"Vision OCR failed for {str(path)!r}: {success[1]}")

        # Extract text
        results = request.results()
        if not results:
            return ""

        text_lines = []
        for observation in results:
            candidates = observation.topCandidates_(1)
            if candidates:
                text_lines.append(candidates[0].string())

        return "\n".join(text_lines)

    def _map_languages(self, languages: list) -> list:
        """Map short language codes to Vision Framework identifiers."""
        mapping = {
            "en": "en-US",
            "hi": "hi-Deva",
            "fr": "fr-FR",
            "de": "de-DE",
            "es": "es-ES",
            "it": "it-IT",
            "pt": "pt-BR",
            "zh": "zh-Hans",
        }
        result = []
        for lang in languages:
            mapped = mapping.get(lang, lang)
            result.append(mapped)
        return result
