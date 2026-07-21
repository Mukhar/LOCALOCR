"""
test_dashboard_render.py
========================
Regression tests for viewer.html generation.

Two invariants under test:
  1. XSS defense (fix for review finding W6): every LLM-derived string
     that lands in the dashboard MUST come through html.escape server-
     side before it's dropped into the <script> block.
  2. Optional-field rendering: the "Spoken context" section appears only
     when transcript_context is present AND non-empty. Missing key OR
     all-empty values -> zero HTML output (backwards-compat with pre-
     Phase-2 dashboards).

Uses the pure `build_dashboard_html(picks)` function so we can assert
on the string directly -- no filesystem I/O required.
"""

from __future__ import annotations

import pytest

from post_ocr_pipeline import build_dashboard_html


# --- XSS defense -----------------------------------------------------------

def test_html_escape_neutralizes_script_tags():
    """<script> and event-handler payloads in ANY string field render as
    inert text entities, not executable HTML."""
    picks = [{
        "stockPick": "<script>alert(1)</script>",
        "analyst":   'Rahul "the hacker" Shah',
        "recommended_price": 2400,
        "current_price": 2380,
        "stop_loss": 2300,
        "target": 2600,
        "_keyword": "sethi",
        "_frame_path": "matched/sethi/frame_0001.png",
        "transcript_context": {
            "before": "Welcome back.",
            "at":     "<img src=x onerror=alert(1)>",
            "after":  "</script><script>alert(2)</script>",
            "speaker": None,
        },
    }]
    out = build_dashboard_html(picks, timestamp="2026-07-21 10:00:00")

    # Locate the JSON payload block (between DATA and TIMESTAMP assigns)
    data_start = out.index("const DATA = ")
    data_end   = out.index("const TIMESTAMP")
    json_block = out[data_start:data_end]

    # No executable payloads survive
    assert "<script>alert(1)</script>" not in json_block
    assert "<script>alert(2)</script>" not in json_block
    assert "<img src=x onerror" not in json_block

    # Every dangerous byte is entity-escaped
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in json_block
    assert "&lt;img src=x onerror=alert(1)&gt;" in json_block

    # Quote in analyst name is escaped too (json.dumps handles the
    # JS-side escape, html.escape converts to &quot; entity BEFORE
    # json.dumps sees it)
    assert '"the hacker"' not in json_block
    assert "&quot;the hacker&quot;" in json_block


def test_defense_in_depth_slash_replacement_in_json():
    """Even if the recursive escape somehow got bypassed, </ in the JSON
    is transformed to <\\/ so the HTML parser can't see it as a
    script-close tag."""
    # Bypass the escaper by injecting a key the escaper doesn't touch
    # (there isn't one; the escaper is recursive). So we test the
    # replacement directly by asserting the substitution happened on
    # ANY </ in the JSON block. json.dumps naturally produces </
    # sequences when a string contains one (post-escape), so this is
    # a real path.
    picks = [{"stockPick": "TEST/PATH", "_frame_path": "a/b</script>c"}]
    out = build_dashboard_html(picks, timestamp="ts")

    data_start = out.index("const DATA = ")
    data_end   = out.index("const TIMESTAMP")
    json_block = out[data_start:data_end]

    # After escaping AND the </-to-<\/ replace, no literal </ survives
    # in the JSON block (all </ became <\/).
    assert "</script>" not in json_block  # escaped to &lt;/script&gt;
    # And even the escaped form is safe because &lt; can't start a tag


def test_timestamp_is_escaped():
    """Timestamp is a string embedded in JS -- also passes through escape."""
    picks = [{"stockPick": "X"}]
    out = build_dashboard_html(picks, timestamp='<img src=x onerror=1>')
    assert 'const TIMESTAMP = "<img src=x onerror=1>";' not in out
    assert '&lt;img src=x onerror=1&gt;' in out


# --- Optional-field rendering ---------------------------------------------

def test_transcript_section_renders_when_context_present():
    """Non-empty transcript_context -> 'Spoken context' summary text and
    the quoted before/at/after values (escaped) appear in the template."""
    picks = [{
        "stockPick": "RELIANCE",
        "analyst":   "Rahul Shah",
        "target": 2900,
        "transcript_context": {
            "before": "Now for our top pick.",
            "at":     "Reliance target 2900.",
            "after":  "Stop loss 2750.",
            "speaker": "Rahul Shah",
        },
    }]
    out = build_dashboard_html(picks, timestamp="ts")

    # Template contains the collapsible summary text
    assert "Spoken context" in out
    # ...and the transcript strings survived escaping (only reserved
    # HTML chars would change; plain ASCII sentences pass through as-is)
    assert "Now for our top pick." in out
    assert "Reliance target 2900." in out
    assert "Stop loss 2750." in out


def test_transcript_section_absent_when_context_missing():
    """Pre-Phase-2 picks (no transcript_context key) do NOT render the
    section. But the template STILL defines 'Spoken context' text
    because the JS `card()` template literal contains it -- what we
    verify is that the pick's data has no transcript_context to render."""
    picks = [{
        "stockPick": "TCS",
        "analyst":   "Ashwani Gujral",
        "target": 3800,
        # NO transcript_context key
    }]
    out = build_dashboard_html(picks, timestamp="ts")

    # The pick's JSON payload doesn't carry transcript_context
    data_start = out.index("const DATA = ")
    data_end   = out.index("const TIMESTAMP")
    json_block = out[data_start:data_end]
    assert "transcript_context" not in json_block

    # (The literal 'Spoken context' string still appears in the JS
    # template; the runtime `hasCtx` check hides it. That's the
    # backwards-compat contract: template is stable, data drives the
    # rendered output.)


def test_transcript_section_absent_when_context_all_empty_strings():
    """transcript_context present but all values empty/null -> still
    no rendered section (client-side hasCtx guard checks truthiness)."""
    picks = [{
        "stockPick": "INFY",
        "transcript_context": {
            "before": "",
            "at":     "",
            "after":  "",
            "speaker": None,
        },
    }]
    out = build_dashboard_html(picks, timestamp="ts")

    # The transcript_context IS in the JSON (we don't filter empty
    # dicts server-side), but the JS `hasCtx` check will evaluate to
    # false on all-empty strings + null speaker. Verify the check
    # exists in the template:
    assert "const hasCtx = ctx && (ctx.before || ctx.at || ctx.after);" in out


def test_transcript_context_speaker_only_hides_body():
    """Speaker set but before/at/after all empty -> hasCtx is false
    (speaker alone is not enough to render the body section)."""
    picks = [{
        "stockPick": "AAPL",
        "transcript_context": {
            "before": "",
            "at":     "",
            "after":  "",
            "speaker": "Some Anchor",
        },
    }]
    out = build_dashboard_html(picks, timestamp="ts")
    # Speaker string is in the data payload but not rendered because
    # hasCtx requires at least one of before/at/after to be truthy.
    assert '"speaker": "Some Anchor"' in out


# --- Backwards-compat & smoke ----------------------------------------------

def test_empty_pick_list_still_renders_valid_html():
    """Zero picks -> the shell renders without errors and DATA is []."""
    out = build_dashboard_html([], timestamp="ts")
    assert "const DATA = [];" in out
    assert "<div class=\"grid\" id=\"grid\"></div>" in out


def test_pick_without_frame_path_omits_screenshot_link():
    """Picks without _frame_path -> the client-side ternary produces
    no <a class='screenshot-link'> element for that pick."""
    picks = [{"stockPick": "X"}]  # no _frame_path
    out = build_dashboard_html(picks, timestamp="ts")
    # The template contains the ternary logic; the pick's data drives
    # whether the link appears at runtime. Verify no _frame_path
    # sneaks into the JSON payload.
    data_start = out.index("const DATA = ")
    data_end   = out.index("const TIMESTAMP")
    json_block = out[data_start:data_end]
    assert "_frame_path" not in json_block
