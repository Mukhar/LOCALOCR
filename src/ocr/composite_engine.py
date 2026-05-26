"""
composite_engine.py
~~~~~~~~~~~~~~~~~~~
OCR engine that runs multiple engines in sequence and merges their results.
Used when different scripts require different engines — e.g. Apple Vision for
English and EasyOCR for Hindi — to get the best accuracy for each language.

Merge strategy
--------------
Lines from the *first* engine (Apple Vision) are always kept verbatim.
Lines from subsequent engines are only added if they contain predominantly
non-Latin script (Devanagari, Arabic, CJK, …). This prevents EasyOCR from
duplicating numbers and English words that Apple Vision already captured with
higher accuracy — EasyOCR contributes only what Apple Vision cannot read.
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from .base_engine import OCREngine

logger = logging.getLogger(__name__)

# Unicode ranges for non-Latin scripts we care about.
# A line is "non-Latin" if it contains at least one char from these ranges,
# meaning EasyOCR is the right engine for it.
_NON_LATIN_RANGES = (
    (0x0900, 0x097F),   # Devanagari (Hindi, Marathi, Nepali)
    (0x0980, 0x09FF),   # Bengali
    (0x0A00, 0x0A7F),   # Gurmukhi (Punjabi)
    (0x0A80, 0x0AFF),   # Gujarati
    (0x0B00, 0x0B7F),   # Odia
    (0x0B80, 0x0BFF),   # Tamil
    (0x0C00, 0x0C7F),   # Telugu
    (0x0C80, 0x0CFF),   # Kannada
    (0x0D00, 0x0D7F),   # Malayalam
    (0x0600, 0x06FF),   # Arabic
    (0x4E00, 0x9FFF),   # CJK Unified Ideographs
    (0x3040, 0x30FF),   # Hiragana + Katakana
    (0xAC00, 0xD7AF),   # Hangul syllables
)


def _is_non_latin(text: str) -> bool:
    """Return True if the line contains at least one non-Latin script character.

    Pure ASCII lines (digits, punctuation, English) return False — the primary
    Apple Vision engine already handles those with better accuracy.
    """
    for c in text:
        cp = ord(c)
        if any(lo <= cp <= hi for lo, hi in _NON_LATIN_RANGES):
            return True
    return False


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
        Run each sub-engine in parallel (different hardware: CPU/ANE vs MPS GPU),
        then merge results using script-aware deduplication.

        The primary engine (index 0, Apple Vision) contributes all its lines.
        Secondary engines (index 1+, EasyOCR) only contribute lines that are
        predominantly non-Latin script — they do not re-add numbers or English
        words that the primary engine already found more accurately.
        Exact-string duplicates are also removed across all engines.
        """
        # Run all engines concurrently — they use different hardware
        # (Apple Vision → CPU/ANE, EasyOCR → MPS GPU), so no resource contention.
        engine_results = {}  # {engine_idx: text_or_None}

        def _call_engine(idx_engine_langs):
            idx, engine, engine_langs = idx_engine_langs
            try:
                return idx, engine.recognize(image_path, languages=engine_langs), None
            except Exception as exc:
                return idx, "", exc

        with ThreadPoolExecutor(max_workers=len(self._engines)) as pool:
            futures = {
                pool.submit(_call_engine, (idx, eng, langs)): idx
                for idx, (eng, langs) in enumerate(self._engines)
            }
            for future in as_completed(futures):
                idx, text, exc = future.result()
                if exc:
                    engine_name = self._engines[idx][0].name
                    logger.warning("Engine %s failed on %s: %s", engine_name, image_path, exc)
                engine_results[idx] = text

        # Merge in engine order (primary first) with script-aware dedup
        all_lines = []
        seen_lines = set()

        for engine_idx, (engine, _) in enumerate(self._engines):
            is_primary = engine_idx == 0
            text = engine_results.get(engine_idx, "")

            added = skipped_dup = skipped_latin = 0
            for line in text.splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                if stripped in seen_lines:
                    skipped_dup += 1
                    continue
                # Secondary engines: skip lines that are mostly Latin/numeric
                # (Apple Vision already handles those better)
                if not is_primary and not _is_non_latin(stripped):
                    skipped_latin += 1
                    continue
                seen_lines.add(stripped)
                all_lines.append(stripped)
                added += 1

            logger.debug(
                "Engine %s: added=%d  skipped_dup=%d  skipped_latin=%d",
                engine.name, added, skipped_dup, skipped_latin,
            )

        return "\n".join(all_lines)
