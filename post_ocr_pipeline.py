"""
post_ocr_pipeline.py
~~~~~~~~~~~~~~~~~~~~
Production-ready post-OCR pipeline with three phases:

    Phase 1 — Vision Extraction:
        Iterate matched/ sub-folders → send each image to Ollama vision
        model → collect structured JSON picks per frame.

    Phase 2 — Deduplication:
        Send the aggregated Phase-1 JSON back to Ollama → merge partial
        matches, remove exact duplicates → return a clean, consolidated
        JSON array.

    Phase 3 — HTML Dashboard (parallel thread):
        Build a standalone viewer.html from the deduplicated JSON.
        Runs in a daemon thread so it never blocks the main pipeline.

Interrupt:
    A threading.Event (_stop_event) is set on KeyboardInterrupt.
    Every inner loop checks it before each Ollama call so the process
    exits gracefully without corrupting the in-progress JSON.

Entry point:
    run_post_ocr_pipeline(config)   ← plug into any existing pipeline
    or
    python post_ocr_pipeline.py     ← standalone CLI

CLI usage:
    python post_ocr_pipeline.py
    python post_ocr_pipeline.py --matched-dir ./output/matched
    python post_ocr_pipeline.py --output-dir  ./output
    python post_ocr_pipeline.py --model gemma4 --url http://localhost:11434
    python post_ocr_pipeline.py --timeout 120
    python post_ocr_pipeline.py --skip-dedup   # skip Phase 2
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import logging
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

import requests

# ── logging ───────────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)

# ── constants / defaults ──────────────────────────────────────────────────────
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL      = "gemma4"
DEFAULT_TIMEOUT    = 90          # seconds per Ollama call
IMAGE_EXTS         = {".png", ".jpg", ".jpeg"}
REQUIRED_FIELDS    = ["analyst", "stockPick", "recommended_price",
                      "current_price", "stop_loss", "target"]

EXTRACTION_PROMPT = (
    "You are analyzing a financial news TV screenshot. "
    "Focus ONLY on the PRIMARY stock pick in the CENTER or MAIN area of the screen — "
    "the largest highlighted card, overlay, or banner. "
    "Ignore scrolling tickers, edge banners, and secondary content. "
    "Return ONLY a valid JSON array — no markdown, no explanation — "
    "with exactly one object containing these six fields:\n"
    '  "analyst"           — analyst / expert name visible on screen\n'
    '  "stockPick"         — stock name or NSE/BSE ticker\n'
    '  "recommended_price" — buy / entry price shown\n'
    '  "current_price"     — current market price shown\n'
    '  "stop_loss"         — stop-loss level shown\n'
    '  "target"            — target price shown\n'
    "Set any field that is NOT visible to null.\n"
    'Example output: [{"analyst":"Rahul Shah","stockPick":"RELIANCE",'
    '"recommended_price":2400,"current_price":2380,"stop_loss":2300,"target":2600}]'
)

DEDUP_PROMPT_TEMPLATE = (
    "Below is a JSON array of stock picks extracted from multiple screenshots of a "
    "financial news broadcast. Many entries may be duplicates or partial matches of "
    "the same recommendation shown across different frames.\n\n"
    "Your task:\n"
    "1. Merge entries that refer to the same stock pick by the same analyst.\n"
    "2. When merging, prefer non-null values over null ones.\n"
    "3. Remove exact duplicates.\n"
    "4. PRESERVE any 'transcript_context' field verbatim from the input on the merged "
    "record - do not summarize, rewrite, or drop it.\n"
    "5. Return ONLY a valid JSON array of unique, fully merged picks - "
    "no markdown, no explanation.\n\n"
    "Input data:\n{data}"
)


# ── shared interrupt event ────────────────────────────────────────────────────
_stop_event = threading.Event()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _encode_image(path: Path) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _strip_fence(text: str) -> str:
    """Strip ```json … ``` or ``` … ``` markdown fences."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        inner = lines[1:] if len(lines) > 1 else lines
        if inner and inner[-1].strip() == "```":
            inner = inner[:-1]
        text = "\n".join(inner).strip()
    return text


def _ollama_post(
    url: str,
    model: str,
    prompt: str,
    timeout: int,
    image_b64: str | None = None,
) -> tuple[str | None, str | None]:
    """
    Single Ollama /api/generate call.

    Returns (raw_text, error_message). One of them is always None.
    """
    payload: dict[str, Any] = {"model": model, "prompt": prompt, "stream": False}
    if image_b64:
        payload["images"] = [image_b64]

    try:
        resp = requests.post(
            f"{url.rstrip('/')}/api/generate",
            json=payload,
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json().get("response", ""), None
    except requests.exceptions.ConnectionError:
        return None, f"Cannot connect to Ollama at {url}"
    except requests.exceptions.HTTPError as exc:
        body = exc.response.text[:300] if exc.response is not None else ""
        return None, f"HTTP {exc.response.status_code}: {body}"
    except requests.exceptions.Timeout:
        return None, f"Request timed out after {timeout}s"
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


def _parse_json_response(raw: str) -> tuple[Any, str | None]:
    """
    Try to parse the LLM response as JSON.

    Returns (parsed_object, error_message). On failure, parsed_object is None.
    """
    cleaned = _strip_fence(raw)
    try:
        return json.loads(cleaned), None
    except json.JSONDecodeError as exc:
        return None, f"JSON parse error: {exc}"


def _missing_fields(record: list | None) -> list[str]:
    if not record or not isinstance(record, list) or not record[0]:
        return list(REQUIRED_FIELDS)
    item = record[0]
    return [f for f in REQUIRED_FIELDS if item.get(f) is None]


def _build_transcript_addendum(ctx: dict | None) -> str:
    """Build a natural-language prompt suffix from a transcript_context dict.

    Returns "" when ctx is missing / empty / non-dict. The addendum is
    deliberately formatted as short quoted snippets so the vision model
    treats it as corroborating dialogue rather than commands to obey.
    """
    if not ctx or not isinstance(ctx, dict):
        return ""
    parts: list[str] = []
    if ctx.get("before"):
        parts.append(f'Before this moment: "{ctx["before"]}"')
    if ctx.get("at"):
        parts.append(f'At this moment: "{ctx["at"]}"')
    if ctx.get("after"):
        parts.append(f'After this moment: "{ctx["after"]}"')
    if ctx.get("speaker"):
        parts.append(f'Attributed to: {ctx["speaker"]}')
    if not parts:
        return ""
    return "\n\nSpoken context (±8s around this frame):\n" + "\n".join(parts)


def _load_transcript_contexts(ocr_results_path: Path) -> dict:
    """Build a {frame_name: transcript_context} lookup from ocr_results.json.

    Returns {} if the file is missing or malformed - post-OCR always
    degrades gracefully to no-context mode.
    """
    if not ocr_results_path.is_file():
        logger.info(
            "No ocr_results.json at %s; post-OCR will run without transcript context",
            ocr_results_path,
        )
        return {}
    try:
        with open(ocr_results_path, encoding="utf-8") as f:
            entries = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "Could not read %s (%s); running without transcript context",
            ocr_results_path, exc,
        )
        return {}
    lookup: dict = {}
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        name = entry.get("frame")
        ctx = entry.get("transcript_context")
        if name and isinstance(ctx, dict):
            lookup[name] = ctx
    if lookup:
        logger.info(
            "Loaded transcript context for %d frames from %s",
            len(lookup), ocr_results_path,
        )
    return lookup


def _attach_transcript_context(analysis: Any, ctx: dict | None) -> Any:
    """Attach transcript_context to each item in the analysis list (in-place).

    No-op when ctx is empty or analysis isn't a list. Called AFTER
    _apply_folder_analyst so folder-analyst override + transcript_context
    both land on the final record.
    """
    if not ctx or not isinstance(analysis, list):
        return analysis
    for item in analysis:
        if isinstance(item, dict):
            item["transcript_context"] = ctx
    return analysis


def _apply_folder_analyst(analysis: Any, folder_name: str) -> Any:
    """Override analyst with folder name for list/dict analysis payloads."""
    if analysis is None:
        return None

    if isinstance(analysis, list):
        normalized = []
        for item in analysis:
            if isinstance(item, dict):
                updated = dict(item)
                updated["analyst"] = folder_name
                normalized.append(updated)
            else:
                normalized.append(item)
        return normalized

    if isinstance(analysis, dict):
        updated = dict(analysis)
        updated["analyst"] = folder_name
        return updated

    return analysis


def _collect_images(matched_dir: Path) -> list[tuple[str, Path]]:
    """
    Walk matched/<keyword>/ sub-folders and return [(keyword, image_path), ...].
    """
    images: list[tuple[str, Path]] = []
    for folder in sorted(matched_dir.iterdir()):
        if not folder.is_dir():
            continue
        for img in sorted(folder.iterdir()):
            if img.suffix.lower() in IMAGE_EXTS:
                images.append((folder.name, img))
    return images


# ─────────────────────────────────────────────────────────────────────────────
# Phase 1 — Vision Extraction
# ─────────────────────────────────────────────────────────────────────────────

def _extract_single(
    keyword: str,
    image_path: Path,
    url: str,
    model: str,
    timeout: int,
    transcript_context: dict | None = None,
) -> dict:
    """
    Two-pass extraction for one image.

    Pass 1: initial extraction. If ``transcript_context`` is provided,
            the spoken-context addendum is appended to the vision prompt
            so the model has corroborating dialogue.
    Pass 2: retry only if required fields are missing, feeding the partial
            result back to the model with the same image.
    """
    image_b64 = _encode_image(image_path)
    prompt = EXTRACTION_PROMPT + _build_transcript_addendum(transcript_context)

    # Pass 1
    raw1, err = _ollama_post(url, model, prompt, timeout, image_b64)
    if err:
        logger.error("  [P1] %s/%s — %s", keyword, image_path.name, err)
        return _make_result(keyword, image_path, None, None, None, err, retried=False)

    parsed, perr = _parse_json_response(raw1)
    logger.debug("  [P1 raw] %s", raw1[:200])

    # Pass 2 — only if fields missing
    raw2 = None
    retried = False
    missing = _missing_fields(parsed if isinstance(parsed, list) else None)

    if missing and not _stop_event.is_set():
        retried = True
        partial_str = json.dumps(parsed[0] if parsed and isinstance(parsed, list) else {})
        retry_prompt = (
            f"Your previous extraction returned: {partial_str}\n"
            f"Missing fields: {', '.join(missing)}.\n"
            "Look carefully at the CENTER of the image — the largest highlighted segment — "
            "and fill in ALL missing values. "
            "Return ONLY the complete JSON array with all six fields "
            "(analyst, stockPick, recommended_price, current_price, stop_loss, target). "
            "Use null for any value that is genuinely not visible. No markdown."
        )
        raw2, err2 = _ollama_post(url, model, retry_prompt, timeout, image_b64)
        logger.debug("  [P2 raw] %s", (raw2 or "")[:200])

        if not err2 and raw2:
            parsed2, _ = _parse_json_response(raw2)
            if parsed and isinstance(parsed, list) and parsed2 and isinstance(parsed2, list):
                # Merge: fill nulls from pass-1 with pass-2 non-null values
                merged = {**parsed[0], **{k: v for k, v in parsed2[0].items() if v is not None}}
                parsed = [merged]
            elif parsed2:
                parsed = parsed2

    parsed = _apply_folder_analyst(parsed, keyword)
    parsed = _attach_transcript_context(parsed, transcript_context)

    still_missing = _missing_fields(parsed if isinstance(parsed, list) else None)
    parse_error = perr
    if not parse_error and still_missing:
        parse_error = f"Null after retry: {', '.join(still_missing)}"

    return _make_result(keyword, image_path, raw1, raw2, parsed, None,
                        parse_error=parse_error, retried=retried,
                        transcript_context=transcript_context)


def _make_result(
    keyword: str,
    image_path: Path,
    raw1: str | None,
    raw2: str | None,
    analysis: Any,
    error: str | None,
    parse_error: str | None = None,
    retried: bool = False,
    transcript_context: dict | None = None,
) -> dict:
    result = {
        "keyword": keyword,
        "frame_name": image_path.name,
        "frame_path": str(image_path),
        "raw_response": raw1,
        "raw_retry_response": raw2,
        "retried": retried,
        "analysis": analysis,
        "parse_error": parse_error,
        "error": error,
    }
    if transcript_context:
        # Persist context alongside the raw analysis so downstream code
        # (dashboard, dedup fallback) can recover it even if the vision
        # model dropped it from the analysis payload.
        result["transcript_context"] = transcript_context
    return result


def phase1_extract(
    matched_dir: Path,
    url: str,
    model: str,
    timeout: int,
    transcript_map: dict | None = None,
) -> list[dict]:
    """
    Phase 1: iterate all images in matched/ sub-folders and extract
    structured JSON picks via Ollama.

    ``transcript_map`` -- optional ``{frame_name: transcript_context}``
    dict from the main pipeline's ocr_results.json. When a frame has
    context, it's appended to the vision prompt ("Spoken context (±8s):
    ...") to help the model disambiguate ambiguous screens.

    Respects _stop_event for graceful interrupt.
    Returns list of result dicts (one per image).
    """
    images = _collect_images(matched_dir)
    if not images:
        logger.warning("Phase 1: no images found in %s", matched_dir)
        return []

    transcript_map = transcript_map or {}
    with_ctx = sum(1 for _, p in images if p.name in transcript_map)
    logger.info(
        "Phase 1 -- extracting %d images with model '%s' (%d with spoken context)",
        len(images), model, with_ctx,
    )
    results: list[dict] = []

    for idx, (keyword, img_path) in enumerate(images, 1):
        if _stop_event.is_set():
            logger.warning("Phase 1 -- interrupted at image %d/%d", idx, len(images))
            break

        logger.info("  [%d/%d] %s/%s", idx, len(images), keyword, img_path.name)
        ctx = transcript_map.get(img_path.name)
        result = _extract_single(keyword, img_path, url, model, timeout,
                                 transcript_context=ctx)

        # Log raw output at INFO so it's always visible
        if result["raw_response"]:
            logger.info("  [pass-1 raw]\n%s", result["raw_response"])
        if result["raw_retry_response"]:
            logger.info("  [pass-2 raw]\n%s", result["raw_retry_response"])

        if result["error"]:
            logger.error("  FAILED: %s", result["error"])
        else:
            tag = " [retried]" if result["retried"] else ""
            logger.info("  analysis%s: %s", tag, json.dumps(result["analysis"]))

        results.append(result)

    logger.info("Phase 1 complete — %d processed", len(results))
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Frame-path enrichment helper
# ─────────────────────────────────────────────────────────────────────────────

def _enrich_with_frame_paths(
    picks: list[dict],
    p1_results: list[dict],
    html_dir: Path,
) -> list[dict]:
    """
    Attach ``_frame_path`` (relative to viewer.html's directory) AND
    ``transcript_context`` to each deduplicated pick by matching on
    ``stockPick`` against phase-1 results. First matching frame wins;
    picks with no match are left unchanged.

    The transcript_context re-attachment is a belt-and-suspenders safety
    net: even if the Ollama dedup pass drops the field (against instructions),
    we recover it here from the phase-1 raw data indexed by stock name.
    """
    stock_to_frame: dict[str, str] = {}
    stock_to_ctx: dict[str, dict] = {}
    for r in p1_results:
        fp = r.get("frame_path", "")
        analysis = r.get("analysis")
        ctx = r.get("transcript_context")
        if not fp or not analysis:
            continue
        items = analysis if isinstance(analysis, list) else [analysis]
        for item in items:
            stock = (item.get("stockPick") or "").strip().lower()
            if stock and stock not in stock_to_frame:
                try:
                    rel = Path(fp).relative_to(html_dir)
                    stock_to_frame[stock] = str(rel)
                except ValueError:
                    stock_to_frame[stock] = fp  # fallback to absolute
                # Only record ctx on first-frame-wins so it lines up with
                # the frame_path we chose above.
                if ctx and isinstance(ctx, dict):
                    stock_to_ctx[stock] = ctx

    enriched = []
    for pick in picks:
        p = dict(pick)
        stock = (p.get("stockPick") or "").strip().lower()
        if stock in stock_to_frame:
            p["_frame_path"] = stock_to_frame[stock]
        # Only fill in transcript_context when the dedup pass dropped it;
        # never overwrite a context the model preserved verbatim.
        if stock in stock_to_ctx and not p.get("transcript_context"):
            p["transcript_context"] = stock_to_ctx[stock]
        enriched.append(p)
    return enriched


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 — Deduplication
# ─────────────────────────────────────────────────────────────────────────────

def phase2_dedup(
    phase1_results: list[dict],
    url: str,
    model: str,
    timeout: int,
) -> list[dict]:
    """
    Phase 2: aggregate all non-null picks from Phase 1, send to Ollama
    for deduplication/merging, and return the consolidated JSON array.
    """
    if _stop_event.is_set():
        logger.warning("Phase 2 — skipped (interrupted)")
        return []

    # Collect all valid analysis picks
    raw_picks: list[dict] = []
    for r in phase1_results:
        analysis = r.get("analysis")
        if analysis and isinstance(analysis, list):
            raw_picks.extend(analysis)

    if not raw_picks:
        logger.warning("Phase 2 — no picks to deduplicate")
        return []

    logger.info("Phase 2 — deduplicating %d raw picks", len(raw_picks))

    dedup_prompt = DEDUP_PROMPT_TEMPLATE.format(data=json.dumps(raw_picks, ensure_ascii=False))
    raw, err = _ollama_post(url, model, dedup_prompt, timeout)

    if err:
        logger.error("Phase 2 — Ollama error: %s", err)
        return raw_picks  # fall back to undeduped data

    logger.info("Phase 2 [raw dedup response]\n%s", raw)

    parsed, perr = _parse_json_response(raw)
    if perr or not isinstance(parsed, list):
        logger.error("Phase 2 — could not parse dedup response: %s", perr)
        return raw_picks  # fall back

    logger.info("Phase 2 complete — %d unique picks", len(parsed))
    return parsed


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 — HTML Dashboard (runs in a parallel thread)
# ─────────────────────────────────────────────────────────────────────────────

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>LOCALOCR — Stock Picks Dashboard</title>
<style>
  :root {
    --bg: #0f1117; --surface: #1a1d27; --border: #2a2d3a;
    --accent: #4f8ef7; --green: #22c55e; --red: #ef4444;
    --yellow: #facc15; --text: #e2e8f0; --muted: #64748b;
    --radius: 10px; --card-w: 340px;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: "Inter", system-ui, sans-serif; min-height: 100vh; padding: 2rem; }
  h1 { font-size: 1.6rem; font-weight: 700; color: var(--accent); margin-bottom: .4rem; }
  .subtitle { color: var(--muted); font-size: .85rem; margin-bottom: 1.6rem; }
  .controls { display: flex; gap: .75rem; flex-wrap: wrap; margin-bottom: 1.8rem; align-items: center; }
  .controls input { flex: 1; min-width: 220px; padding: .55rem .9rem; background: var(--surface);
    border: 1px solid var(--border); border-radius: var(--radius); color: var(--text); font-size: .9rem; }
  .controls input:focus { outline: none; border-color: var(--accent); }
  .controls select { padding: .55rem .9rem; background: var(--surface); border: 1px solid var(--border);
    border-radius: var(--radius); color: var(--text); font-size: .9rem; cursor: pointer; }
  .badge { font-size: .75rem; padding: .2rem .6rem; border-radius: 99px; font-weight: 600; }
  .badge-total { background: #1e3a5f; color: var(--accent); }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(var(--card-w), 1fr)); gap: 1.2rem; }
  .card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);
    padding: 1.2rem 1.4rem; transition: border-color .2s, transform .15s; position: relative; }
  .card:hover { border-color: var(--accent); transform: translateY(-2px); }
  .card-header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: .9rem; }
  .card-ticker { font-size: 1.15rem; font-weight: 700; color: var(--accent); }
  .card-analyst { font-size: .78rem; color: var(--muted); margin-top: .15rem; }
  .card-keyword { font-size: .7rem; background: #1f2a1f; color: var(--green); padding: .2rem .5rem;
    border-radius: 99px; font-weight: 600; }
  .row { display: flex; justify-content: space-between; padding: .35rem 0;
    border-bottom: 1px solid var(--border); font-size: .85rem; }
  .row:last-child { border-bottom: none; }
  .row .label { color: var(--muted); }
  .row .value { font-weight: 600; }
  .value.green { color: var(--green); }
  .value.red { color: var(--red); }
  .value.yellow { color: var(--yellow); }
  .null-val { color: var(--muted); font-style: italic; font-weight: 400; }
  .empty { text-align: center; padding: 4rem; color: var(--muted); font-size: 1rem; }
  .ts { position: absolute; bottom: .6rem; right: .9rem; font-size: .68rem; color: var(--border); }
  .screenshot-link { display: block; margin-top: .7rem; font-size: .75rem; color: var(--accent);
    text-decoration: none; opacity: .7; transition: opacity .2s; }
  .screenshot-link:hover { opacity: 1; text-decoration: underline; }
  /* Spoken-context (transcript) section */
  .ctx { margin-top: .8rem; font-size: .82rem; }
  .ctx summary { cursor: pointer; color: var(--muted); user-select: none;
    padding: .3rem 0; outline: none; }
  .ctx summary:hover { color: var(--text); }
  .ctx-body { margin-top: .3rem; padding: .55rem .75rem; background: #0f151d;
    border-radius: 6px; border: 1px solid var(--border); }
  .ctx-body p { margin: .2rem 0; line-height: 1.35; color: var(--text); }
  .ctx-label { color: var(--muted); font-weight: 600; margin-right: .3rem; }
  .ctx-speaker { color: var(--muted); font-size: .75rem; margin-top: .35rem !important;
    padding-top: .3rem; border-top: 1px dashed var(--border); }
</style>
</head>
<body>
<h1>📊 Stock Picks Dashboard</h1>
<p class="subtitle" id="subtitle">Generated by LOCALOCR post-OCR pipeline</p>
<div class="controls">
  <input type="text" id="search" placeholder="Search stock, analyst…" oninput="render()">
  <select id="sort" onchange="render()">
    <option value="">— Sort by —</option>
    <option value="stockPick">Stock</option>
    <option value="analyst">Analyst</option>
    <option value="target">Target ↑</option>
    <option value="stop_loss">Stop Loss ↑</option>
  </select>
  <span class="badge badge-total" id="badge">0 picks</span>
</div>
<div class="grid" id="grid"></div>

<script>
const DATA = __PICKS_JSON__;
const TIMESTAMP = "__TIMESTAMP__";

function fmt(v) {
  if (v === null || v === undefined || v === "") return '<span class="null-val">—</span>';
  return v;
}
function numVal(v, cls) {
  if (v === null || v === undefined) return '<span class="null-val">—</span>';
  return `<span class="value ${cls}">${v}</span>`;
}
function gainPct(current, target) {
  const c = parseFloat(current), t = parseFloat(target);
  if (!isFinite(c) || !isFinite(t) || c === 0) return '<span class="null-val">—</span>';
  const pct = ((t - c) / c * 100).toFixed(1);
  const cls = pct >= 0 ? "green" : "red";
  return `<span class="value ${cls}">${pct >= 0 ? "+" : ""}${pct}%</span>`;
}
function card(p) {
  const ctx = p.transcript_context;
  const hasCtx = ctx && (ctx.before || ctx.at || ctx.after);
  const ctxHTML = hasCtx ? `
    <details class="ctx">
      <summary> Spoken context (±8s)</summary>
      <div class="ctx-body">
        ${ctx.before ? `<p><span class="ctx-label">Before:</span> “${ctx.before}”</p>` : ""}
        ${ctx.at     ? `<p><span class="ctx-label">At:</span> “${ctx.at}”</p>`         : ""}
        ${ctx.after  ? `<p><span class="ctx-label">After:</span> “${ctx.after}”</p>`   : ""}
        ${ctx.speaker ? `<p class="ctx-speaker">Speaker: ${ctx.speaker}</p>` : ""}
      </div>
    </details>` : "";
  return `<div class="card">
    <div class="card-header">
      <div>
        <div class="card-ticker">${fmt(p.stockPick)}</div>
        <div class="card-analyst">${p.analyst ? "by " + p.analyst : ""}</div>
      </div>
      ${p._keyword ? `<span class="card-keyword">${p._keyword}</span>` : ""}
    </div>
    <div class="row"><span class="label">Recommended Price</span>${numVal(p.recommended_price, "green")}</div>
    <div class="row"><span class="label">Current Price</span>${numVal(p.current_price, "")}</div>
    <div class="row"><span class="label">Stop Loss</span>${numVal(p.stop_loss, "red")}</div>
    <div class="row"><span class="label">Target</span>${numVal(p.target, "yellow")}</div>
    <div class="row"><span class="label">Upside</span>${gainPct(p.current_price, p.target)}</div>
    ${p._frame_path ? `<a class="screenshot-link" href="${p._frame_path}" target="_blank"> View screenshot</a>` : ""}
    ${ctxHTML}
    <span class="ts">${TIMESTAMP}</span>
  </div>`;
}
function render() {
  const q = document.getElementById("search").value.toLowerCase();
  const sortKey = document.getElementById("sort").value;
  let items = DATA.filter(p =>
    (p.stockPick||"").toLowerCase().includes(q) ||
    (p.analyst||"").toLowerCase().includes(q)
  );
  if (sortKey) items = [...items].sort((a, b) => {
    const av = a[sortKey] ?? "", bv = b[sortKey] ?? "";
    return String(av).localeCompare(String(bv), undefined, {numeric: true});
  });
  const grid = document.getElementById("grid");
  document.getElementById("badge").textContent = items.length + " pick" + (items.length !== 1 ? "s" : "");
  grid.innerHTML = items.length ? items.map(card).join("") : '<div class="empty">No picks match.</div>';
}
document.getElementById("subtitle").textContent =
  `${DATA.length} unique picks · Generated ${TIMESTAMP}`;
render();
</script>
</body>
</html>"""


def _escape_pick_strings(value: Any) -> Any:
    """Recursively HTML-escape every string in a pick payload.

    Numbers, bools, None and other non-string scalars pass through
    unchanged so client-side numeric formatting still works. Escaping
    is done SERVER-SIDE (fix for review finding W6) so the resulting
    JSON, once dropped into a JS template literal via ``.innerHTML``,
    renders LLM-derived text as inert text rather than executable HTML.

    Trade-off: users can't have live '<' in analyst names, but they
    also can't be XSS'd by a malicious/hallucinated ``</script>`` in
    an analyst name. Correct call for a local viewer.
    """
    if isinstance(value, str):
        return html.escape(value, quote=True)
    if isinstance(value, dict):
        return {k: _escape_pick_strings(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_escape_pick_strings(v) for v in value]
    return value


def build_dashboard_html(
    picks: list[dict],
    timestamp: str = "",
) -> str:
    r"""Render the viewer dashboard HTML string for a list of picks.

    Pure function -- no filesystem I/O -- so tests can assert on the
    output directly. ``_build_html`` writes this same string to disk.

    Every string in every pick is HTML-escaped server-side (recursive)
    before being embedded in the JSON, and the emitted JSON has
    ``</`` replaced with ``<\/`` as a defense-in-depth measure against
    ``<script>`` tag breakout.
    """
    safe_picks = [_escape_pick_strings(p) for p in picks]
    picks_json = json.dumps(safe_picks, ensure_ascii=False).replace("</", "<\\/")
    safe_timestamp = html.escape(timestamp, quote=True)
    return (_HTML_TEMPLATE
            .replace("__PICKS_JSON__", picks_json)
            .replace("__TIMESTAMP__", safe_timestamp))


def _build_html(picks: list[dict], output_path: Path, timestamp: str) -> None:
    """
    Inject picks JSON into the HTML template and write viewer.html.
    Called from a background thread (Phase 3).

    Delegates the actual rendering to :func:`build_dashboard_html` so
    both the write path and the test path share ONE code path -- there
    is no way for the on-disk output to diverge from what tests assert.
    """
    logger.info("Phase 3 [thread] -- building HTML dashboard (%d picks)", len(picks))
    html_out = build_dashboard_html(picks, timestamp)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html_out, encoding="utf-8")
    logger.info("Phase 3 [thread] -- viewer.html written -> %s", output_path)


def phase3_html_async(
    picks: list[dict],
    output_path: Path,
    timestamp: str,
    executor: ThreadPoolExecutor,
) -> Future:
    """
    Submit Phase 3 to the shared ThreadPoolExecutor.
    Returns the Future so the caller can optionally wait or check status.
    """
    return executor.submit(_build_html, picks, output_path, timestamp)


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline orchestrator
# ─────────────────────────────────────────────────────────────────────────────

class PostOCRPipeline:
    """
    Encapsulates the three-phase post-OCR pipeline.
    Instantiate with a config dict, call .run().
    """

    def __init__(self, config: dict) -> None:
        self.url      = config.get("url", DEFAULT_OLLAMA_URL)
        self.model    = config.get("model", DEFAULT_MODEL)
        self.timeout  = int(config.get("timeout_seconds", DEFAULT_TIMEOUT))
        self.skip_dedup = bool(config.get("skip_dedup", False))

        out_dir = Path(config.get("output_directory", "./output")).resolve()
        self.matched_dir  = Path(config.get("matched_dir", str(out_dir / "matched"))).resolve()
        self.metadata_dir = out_dir / "metadata"
        self.html_path    = out_dir / "viewer.html"

        self.phase1_json  = self.metadata_dir / "phase1_extractions.json"
        self.phase2_json  = self.metadata_dir / "phase2_deduplicated.json"
        self.ocr_results_json = self.metadata_dir / "ocr_results.json"

    # ── helpers ───────────────────────────────────────────────────────────────

    def _write(self, path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("Saved → %s", path)

    # ── run ───────────────────────────────────────────────────────────────────

    def run(self) -> dict:
        """
        Execute Phase 1 → Phase 2 → Phase 3 (async).

        Returns a summary dict. Safe to call from a larger pipeline.
        Raises KeyboardInterrupt only after saving any partial data.
        """
        timestamp   = time.strftime("%Y-%m-%d %H:%M:%S")
        start       = time.time()
        interrupted = False

        logger.info("=" * 65)
        logger.info("PostOCRPipeline started — model: %s | url: %s", self.model, self.url)
        logger.info("=" * 65)

        # Load per-frame transcript context (if the main pipeline produced
        # any). Missing/empty -> {} and the rest of the flow behaves
        # identically to pre-Phase-2 (no addendum, no context section).
        transcript_map = _load_transcript_contexts(self.ocr_results_json)

        # ── Phase 1 ──────────────────────────────────────────────────────────────
        logger.info("─── Phase 1: Vision Extraction ────────────────────────────────")
        try:
            p1_results = phase1_extract(
                self.matched_dir, self.url, self.model, self.timeout,
                transcript_map=transcript_map,
            )
        except KeyboardInterrupt:
            logger.warning("Phase 1 interrupted by user.")
            _stop_event.set()
            interrupted = True
            p1_results = []

        self._write(self.phase1_json, {
            "timestamp": timestamp,
            "interrupted": interrupted,
            "results": p1_results,
        })

        # ── Phase 2 ───────────────────────────────────────────────────────────
        unique_picks: list[dict] = []
        if not interrupted and not self.skip_dedup:
            logger.info("─── Phase 2: Deduplication ──────────────────────────────────")
            try:
                unique_picks = phase2_dedup(
                    p1_results, self.url, self.model, self.timeout
                )
            except KeyboardInterrupt:
                logger.warning("Phase 2 interrupted by user.")
                _stop_event.set()
                interrupted = True
                unique_picks = [
                    r["analysis"][0]
                    for r in p1_results
                    if r.get("analysis") and isinstance(r["analysis"], list)
                ]
        elif self.skip_dedup:
            logger.info("─── Phase 2: Skipped (--skip-dedup) ────────────────────────")
            unique_picks = [
                r["analysis"][0]
                for r in p1_results
                if r.get("analysis") and isinstance(r["analysis"], list)
            ]

        self._write(self.phase2_json, {
            "timestamp": timestamp,
            "interrupted": interrupted,
            "picks": unique_picks,
        })

        # Enrich picks with source frame paths so the dashboard can link screenshots
        unique_picks = _enrich_with_frame_paths(
            unique_picks, p1_results, self.html_path.parent
        )

        # ── Phase 3 (parallel thread) ─────────────────────────────────────────
        logger.info("─── Phase 3: HTML Dashboard (background thread) ─────────────")
        html_future: Future | None = None
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="html-builder") as ex:
            html_future = phase3_html_async(unique_picks, self.html_path, timestamp, ex)
            # Block briefly to allow the thread to start, then continue
            try:
                html_future.result(timeout=120)
            except KeyboardInterrupt:
                logger.warning("Phase 3 interrupted — HTML may be incomplete.")
                _stop_event.set()
                interrupted = True
            except Exception as exc:
                logger.error("Phase 3 HTML build failed: %s", exc)

        elapsed = round(time.time() - start, 2)

        summary = {
            "status": "interrupted" if interrupted else "complete",
            "model": self.model,
            "ollama_url": self.url,
            "phase1_images_processed": len(p1_results),
            "phase1_succeeded": sum(1 for r in p1_results if not r.get("error")),
            "phase2_unique_picks": len(unique_picks),
            "phase1_file": str(self.phase1_json),
            "phase2_file": str(self.phase2_json),
            "html_dashboard": str(self.html_path),
            "elapsed_seconds": elapsed,
        }

        logger.info("=" * 65)
        logger.info("PostOCRPipeline %s in %.1fs", summary["status"], elapsed)
        logger.info("  Phase1 processed : %d  succeeded: %d",
                    summary["phase1_images_processed"], summary["phase1_succeeded"])
        logger.info("  Phase2 unique    : %d picks", summary["phase2_unique_picks"])
        logger.info("  HTML dashboard   : %s", summary["html_dashboard"])
        logger.info("=" * 65)

        return summary


# ─────────────────────────────────────────────────────────────────────────────
# Public entry-point (for use from larger pipeline)
# ─────────────────────────────────────────────────────────────────────────────

def run_post_ocr_pipeline(config: dict) -> dict:
    """
    Plug-in entry point. Pass the same config dict used by the main pipeline.
    Reads ollama_config sub-key if present; falls back to top-level keys.

    Example
    -------
    from post_ocr_pipeline import run_post_ocr_pipeline

    summary = run_post_ocr_pipeline(config)
    """
    ocfg = config.get("ollama_config", {})
    merged = {
        "url":              ocfg.get("url",  config.get("ollama_url",  DEFAULT_OLLAMA_URL)),
        "model":            ocfg.get("model", config.get("model",     DEFAULT_MODEL)),
        "timeout_seconds":  ocfg.get("timeout_seconds", config.get("timeout_seconds", DEFAULT_TIMEOUT)),
        "skip_dedup":       config.get("skip_dedup", False),
        "output_directory": config.get("output_directory", "./output"),
        "matched_dir":      config.get("matched_dir", ""),
    }
    pipeline = PostOCRPipeline(merged)
    return pipeline.run()


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Post-OCR pipeline: Vision extraction → Dedup → HTML dashboard"
    )
    parser.add_argument("--matched-dir",   default="./output/matched",    help="Path to matched frames directory")
    parser.add_argument("--output-dir",    default="./output",             help="Base output directory")
    parser.add_argument("--url",           default=DEFAULT_OLLAMA_URL,     help="Ollama base URL")
    parser.add_argument("--model",         default=DEFAULT_MODEL,          help="Ollama model name")
    parser.add_argument("--timeout",       type=int, default=DEFAULT_TIMEOUT, help="Per-request timeout (seconds)")
    parser.add_argument("--skip-dedup",    action="store_true",            help="Skip Phase 2 deduplication")
    parser.add_argument("--verbose", "-v", action="store_true",            help="Enable DEBUG logging")
    args = parser.parse_args()

    _setup_logging(args.verbose)

    config = {
        "url":              args.url,
        "model":            args.model,
        "timeout_seconds":  args.timeout,
        "skip_dedup":       args.skip_dedup,
        "output_directory": args.output_dir,
        "matched_dir":      args.matched_dir,
    }

    pipeline = PostOCRPipeline(config)

    try:
        summary = pipeline.run()
    except KeyboardInterrupt:
        _stop_event.set()
        logger.warning("Pipeline interrupted by user — partial data saved.")
        sys.exit(1)

    sys.exit(0 if summary["status"] == "complete" else 1)


if __name__ == "__main__":
    main()
