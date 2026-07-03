"""
file_organizer.py
~~~~~~~~~~~~~~~~~
Organize matched frames into categorized folders based on matched keywords.
"""

from __future__ import annotations

import logging
import re
import shutil
import unicodedata
from pathlib import Path

logger = logging.getLogger(__name__)


CONTEXT_PREFIX = "ctx_"


def organize_frames(
    matched_results: list,
    output_dir: str,
    source_prefix: str | None = None,
) -> dict:
    """
    Organize frames into matched/keyword/ folders.

    Parameters
    ----------
    matched_results : list[dict]
        Results from text_matcher (and optionally context_expander) with
        'matched', 'matched_keywords', 'frame_path', and optionally
        'is_context' (True for context-window entries).
    output_dir : str
        Base output directory.
    source_prefix : str | None
        Optional identifier prepended to every ``matched/<keyword>/`` filename
        so screenshots from different source videos are distinguishable inside
        the same output tree. Typically the video basename minus extension
        (e.g. ``"june22zeebiz"`` for ``june22zeebiz.mp4``). Sanitized to safe
        filename characters. When ``None`` or empty, filenames are unchanged.

        - Anchor becomes: ``<prefix>_frame_NNNN_XXmYYs.png``
        - Context becomes: ``ctx_<prefix>_frame_NNNN_XXmYYs.png``

        The ``all_frames/`` copy is NOT prefixed — it stays byte-identical to
        the source frame naming.

    Returns
    -------
    dict with keys: matched_count, unmatched_count, context_count, categories

    Anchors-win rule
    ----------------
    When a frame is emitted as both an anchor and a context entry for the SAME
    keyword folder (which shouldn't happen if context_expander did its job, but
    we're defensive), the anchor filename wins — we never overwrite a real
    ``frame_*.png`` with a ``ctx_frame_*.png`` of the same source, and vice
    versa. Anchors are always processed before context entries so the
    anchor-file exists first and the ``ctx_`` copy is skipped as redundant.
    """
    out_path = Path(output_dir).resolve()
    matched_dir = out_path / "matched"
    all_frames_dir = out_path / "all_frames"

    matched_dir.mkdir(parents=True, exist_ok=True)
    all_frames_dir.mkdir(parents=True, exist_ok=True)

    prefix = _sanitize_source_prefix(source_prefix)

    matched_count = 0
    context_count = 0
    unmatched_count = 0
    categories: dict[str, int] = {}

    # Split anchors from context entries and process anchors first so the
    # anchors-win rule holds even if callers pass mixed order.
    anchors: list[dict] = []
    contexts: list[dict] = []
    unmatched: list[dict] = []
    for result in matched_results:
        if not result.get("matched") or not result.get("matched_keywords"):
            unmatched.append(result)
        elif result.get("is_context"):
            contexts.append(result)
        else:
            anchors.append(result)

    # Track which (folder, source_frame_name) pairs already have an anchor
    # copied — the ctx_ variant for the same source must not overwrite it.
    # We key by the SOURCE frame name (not destination) so the anchors-win
    # rule works regardless of source_prefix.
    anchor_frames_by_folder: dict[str, set[str]] = {}

    def _copy_all_frames(frame_path: Path) -> None:
        all_dest = all_frames_dir / frame_path.name
        if not all_dest.exists():
            shutil.copy2(str(frame_path), str(all_dest))

    # ── Anchors ──────────────────────────────────────────────────────────────
    for result in anchors:
        frame_path = Path(result["frame_path"])
        if not frame_path.exists():
            logger.warning("Frame file not found: %s", frame_path)
            continue

        _copy_all_frames(frame_path)
        matched_count += 1

        for keyword in result["matched_keywords"]:
            folder_name = _sanitize_folder_name(keyword)
            keyword_dir = matched_dir / folder_name
            keyword_dir.mkdir(parents=True, exist_ok=True)

            anchor_name = _anchor_filename(frame_path.name, prefix)
            dest = keyword_dir / anchor_name
            if not dest.exists():
                shutil.copy2(str(frame_path), str(dest))

            anchor_frames_by_folder.setdefault(folder_name, set()).add(frame_path.name)
            categories[folder_name] = categories.get(folder_name, 0) + 1

            logger.debug(
                "Organized anchor %s → matched/%s/%s",
                frame_path.name, folder_name, anchor_name,
            )

    # ── Context entries ──────────────────────────────────────────────────────
    for result in contexts:
        frame_path = Path(result["frame_path"])
        if not frame_path.exists():
            logger.warning("Context frame file not found: %s", frame_path)
            continue

        _copy_all_frames(frame_path)

        # Context entries always carry exactly one keyword (the window owner),
        # but iterate defensively.
        for keyword in result["matched_keywords"]:
            folder_name = _sanitize_folder_name(keyword)

            # Anchors-win: if this exact source frame is already an anchor in
            # this folder, skip emitting the ctx_ copy.
            if frame_path.name in anchor_frames_by_folder.get(folder_name, ()):
                logger.debug(
                    "Skipping ctx_ for %s in matched/%s/: already an anchor",
                    frame_path.name, folder_name,
                )
                continue

            keyword_dir = matched_dir / folder_name
            keyword_dir.mkdir(parents=True, exist_ok=True)

            ctx_name = _context_filename(frame_path.name, prefix)
            dest = keyword_dir / ctx_name
            if not dest.exists():
                shutil.copy2(str(frame_path), str(dest))

            context_count += 1
            categories[folder_name] = categories.get(folder_name, 0) + 1

            logger.debug(
                "Organized context %s → matched/%s/%s",
                frame_path.name, folder_name, ctx_name,
            )

    # ── Unmatched (all_frames copy only) ─────────────────────────────────────
    for result in unmatched:
        frame_path = Path(result["frame_path"])
        if not frame_path.exists():
            logger.warning("Frame file not found: %s", frame_path)
            continue
        _copy_all_frames(frame_path)
        unmatched_count += 1

    logger.info(
        "Organization complete: %d anchor(s), %d context, %d unmatched, %d categories%s",
        matched_count, context_count, unmatched_count, len(categories),
        f" (source_prefix={prefix!r})" if prefix else "",
    )

    return {
        "matched_count": matched_count,
        "context_count": context_count,
        "unmatched_count": unmatched_count,
        "categories": categories,
    }


def _anchor_filename(source_name: str, prefix: str) -> str:
    """Prepend the sanitized source prefix to an anchor filename (idempotent)."""
    if not prefix:
        return source_name
    if source_name.startswith(f"{prefix}_"):
        return source_name
    return f"{prefix}_{source_name}"


def _context_filename(source_name: str, prefix: str = "") -> str:
    """Prefix a frame filename with ``ctx_[<prefix>_]`` (idempotent)."""
    base = source_name
    if base.startswith(CONTEXT_PREFIX):
        base = base[len(CONTEXT_PREFIX):]
    if prefix and not base.startswith(f"{prefix}_"):
        base = f"{prefix}_{base}"
    return f"{CONTEXT_PREFIX}{base}"


def _sanitize_source_prefix(prefix: str | None) -> str:
    """
    Normalize an arbitrary string (typically a video basename) into a safe
    filename component: keep alphanumerics, dashes, and underscores; collapse
    everything else to a single underscore; trim leading/trailing underscores.
    Returns "" for None/empty/whitespace-only input.
    """
    if not prefix:
        return ""
    cleaned = unicodedata.normalize("NFC", prefix).strip()
    if not cleaned:
        return ""
    safe = "".join(
        c if (c.isalnum() or c in "-_") else "_"
        for c in cleaned
    )
    safe = re.sub(r"_+", "_", safe).strip("_")
    return safe


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
