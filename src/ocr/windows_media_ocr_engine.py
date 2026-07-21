"""
windows_media_ocr_engine.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~
OCR engine using Windows.Media.Ocr (the native Windows Runtime OCR API).

Windows-only. Free, GPU-accelerated (uses whatever GPU driver is available),
and ships with Windows 10+. This is the Windows equivalent of macOS's
Apple Vision Framework.

Supported languages depend on which OS language packs are installed on the
machine (Settings → Time & Language → Language → Add a language). Common
packs: en-US, hi-IN, fr-FR, de-DE, es-ES, zh-Hans-CN, ja-JP, ko-KR, ...

Dependency: ``winocr`` — a thin sync wrapper around the WinRT bindings.
Install with: ``pip install winocr``  (Windows only — no-op on other OSes)
"""

import logging
import sys
import threading
from pathlib import Path

from .base_engine import OCREngine

logger = logging.getLogger(__name__)

# Windows.Media.Ocr engine instances are cheap but re-using per language
# avoids repeated language-tag lookups.
_engines: dict = {}
_engines_lock = threading.Lock()


class WindowsMediaOcrEngine(OCREngine):
    """OCR engine backed by the native Windows.Media.Ocr WinRT API."""

    def __init__(self, workers: int = 2):
        """
        Parameters
        ----------
        workers : int
            Number of frames processed concurrently via ThreadPoolExecutor.
            Windows.Media.Ocr is thread-safe; default 2 gives good overlap
            between CPU image decode and GPU inference.
            Controlled by ``windows_media_ocr_workers`` in ocr_config.
        """
        if sys.platform != "win32":
            from .ocr_engine import OCRError
            raise OCRError(
                "WindowsMediaOcrEngine only works on Windows "
                f"(current platform: {sys.platform})."
            )
        self._workers = workers

    @property
    def name(self) -> str:
        return "windows_media_ocr"

    @property
    def max_parallel_frames(self) -> int:
        return self._workers

    @property
    def supports_multiprocessing(self) -> bool:
        return False

    def supported_languages(self) -> list:
        """
        Return language codes for which the OS has an installed pack.
        Requires ``winocr`` to be importable.
        """
        try:
            import winocr  # noqa: F401
            from winrt.windows.media.ocr import OcrEngine
            available = OcrEngine.available_recognizer_languages
            return [lang.language_tag for lang in available]
        except Exception:
            # Fallback: at least en-US is virtually always installed.
            return ["en-US"]

    def recognize(self, image_path: str, languages: list = None) -> str:
        """
        Perform OCR using Windows.Media.Ocr.

        Parameters
        ----------
        image_path : str
            Path to the image file.
        languages : list[str], optional
            Language codes. Only the first entry is used (Windows.Media.Ocr
            takes exactly one language per request). Maps 'en'→'en-US',
            'hi'→'hi-IN', etc.
        """
        try:
            import winocr
            from PIL import Image
        except ImportError as exc:
            from .ocr_engine import OCRError
            raise OCRError(
                "winocr and Pillow are required for Windows.Media.Ocr. "
                "Install with: pip install winocr Pillow"
            ) from exc

        path = Path(image_path).resolve()
        if not path.exists():
            from .ocr_engine import OCRError
            raise OCRError(f"Image file not found: {str(path)!r}")

        lang_tag = self._pick_language_tag(languages)

        try:
            with Image.open(str(path)) as img:
                result = winocr.recognize_pil_sync(img, lang_tag)
        except Exception as exc:
            from .ocr_engine import OCRError
            raise OCRError(
                f"Windows.Media.Ocr failed for {str(path)!r} "
                f"(lang={lang_tag!r}): {exc}"
            ) from exc

        # winocr result shape: {'text': '...', 'lines': [{'text': ...}, ...]}
        if isinstance(result, dict):
            text = result.get("text")
            if text:
                return text
            lines = result.get("lines") or []
            return "\n".join(
                line.get("text", "") for line in lines if line.get("text")
            )
        return str(result) if result else ""

    def _pick_language_tag(self, languages: list) -> str:
        """Map short codes to BCP-47 tags Windows expects."""
        mapping = {
            "en": "en-US",
            "hi": "hi-IN",
            "fr": "fr-FR",
            "de": "de-DE",
            "es": "es-ES",
            "it": "it-IT",
            "pt": "pt-PT",
            "zh": "zh-Hans-CN",
            "ja": "ja-JP",
            "ko": "ko-KR",
            "ar": "ar-SA",
            "ru": "ru-RU",
        }
        if not languages:
            return "en-US"
        first = languages[0]
        return mapping.get(first, first)
