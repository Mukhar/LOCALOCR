"""
tests/test_transcript/test_correlator.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Unit tests for the pure-function correlator. **No mocks needed** --
this is why we kept it side-effect-free.
"""

from __future__ import annotations

import pytest

from src.transcript import (
    Segment,
    correlate,
    enrich_ocr_results,
    frame_timestamp_seconds,
)


# ---------------------------------------------------------------------------
# frame_timestamp_seconds
# ---------------------------------------------------------------------------
def test_frame_timestamp_seconds_parses_XXmYYs():
    assert frame_timestamp_seconds({"timestamp": "05m30s"}) == 330
    assert frame_timestamp_seconds({"timestamp": "00m00s"}) == 0
    assert frame_timestamp_seconds({"timestamp": "10m45s"}) == 645


def test_frame_timestamp_seconds_bad_input_returns_zero():
    """Malformed timestamps return 0.0 rather than raising -- one bad
    frame shouldn't crash the whole enrichment pass."""
    assert frame_timestamp_seconds({"timestamp": "garbage"}) == 0.0
    assert frame_timestamp_seconds({"timestamp": ""}) == 0.0
    assert frame_timestamp_seconds({}) == 0.0
    # Non-XXmYYs formats (extractor changed convention?) also -> 0
    assert frame_timestamp_seconds({"timestamp": "5:30"}) == 0.0
    assert frame_timestamp_seconds({"timestamp": "5m30s"}) == 0.0  # not zero-padded


# ---------------------------------------------------------------------------
# correlate
# ---------------------------------------------------------------------------
def test_correlate_empty_segments():
    assert correlate(10.0, [], 5.0) == {
        "before": "", "at": "", "after": "", "speaker": None,
    }


def test_correlate_all_before():
    """All segments end < frame_ts -> only 'before' populated."""
    segs = [
        Segment(0, 5, "one"),
        Segment(5, 10, "two"),
        Segment(10, 30, "three"),  # ends at 30, which is < 100
    ]
    out = correlate(100.0, segs, 200.0)  # huge window includes all
    # Sort-insensitive check: all three should be in 'before'
    assert "one" in out["before"]
    assert "two" in out["before"]
    assert "three" in out["before"]
    assert out["at"] == ""
    assert out["after"] == ""


def test_correlate_all_after():
    """All segments start > frame_ts -> only 'after' populated."""
    segs = [
        Segment(10, 15, "first"),
        Segment(20, 25, "second"),
    ]
    out = correlate(5.0, segs, 100.0)
    assert out["before"] == ""
    assert out["at"] == ""
    assert "first" in out["after"]
    assert "second" in out["after"]


def test_correlate_containing_segment_goes_to_at():
    """A segment (start=5, end=15) with frame_ts=10 lives in 'at'."""
    segs = [Segment(5, 15, "containing")]
    out = correlate(10.0, segs, 5.0)
    assert out["at"] == "containing"
    assert out["before"] == ""
    assert out["after"] == ""


def test_correlate_boundary_cases():
    """Segments exactly at the frame_ts boundary land in 'at'."""
    # segment where start == frame_ts
    out = correlate(10.0, [Segment(10, 15, "start=ts")], 5.0)
    assert out["at"] == "start=ts"

    # segment where end == frame_ts (start=5 <= ts=10 <= end=10)
    out = correlate(10.0, [Segment(5, 10, "end=ts")], 5.0)
    assert out["at"] == "end=ts"


def test_correlate_window_filters_out_of_range():
    """Segments outside [frame_ts-window, frame_ts+window] are excluded."""
    segs = [
        Segment(0, 1, "way before"),      # ends at 1, window starts at 95 -> excluded
        Segment(99, 101, "in window"),    # overlaps window
        Segment(500, 510, "way after"),   # starts at 500, window ends at 105 -> excluded
    ]
    out = correlate(100.0, segs, 5.0)  # window [95, 105]
    combined = out["before"] + out["at"] + out["after"]
    assert "in window" in combined
    assert "way before" not in combined
    assert "way after" not in combined


def test_correlate_speaker_majority():
    """When speakers are set, majority wins."""
    segs = [
        Segment(0, 5, "a", speaker="Alice"),
        Segment(5, 10, "b", speaker="Bob"),
        Segment(10, 15, "c", speaker="Alice"),  # Alice appears twice
    ]
    out = correlate(7.0, segs, 100.0)
    assert out["speaker"] == "Alice"


def test_correlate_speaker_none_when_all_null():
    """No speakers in window -> speaker is None (not '' or 'unknown')."""
    segs = [Segment(0, 5, "hi"), Segment(5, 10, "bye")]
    out = correlate(5.0, segs, 100.0)
    assert out["speaker"] is None


def test_correlate_uses_fixture_style_data():
    """End-to-end sanity check with the realistic fixture-shaped data."""
    segs = [
        Segment(0.0, 3.24, "Welcome back to Zee Business."),
        Segment(3.24, 8.5, "Our top pick today is Reliance Industries at target 2900."),
        Segment(8.5, 12.0, "Stop loss at 2750 for aggressive traders."),
    ]
    # Frame at 5s, window ± 5s -> all three segments overlap
    out = correlate(5.0, segs, 5.0)
    assert "Welcome back" in out["before"]           # ends at 3.24 < 5
    assert "Reliance" in out["at"]                    # 3.24 <= 5 <= 8.5
    assert "Stop loss" in out["after"]                # starts at 8.5 > 5


# ---------------------------------------------------------------------------
# enrich_ocr_results
# ---------------------------------------------------------------------------
def _sample_results():
    return [
        {"matched": True, "timestamp": "00m05s", "keyword": "reliance"},
        {"matched": False, "timestamp": "00m10s"},
        {"matched": True, "timestamp": "00m09s", "keyword": "target"},
    ]


def _sample_segments():
    return [
        Segment(0.0, 3.24, "Welcome back to Zee Business."),
        Segment(3.24, 8.5, "Our top pick today is Reliance target 2900."),
        Segment(8.5, 12.0, "Stop loss at 2750."),
    ]


def test_enrich_ocr_results_only_touches_matched():
    inp = _sample_results()
    out = enrich_ocr_results(inp, _sample_segments(), 5.0)

    # Matched results get the new field
    assert "transcript_context" in out[0]
    assert "transcript_context" in out[2]

    # Unmatched is passed through by IDENTITY (not copied)
    assert out[1] is inp[1]
    assert "transcript_context" not in out[1]


def test_enrich_ocr_results_matched_dicts_are_new_objects():
    """Matched entries must be new dicts, not mutated in place -- the
    caller's list must be untouched (defensive copy contract)."""
    inp = _sample_results()
    out = enrich_ocr_results(inp, _sample_segments(), 5.0)

    assert out[0] is not inp[0], "matched entry must be a new dict"
    assert "transcript_context" not in inp[0], "input list must not be mutated"


def test_enrich_empty_segments_returns_shallow_copy():
    """No transcript -> return input contents unchanged (shallow copy)."""
    inp = _sample_results()
    out = enrich_ocr_results(inp, [], 5.0)
    assert out == inp
    assert out is not inp  # different list container...
    for i in range(len(inp)):
        assert out[i] is inp[i]  # ...but same dict elements (shallow)


def test_enrich_transcript_context_shape():
    """The added transcript_context dict has all four expected keys."""
    inp = [{"matched": True, "timestamp": "00m05s"}]
    out = enrich_ocr_results(inp, _sample_segments(), 5.0)
    ctx = out[0]["transcript_context"]
    assert set(ctx.keys()) == {"before", "at", "after", "speaker"}
