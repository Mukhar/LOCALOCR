"""
context_expander.py
~~~~~~~~~~~~~~~~~~~
Expand each anchor match into a ±N context window of neighboring frames.

Rules
-----
- **Anchors-win within a keyword folder.** If a frame is itself an anchor for
  keyword ``K``, we never emit a context entry for ``K`` over it.
- **Cross-keyword allowed.** The same frame can appear as an anchor for one
  keyword and as context (``is_context=True``) for a different keyword.
- **Only real frames.** We never fabricate a neighbor that wasn't OCR'd — the
  window is clipped against the set of frames actually present in the input.
- **Dedup.** A given ``(neighbor_frame_number, keyword)`` pair is emitted at
  most once even if multiple anchors' windows overlap.

The output list preserves the original entries unchanged and appends
synthesized context entries. Each context entry has:

- ``is_context = True``
- ``matched = True`` (so the organizer routes it into the keyword folder)
- ``matched_keywords = [context_for_keyword]``
- ``context_for_keyword``: the keyword whose window this entry serves
- ``anchor_frame_number``: the source anchor's ``frame_number`` (provenance)

Anchor entries in the returned list are also enriched with ``is_context=False``
(if not already set) for uniform downstream consumption.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


_FRAME_NAME_RE = re.compile(r"^frame_(\d{4})_(\d{2})m(\d{2})s\.\w+$")


def _resolve_frame_number(entry: dict) -> int | None:
    """Return the entry's ``frame_number``, deriving it from ``frame_name`` if needed."""
    fn = entry.get("frame_number")
    if isinstance(fn, int) and fn > 0:
        return fn
    name = entry.get("frame_name", "")
    m = _FRAME_NAME_RE.match(name)
    if m:
        return int(m.group(1))
    return None


def expand_context_windows(
    matched_results: list[dict],
    frames_before: int,
    frames_after: int,
) -> list[dict]:
    """
    Expand each anchor entry into a ±N context window.

    Parameters
    ----------
    matched_results : list[dict]
        Output of ``text_matcher.match_text``. Each entry must have
        ``frame_name``; ``frame_number`` is used when present, otherwise it is
        parsed from ``frame_name`` (``frame_NNNN_XXmYYs.png``).
    frames_before : int
        Number of frames before each anchor to include as context (``>= 0``).
    frames_after : int
        Number of frames after each anchor to include as context (``>= 0``).

    Returns
    -------
    list[dict]
        The original entries (each enriched with ``is_context=False``) followed
        by synthesized context entries. Ordering: original entries first (in
        input order), then context entries sorted by
        ``(frame_number, context_for_keyword)`` for determinism.
    """
    if frames_before < 0 or frames_after < 0:
        raise ValueError(
            f"frames_before and frames_after must be >= 0, got "
            f"before={frames_before}, after={frames_after}"
        )

    if frames_before == 0 and frames_after == 0:
        logger.info("Context expansion: window is zero, returning input unchanged")
        for entry in matched_results:
            entry.setdefault("is_context", False)
        return matched_results

    # Index entries by frame_number so we can look up neighbors in O(1).
    # We keep the FIRST entry seen per frame_number (input should be unique
    # anyway — OCR emits one entry per frame).
    by_frame: dict[int, dict] = {}
    unindexed = 0
    for entry in matched_results:
        entry.setdefault("is_context", False)
        fn = _resolve_frame_number(entry)
        if fn is None:
            unindexed += 1
            continue
        # Preserve normalized frame_number on the entry.
        entry["frame_number"] = fn
        by_frame.setdefault(fn, entry)

    if unindexed:
        logger.warning(
            "Context expansion: %d entries lacked parseable frame_number and "
            "will not participate in the window",
            unindexed,
        )

    if not by_frame:
        return matched_results

    # Track anchor keywords per frame so we can enforce anchors-win.
    anchors_by_frame: dict[int, set[str]] = {}
    for fn, entry in by_frame.items():
        if entry.get("matched") and entry.get("matched_keywords"):
            anchors_by_frame[fn] = set(entry["matched_keywords"])

    if not anchors_by_frame:
        logger.info("Context expansion: no anchors found, returning input unchanged")
        return matched_results

    # Deduplicate synthesized entries by (neighbor_frame_number, keyword).
    seen_ctx: set[tuple[int, str]] = set()
    context_entries: list[dict] = []

    for anchor_fn, keywords in anchors_by_frame.items():
        for keyword in keywords:
            for delta in range(-frames_before, frames_after + 1):
                if delta == 0:
                    continue
                neighbor_fn = anchor_fn + delta
                neighbor = by_frame.get(neighbor_fn)
                if neighbor is None:
                    continue  # neighbor frame wasn't extracted/OCR'd

                # Anchors-win: don't demote a frame that's already an anchor
                # for this same keyword to a context entry.
                if keyword in anchors_by_frame.get(neighbor_fn, ()):
                    continue

                key = (neighbor_fn, keyword)
                if key in seen_ctx:
                    continue
                seen_ctx.add(key)

                context_entries.append({
                    "frame_path": neighbor.get("frame_path", ""),
                    "frame_name": neighbor.get("frame_name", ""),
                    "timestamp": neighbor.get("timestamp", ""),
                    "frame_number": neighbor_fn,
                    "ocr_text": neighbor.get("ocr_text", ""),
                    "matched": True,
                    "matched_keywords": [keyword],
                    "is_context": True,
                    "context_for_keyword": keyword,
                    "anchor_frame_number": anchor_fn,
                })

    context_entries.sort(key=lambda e: (e["frame_number"], e["context_for_keyword"]))

    logger.info(
        "Context expansion: %d anchor frames → %d context entries "
        "(window=-%d/+%d)",
        len(anchors_by_frame), len(context_entries), frames_before, frames_after,
    )

    return list(matched_results) + context_entries
