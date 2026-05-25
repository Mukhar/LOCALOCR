"""
text_matcher.py
~~~~~~~~~~~~~~~
Match OCR-extracted text against configured keywords/patterns.
Supports: contains (case-insensitive), exact match, and regex.
"""

import logging
import re

logger = logging.getLogger(__name__)


def match_text(ocr_results: list, keywords: list, match_mode: str = "contains") -> list:
    """
    Match OCR text against keywords.

    Parameters
    ----------
    ocr_results : list[dict]
        Each dict must have 'ocr_text' key.
    keywords : list[str]
        Keywords or patterns to match against.
    match_mode : str
        One of: 'contains', 'exact', 'regex'

    Returns
    -------
    list[dict]
        Same dicts enriched with: matched (bool), matched_keywords (list)
    """
    logger.info(
        "Text matching started | %d frames | %d keywords | mode=%s",
        len(ocr_results), len(keywords), match_mode
    )

    if not keywords:
        logger.warning("No keywords configured — no matches will be found")
        for result in ocr_results:
            result["matched"] = False
            result["matched_keywords"] = []
        return ocr_results

    match_count = 0

    for result in ocr_results:
        ocr_text = result.get("ocr_text", "").strip()
        matched_keywords = []

        for keyword in keywords:
            if _is_match(ocr_text, keyword, match_mode):
                matched_keywords.append(keyword)

        result["matched"] = len(matched_keywords) > 0
        result["matched_keywords"] = matched_keywords

        if result["matched"]:
            match_count += 1
            logger.debug(
                "[MATCH] %s matched keywords: %s",
                result.get("frame_name", "unknown"),
                matched_keywords,
            )

    logger.info(
        "Matching complete: %d/%d frames matched",
        match_count, len(ocr_results)
    )
    return ocr_results


def _is_match(text: str, keyword: str, mode: str) -> bool:
    """Check if text matches the keyword based on mode."""
    if not text:
        return False

    if mode == "contains":
        return keyword.lower() in text.lower()

    elif mode == "exact":
        # Case-insensitive exact match on any line
        lines = [line.strip().lower() for line in text.split("\n")]
        return keyword.lower() in lines

    elif mode == "regex":
        try:
            return bool(re.search(keyword, text, re.IGNORECASE))
        except re.error as exc:
            logger.warning("Invalid regex pattern %r: %s", keyword, exc)
            return False

    else:
        logger.warning("Unknown match mode %r, falling back to 'contains'", mode)
        return keyword.lower() in text.lower()
