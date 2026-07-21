"""
rapidocr_engine.py
~~~~~~~~~~~~~~~~~~
Cross-platform OCR engine using RapidOCR (PP-OCRv4 models via ONNX Runtime).

Runs on Linux, Windows, and macOS. Whichever ONNX Runtime execution provider
is installed will be used automatically:

    - ``onnxruntime-gpu``       → CUDA   (Linux / Windows, NVIDIA)
    - ``onnxruntime-directml``  → DirectML (Windows, any GPU / iGPU)
    - ``onnxruntime-openvino``  → OpenVINO (Intel CPU / GPU)
    - ``onnxruntime`` (CPU)      → default fallback everywhere

Models are downloaded on first run (~15 MB total) and cached inside the
``rapidocr_onnxruntime`` package directory.

Why threading (not multiprocessing)?
------------------------------------
ONNX Runtime sessions are thread-safe for concurrent inference calls, so we
share a single reader across worker threads. This gives us throughput without
the fork/spawn overhead that AppleVisionEngine needs to bypass PyObjC.
"""

import logging
import threading
from pathlib import Path

from .base_engine import OCREngine

logger = logging.getLogger(__name__)

# Shared reader — RapidOCR/ONNX Runtime sessions are thread-safe.
_reader = None
_reader_lock = threading.Lock()


class RapidOCREngine(OCREngine):
    """Cross-platform OCR engine backed by RapidOCR (PP-OCRv4 ONNX models)."""

    def __init__(
        self,
        workers: int = 2,
        confidence_threshold: float = 0.5,
        use_det: bool = True,
        use_cls: bool = True,
        use_rec: bool = True,
    ):
        """
        Parameters
        ----------
        workers : int
            Number of frames processed concurrently via ThreadPoolExecutor
            (see ``ocr_engine.run_ocr``). ONNX Runtime is thread-safe.
            Default 2. On GPU/DirectML you can bump to 4–8.
            Controlled by ``rapidocr_workers`` in ocr_config.
        confidence_threshold : float
            Minimum per-line recognition confidence [0–1] to keep a line.
            Default 0.5. Lower to 0.3 to catch faint overlays.
        use_det, use_cls, use_rec : bool
            Enable text-detection / angle-classification / recognition stages.
            All default True (standard OCR pipeline).
        """
        self._workers = workers
        self._confidence_threshold = confidence_threshold
        self._use_det = use_det
        self._use_cls = use_cls
        self._use_rec = use_rec

    @property
    def name(self) -> str:
        return "rapidocr"

    @property
    def max_parallel_frames(self) -> int:
        return self._workers

    @property
    def supports_multiprocessing(self) -> bool:
        # ONNX Runtime is thread-safe; no need for spawn'd processes.
        return False

    def supported_languages(self) -> list:
        """PP-OCRv4 multilingual models handle these well out of the box."""
        return [
            "en", "ch", "japan", "korean", "hi", "fr", "de",
            "es", "it", "pt", "ru", "ar",
        ]

    def recognize(self, image_path: str, languages: list = None) -> str:
        """
        Perform OCR using RapidOCR.

        The ``languages`` argument is accepted for API compatibility but is
        currently informational — the default multilingual PP-OCRv4 models
        already cover the common Latin + Devanagari + CJK cases. To force a
        specific language model, install ``rapidocr-onnxruntime`` with a
        language-specific model bundle and configure via ocr_config.
        """
        path = Path(image_path).resolve()
        if not path.exists():
            from .ocr_engine import OCRError
            raise OCRError(f"Image file not found: {str(path)!r}")

        reader = self._get_reader()

        try:
            result, _elapse = reader(
                str(path),
                use_det=self._use_det,
                use_cls=self._use_cls,
                use_rec=self._use_rec,
            )
        except Exception as exc:
            from .ocr_engine import OCRError
            raise OCRError(f"RapidOCR failed for {str(path)!r}: {exc}") from exc

        if not result:
            return ""

        # Result rows are [box, text, confidence]
        accepted = []
        for row in result:
            if not row or len(row) < 3:
                continue
            _box, text, conf = row[0], row[1], row[2]
            try:
                conf_val = float(conf)
            except (TypeError, ValueError):
                continue
            if (
                isinstance(text, str)
                and text.strip()
                and conf_val >= self._confidence_threshold
            ):
                accepted.append(text)

        if len(accepted) != len(result):
            logger.debug(
                "%s: kept %d/%d lines (threshold=%.2f)",
                path.name, len(accepted), len(result),
                self._confidence_threshold,
            )
        return "\n".join(accepted)

    def _get_reader(self):
        """Lazy-init a process-wide RapidOCR reader (double-checked lock)."""
        global _reader
        if _reader is not None:
            return _reader

        with _reader_lock:
            if _reader is not None:
                return _reader
            try:
                from rapidocr_onnxruntime import RapidOCR
            except ImportError as exc:
                from .ocr_engine import OCRError
                raise OCRError(
                    "rapidocr-onnxruntime is required for the RapidOCR engine. "
                    "Install with: pip install rapidocr-onnxruntime"
                ) from exc

            logger.info(
                "Initializing RapidOCR (PP-OCRv4 ONNX). "
                "First run downloads models (~15MB)."
            )
            _reader = RapidOCR()
            logger.info("RapidOCR reader initialized")
            return _reader
