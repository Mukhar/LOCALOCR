"""
correlator.py
~~~~~~~~~~~~~
Pure functions that map frame timestamps to surrounding transcript
context. **No I/O, no side effects** -- so unit tests need zero mocks.

The pipeline calls :func:`enrich_ocr_results` after whisper produces
its :class:`Segment` list; each MATCHED OCR result gains a
``transcript_context`` field of the form::

    {
      "before":  "text of segments ending before the frame",
      "at":      "text of segments containing the frame timestamp",
      "after":   "text of segments starting after the frame",
      "speaker": "majority speaker across the window, or None"
    }

Unmatched results are returned untouched -- transcript context is only
useful for the frames we're actually going to show to the user / LLM.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, List, Optional

from .whisper_transcriber import Segment

# Frame timestamp format from the extractor: XXmYYs (e.g. "05m30s").
# See src/extractor/frame_extractor.py naming convention.
_TS_RE = re.compile(r"^(\d{2})m(\d{2})s$")


def frame_timestamp_seconds(frame: Dict[str, Any]) -> float:
    """Parse the ``timestamp`` field of a frame dict to seconds.

    Malformed / missing timestamps deliberately return ``0.0`` rather than
    raising -- the correlator's callers already skip unmatched frames,
    and losing a single timestamp shouldn't crash the whole enrichment
    pass. A logged warning would be noise here; we surface the problem
    via a zero-context ``transcript_context`` dict on that one frame.
    """
    ts = str(frame.get("timestamp", "")).strip()
    m = _TS_RE.match(ts)
    if not m:
        return 0.0
    return int(m.group(1)) * 60 + int(m.group(2))


def _empty_context() -> Dict[str, Optional[str]]:
    return {"before": "", "at": "", "after": "", "speaker": None}


def correlate(
    frame_ts: float,
    segments: List[Segment],
    window_seconds: float,
) -> Dict[str, Optional[str]]:
    """Return the transcript window around ``frame_ts``.

    Parameters
    ----------
    frame_ts
        Frame timestamp in seconds (as produced by
        :func:`frame_timestamp_seconds`).
    segments
        Whisper output, ideally sorted by start time (transcribe()
        guarantees this).
    window_seconds
        Half-width of the window. Segments whose ``[start, end]``
        overlaps ``[frame_ts - window, frame_ts + window]`` are
        included. Configurable via ``transcript_config.context_window_seconds``.

    Returns
    -------
    dict
        ``{"before": str, "at": str, "after": str, "speaker": str|None}``.

    Notes
    -----
    * A segment straddling ``frame_ts`` (start <= frame_ts <= end) goes
      into ``at`` regardless of overlap with the window boundary.
    * ``speaker`` is the ``Counter.most_common(1)`` of non-null speakers
      in the window. whisper.cpp doesn't emit speakers today, so this
      is None-in-None-out until diarization ships.
    """
    if not segments:
        return _empty_context()

    window_start = frame_ts - window_seconds
    window_end = frame_ts + window_seconds

    in_window = [
        s for s in segments
        if s.end >= window_start and s.start <= window_end
    ]

    before: List[str] = []
    at: List[str] = []
    after: List[str] = []
    for s in in_window:
        if s.end < frame_ts:
            before.append(s.text)
        elif s.start > frame_ts:
            after.append(s.text)
        else:
            # start <= frame_ts <= end -- segment contains the frame
            at.append(s.text)

    speakers = [s.speaker for s in in_window if s.speaker]
    speaker = Counter(speakers).most_common(1)[0][0] if speakers else None

    return {
        "before": " ".join(before).strip(),
        "at": " ".join(at).strip(),
        "after": " ".join(after).strip(),
        "speaker": speaker,
    }


def enrich_ocr_results(
    results: List[Dict[str, Any]],
    segments: List[Segment],
    window_seconds: float,
) -> List[Dict[str, Any]]:
    """Return a new list where each MATCHED result carries a
    ``transcript_context`` dict. Unmatched results are returned by
    identity reference -- no wasted copy, and the caller can spot-check
    with ``assert out[i] is inp[i]`` in tests.

    When ``segments`` is empty (e.g. audio-less video, whisper skipped),
    returns a shallow copy of ``results`` unchanged.
    """
    if not segments:
        # Shallow copy: preserves identity of each element so callers
        # can still ``is``-compare individual dicts.
        return list(results)

    out: List[Dict[str, Any]] = []
    for r in results:
        if not r.get("matched"):
            out.append(r)  # identity-preserving pass-through
            continue
        enriched = dict(r)
        enriched["transcript_context"] = correlate(
            frame_timestamp_seconds(r),
            segments,
            window_seconds,
        )
        out.append(enriched)
    return out
