"""
composite_engine.py
~~~~~~~~~~~~~~~~~~~
OCR engine that runs multiple engines in sequence and merges their results.
Used when different scripts require different engines — e.g. Apple Vision for
English and EasyOCR for Hindi — to get the best accuracy for each language.
"""

import logging

from .base_engine import OCREngine

logger = logging.getLogger(__name__)


class CompositeEngine(OCREngine):
    """
    Runs two engines on the same image with their respective languages,
    then merges the recognized text.

    Example
    -------
    engine = CompositeEngine(
        engines=[
            (AppleVisionEngine(), ["en"]),
            (EasyOCREngine(),     ["hi"]),
        ]
    )
    text = engine.recognize(image_path, languages=["hi", "en"])
    # → merged output from both engines
    """

    def __init__(self, engines: list):
        """
        Parameters
        ----------
        engines : list[tuple[OCREngine, list[str]]]
            Each entry is (engine_instance, language_list_for_that_engine).
        """
        self._engines = engines  # [(engine, langs), ...]

    @property
    def name(self) -> str:
        names = "+".join(e.name for e, _ in self._engines)
        return f"composite({names})"

    def supported_languages(self) -> list:
        seen = set()
        result = []
        for engine, _ in self._engines:
            for lang in engine.supported_languages():
                if lang not in seen:
                    seen.add(lang)
                    result.append(lang)
        return result

    def recognize(self, image_path: str, languages: list = None) -> str:
        """
        Run each sub-engine with its assigned languages and merge results.
        Duplicate lines (same text from both engines) are deduplicated.
        """
        all_lines = []
        seen_lines = set()

        for engine, engine_langs in self._engines:
            try:
                text = engine.recognize(image_path, languages=engine_langs)
            except Exception as exc:
                logger.warning(
                    "Engine %s failed on %s: %s", engine.name, image_path, exc
                )
                continue

            for line in text.splitlines():
                stripped = line.strip()
                if stripped and stripped not in seen_lines:
                    seen_lines.add(stripped)
                    all_lines.append(stripped)

        return "\n".join(all_lines)
