"""
file_organizer.py
~~~~~~~~~~~~~~~~~
Organize matched frames into categorized folders based on matched keywords.
"""

import logging
import re
import shutil
import unicodedata
from pathlib import Path

logger = logging.getLogger(__name__)


def organize_frames(matched_results: list, output_dir: str) -> dict:
    """
    Organize frames into matched/keyword/ folders.

    Parameters
    ----------
    matched_results : list[dict]
        Results from text_matcher with 'matched', 'matched_keywords', 'frame_path'.
    output_dir : str
        Base output directory.

    Returns
    -------
    dict with keys: matched_count, unmatched_count, categories
    """
    out_path = Path(output_dir).resolve()
    matched_dir = out_path / "matched"
    all_frames_dir = out_path / "all_frames"

    matched_dir.mkdir(parents=True, exist_ok=True)
    all_frames_dir.mkdir(parents=True, exist_ok=True)

    matched_count = 0
    unmatched_count = 0
    categories = {}

    for result in matched_results:
        frame_path = Path(result["frame_path"])

        if not frame_path.exists():
            logger.warning("Frame file not found: %s", frame_path)
            continue

        # Copy to all_frames
        all_dest = all_frames_dir / frame_path.name
        if not all_dest.exists():
            shutil.copy2(str(frame_path), str(all_dest))

        if result.get("matched") and result.get("matched_keywords"):
            matched_count += 1

            for keyword in result["matched_keywords"]:
                # Sanitize keyword for folder name
                folder_name = _sanitize_folder_name(keyword)
                keyword_dir = matched_dir / folder_name
                keyword_dir.mkdir(parents=True, exist_ok=True)

                dest = keyword_dir / frame_path.name
                if not dest.exists():
                    shutil.copy2(str(frame_path), str(dest))

                categories.setdefault(folder_name, 0)
                categories[folder_name] += 1

                logger.debug(
                    "Organized %s → matched/%s/",
                    frame_path.name, folder_name
                )
        else:
            unmatched_count += 1

    logger.info(
        "Organization complete: %d matched, %d unmatched, %d categories",
        matched_count, unmatched_count, len(categories)
    )

    return {
        "matched_count": matched_count,
        "unmatched_count": unmatched_count,
        "categories": categories,
    }


def _sanitize_folder_name(keyword: str) -> str:
    """Convert keyword to a safe folder name."""
    # Preserve Unicode letters/marks (e.g., Devanagari matras), while
    # normalizing separators and punctuation to underscores.
    safe = unicodedata.normalize("NFC", keyword).strip().lower()
    safe = "".join(
        c if (c.isalnum() or unicodedata.category(c).startswith("M") or c in "-_")
        else "_"
        for c in safe
    )
    safe = re.sub(r"_+", "_", safe).strip("_")
    return safe or "uncategorized"
