"""
engine_factory.py
~~~~~~~~~~~~~~~~~
Factory for selecting the appropriate OCR engine based on configuration.

Selection logic (``ocr_engine: "auto"``):
- If languages contain only Indic/non-Latin → EasyOCR (or RapidOCR fallback)
- If languages contain BOTH Latin AND Indic → Composite (native + EasyOCR/RapidOCR)
- If languages are Latin only:
    * macOS   → Apple Vision (native, ANE-accelerated)
    * Windows → Windows.Media.Ocr if importable, else RapidOCR
    * Linux   → RapidOCR (ONNX Runtime, cross-platform)

Explicit engine names always win over auto-selection:
    "apple_vision" | "easyocr" | "rapidocr" | "windows_media_ocr" | "composite"
"""

import logging
import sys

from .base_engine import OCREngine

logger = logging.getLogger(__name__)


def get_engine(config: dict = None) -> OCREngine:
    """
    Create and return the appropriate OCR engine based on config.

    Parameters
    ----------
    config : dict, optional
        Pipeline config. Relevant keys:
        - ocr_engine: str (see module docstring for valid names)
        - languages: list[str]
        - ocr_config: dict with engine-specific options

    Returns
    -------
    OCREngine
        Configured engine instance.
    """
    if config is None:
        config = {}

    engine_name = config.get("ocr_engine", "auto")
    languages = config.get("languages", ["en"])
    ocr_config = config.get("ocr_config", {})

    if engine_name == "auto":
        engine_name = _auto_select(languages)
        logger.info("Auto-selected OCR engine: %s (languages=%s)", engine_name, languages)

    return _build_engine(engine_name, languages, ocr_config)


# ── Engine builders ──────────────────────────────────────────────────────────

def _build_engine(engine_name: str, languages: list, ocr_config: dict) -> OCREngine:
    """Instantiate the named engine, falling back cleanly when unavailable."""
    if engine_name == "apple_vision":
        return _build_apple_vision(ocr_config)

    if engine_name == "easyocr":
        return _build_easyocr(ocr_config)

    if engine_name == "rapidocr":
        return _build_rapidocr(ocr_config)

    if engine_name == "windows_media_ocr":
        return _build_windows_media_ocr(ocr_config)

    if engine_name == "composite":
        return _build_composite(languages, ocr_config)

    logger.warning("Unknown engine %r, falling back to auto-selection", engine_name)
    fallback = _auto_select(languages)
    return _build_engine(fallback, languages, ocr_config)


def _build_apple_vision(ocr_config: dict) -> OCREngine:
    from .apple_vision_engine import AppleVisionEngine
    return AppleVisionEngine(
        recognition_level=ocr_config.get("recognition_level", "accurate"),
        use_language_correction=ocr_config.get("use_language_correction", True),
        workers=ocr_config.get("apple_vision_workers", 2),
    )


def _build_easyocr(ocr_config: dict) -> OCREngine:
    from .easyocr_engine import EasyOCREngine
    return EasyOCREngine(
        gpu=ocr_config.get("easyocr_gpu", False),
        confidence_threshold=ocr_config.get("easyocr_confidence_threshold", 0.3),
    )


def _build_rapidocr(ocr_config: dict) -> OCREngine:
    from .rapidocr_engine import RapidOCREngine
    return RapidOCREngine(
        workers=ocr_config.get("rapidocr_workers", 2),
        confidence_threshold=ocr_config.get("rapidocr_confidence_threshold", 0.5),
    )


def _build_windows_media_ocr(ocr_config: dict) -> OCREngine:
    from .windows_media_ocr_engine import WindowsMediaOcrEngine
    return WindowsMediaOcrEngine(
        workers=ocr_config.get("windows_media_ocr_workers", 2),
    )


def _build_composite(languages: list, ocr_config: dict) -> OCREngine:
    """
    Composite engine: pair the platform-native Latin engine with the best
    available Indic engine, running both concurrently.
    """
    from .composite_engine import CompositeEngine

    indic_langs = _indic_languages(languages)
    latin_langs = [l for l in languages if l not in _NON_LATIN_LANGUAGES]
    if not latin_langs:
        latin_langs = ["en"]

    latin_engine = _pick_latin_engine(ocr_config)
    indic_engine = _pick_indic_engine(ocr_config)

    logger.info(
        "Composite engine: %s (%s) + %s (%s)",
        latin_engine.name, latin_langs, indic_engine.name, indic_langs,
    )
    return CompositeEngine([
        (latin_engine, latin_langs),
        (indic_engine, indic_langs),
    ])


def _pick_latin_engine(ocr_config: dict) -> OCREngine:
    """Select the best Latin-script engine for this OS."""
    if sys.platform == "darwin":
        return _build_apple_vision(ocr_config)
    if sys.platform == "win32" and _is_importable("winocr"):
        return _build_windows_media_ocr(ocr_config)
    return _build_rapidocr(ocr_config)


def _pick_indic_engine(ocr_config: dict) -> OCREngine:
    """Prefer EasyOCR for Indic (better tuned models today); fall back to RapidOCR."""
    if _is_importable("easyocr"):
        return _build_easyocr(ocr_config)
    return _build_rapidocr(ocr_config)


# ── Auto-selection ───────────────────────────────────────────────────────────

# Languages that Apple Vision / Windows.Media.Ocr don't handle well.
# These route to EasyOCR or RapidOCR.
_NON_LATIN_LANGUAGES = {
    "hi", "mr", "ne", "ta", "te", "bn", "gu", "kn", "ml", "pa",
    "ar", "ja", "ko",
}


def _indic_languages(languages: list) -> list:
    """Return only the non-Latin languages from the list."""
    return [l for l in languages if l in _NON_LATIN_LANGUAGES]


def _auto_select(languages: list) -> str:
    """
    Automatically pick the best engine for the given languages + current OS.
    """
    has_indic = any(l in _NON_LATIN_LANGUAGES for l in languages)
    has_latin = any(l not in _NON_LATIN_LANGUAGES for l in languages)

    if has_indic and has_latin:
        return "composite"
    if has_indic:
        return "easyocr" if _is_importable("easyocr") else "rapidocr"

    # Latin-only → prefer the fastest native path per OS.
    if sys.platform == "darwin":
        return "apple_vision"
    if sys.platform == "win32":
        return "windows_media_ocr" if _is_importable("winocr") else "rapidocr"
    # Linux (and any other posix) → RapidOCR
    return "rapidocr"


def _is_importable(module_name: str) -> bool:
    """Cheap check for whether a module can be imported (no side effects)."""
    import importlib.util
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, ValueError):
        return False
