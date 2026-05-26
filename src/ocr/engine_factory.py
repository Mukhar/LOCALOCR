"""
engine_factory.py
~~~~~~~~~~~~~~~~~
Factory for selecting the appropriate OCR engine based on configuration.

Selection logic:
- If ocr_engine is explicitly set → use that engine
- If languages contain only 'en' → Apple Vision (fastest, native)
- If languages contain only Indic/non-Latin → EasyOCR
- If languages contain BOTH 'en' AND Indic → Composite (Apple Vision for en, EasyOCR for Indic)
- Default: Apple Vision
"""

import logging

from .base_engine import OCREngine

logger = logging.getLogger(__name__)


def get_engine(config: dict = None) -> OCREngine:
    """
    Create and return the appropriate OCR engine based on config.

    Parameters
    ----------
    config : dict, optional
        Pipeline config. Relevant keys:
        - ocr_engine: str ("auto", "apple_vision", "easyocr")
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

    if engine_name == "apple_vision":
        from .apple_vision_engine import AppleVisionEngine
        return AppleVisionEngine(
            recognition_level=ocr_config.get("recognition_level", "accurate"),
            use_language_correction=ocr_config.get("use_language_correction", True),
            workers=ocr_config.get("apple_vision_workers", 2),
        )

    elif engine_name == "easyocr":
        from .easyocr_engine import EasyOCREngine
        gpu = ocr_config.get("easyocr_gpu", False)
        confidence = ocr_config.get("easyocr_confidence_threshold", 0.3)
        return EasyOCREngine(gpu=gpu, confidence_threshold=confidence)

    elif engine_name == "composite":
        from .apple_vision_engine import AppleVisionEngine
        from .easyocr_engine import EasyOCREngine
        from .composite_engine import CompositeEngine
        gpu = ocr_config.get("easyocr_gpu", False)
        # Partition languages: Latin scripts → Apple Vision, Indic/non-Latin → EasyOCR
        indic_langs = _indic_languages(languages)
        latin_langs = [l for l in languages if l not in _EASYOCR_LANGUAGES]
        if not latin_langs:
            latin_langs = ["en"]
        confidence = ocr_config.get("easyocr_confidence_threshold", 0.3)
        return CompositeEngine([
            (AppleVisionEngine(
                recognition_level=ocr_config.get("recognition_level", "accurate"),
                use_language_correction=ocr_config.get("use_language_correction", True),
            ), latin_langs),
            (EasyOCREngine(gpu=gpu, confidence_threshold=confidence), indic_langs),
        ])

    else:
        logger.warning("Unknown engine %r, falling back to apple_vision", engine_name)
        from .apple_vision_engine import AppleVisionEngine
        return AppleVisionEngine()


# Languages that require EasyOCR (Indic + non-Latin scripts Apple Vision doesn't support)
_EASYOCR_LANGUAGES = {"hi", "mr", "ne", "ta", "te", "bn", "gu", "kn", "ml", "pa", "ar", "ja", "ko"}


def _indic_languages(languages: list) -> list:
    """Return only the Indic/EasyOCR languages from the list."""
    return [l for l in languages if l in _EASYOCR_LANGUAGES]


def _auto_select(languages: list) -> str:
    """
    Automatically select the best engine for the given languages.

    Rules:
    - English only (no Indic) → Apple Vision (native, fast)
    - Indic only (no English)  → EasyOCR
    - Both English + Indic     → Composite (Apple Vision for en, EasyOCR for Indic)
    """
    has_indic = any(l in _EASYOCR_LANGUAGES for l in languages)
    has_latin = any(l not in _EASYOCR_LANGUAGES for l in languages)

    if has_indic and has_latin:
        return "composite"
    elif has_indic:
        return "easyocr"
    else:
        return "apple_vision"
