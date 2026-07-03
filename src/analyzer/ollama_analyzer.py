"""
ollama_analyzer.py
~~~~~~~~~~~~~~~~~~
Sends matched/filtered screenshots to a local Ollama vision model and
saves the analysis results to JSON.

Ollama API used:
    POST <url>/api/generate
    { "model": "...", "prompt": "...", "images": ["<base64>"], "stream": false }
"""

import base64
import json
import logging
import time
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class OllamaAnalysisError(Exception):
    """Raised when Ollama analysis encounters a fatal error."""


def _encode_image(image_path: Path) -> str:
    """Return a base64-encoded string for the given image file."""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _analyze_single(
    image_path: Path,
    url: str,
    model: str,
    prompt: str,
    timeout: int,
) -> dict:
    """
    Send one image to Ollama and return the result dict.

    Returns
    -------
    dict with keys:
        frame_name, frame_path, model, prompt, analysis, error, elapsed_seconds
    """
    result = {
        "frame_name": image_path.name,
        "frame_path": str(image_path),
        "model": model,
        "prompt": prompt,
        "analysis": None,
        "error": None,
        "elapsed_seconds": None,
    }

    t0 = time.time()
    try:
        encoded = _encode_image(image_path)
        payload = {
            "model": model,
            "prompt": prompt,
            "images": [encoded],
            "stream": False,
        }
        response = requests.post(
            f"{url.rstrip('/')}/api/generate",
            json=payload,
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
        result["analysis"] = data.get("response", "")
    except requests.exceptions.ConnectionError as exc:
        result["error"] = f"Cannot connect to Ollama at {url}: {exc}"
        logger.warning("Ollama connection error for %s: %s", image_path.name, exc)
    except requests.exceptions.Timeout:
        result["error"] = f"Ollama request timed out after {timeout}s"
        logger.warning("Ollama timeout for %s", image_path.name)
    except requests.exceptions.HTTPError as exc:
        result["error"] = f"Ollama HTTP error: {exc}"
        logger.warning("Ollama HTTP error for %s: %s", image_path.name, exc)
    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)
        logger.warning("Ollama unexpected error for %s: %s", image_path.name, exc)

    result["elapsed_seconds"] = round(time.time() - t0, 2)
    return result


def analyze_with_ollama(
    matched_dir: str,
    output_dir: str,
    ollama_config: dict,
) -> dict:
    """
    Analyze all matched frames with an Ollama vision model.

    Walks every sub-folder of *matched_dir*, finds image files, and
    sends each to the configured Ollama endpoint. Results are written to
    ``<output_dir>/metadata/ollama_analysis.json``.

    Parameters
    ----------
    matched_dir : str
        Path to the directory containing per-keyword sub-folders of images
        (i.e. ``<output_directory>/matched``).
    output_dir : str
        Base output directory; metadata is written under ``metadata/``.
    ollama_config : dict
        Keys:
        - url (str): Ollama base URL, default "http://localhost:11434"
        - model (str): Vision model name, e.g. "llava"
        - prompt (str): Prompt sent with each image
        - timeout_seconds (int): Per-request timeout, default 60
        - include_context_in_vision_analysis (bool): When False (default),
          any file whose name begins with ``ctx_`` (context-window frames
          produced by context mode) is skipped. Set True to send both anchors
          and context frames to Ollama at the cost of ~(1 + N_before + N_after)×
          the request volume per anchor.

    Returns
    -------
    dict with summary keys: total, succeeded, failed, output_file
    """
    url = ollama_config.get("url", "http://localhost:11434")
    model = ollama_config.get("model", "llava")
    prompt = ollama_config.get(
        "prompt",
        "Analyze this screenshot. Extract and describe all visible text, "
        "numbers, names, and any financial or informational content shown.",
    )
    timeout = int(ollama_config.get("timeout_seconds", 60))
    include_context = bool(
        ollama_config.get("include_context_in_vision_analysis", False)
    )

    matched_path = Path(matched_dir).resolve()
    metadata_dir = Path(output_dir).resolve() / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    output_file = metadata_dir / "ollama_analysis.json"

    if not matched_path.exists():
        raise OllamaAnalysisError(
            f"Matched frames directory not found: {matched_path}"
        )

    # Collect all image paths across all keyword sub-folders
    image_exts = {".png", ".jpg", ".jpeg"}
    images: list[tuple[str, Path]] = []  # (keyword_folder, image_path)
    context_skipped = 0
    for keyword_dir in sorted(matched_path.iterdir()):
        if not keyword_dir.is_dir():
            continue
        for img in sorted(keyword_dir.iterdir()):
            if img.suffix.lower() not in image_exts:
                continue
            # Skip context-window frames (ctx_*) unless the caller opts in.
            if not include_context and img.name.startswith("ctx_"):
                context_skipped += 1
                continue
            images.append((keyword_dir.name, img))

    if context_skipped:
        logger.info(
            "Skipping %d context-window frames (set "
            "include_context_in_vision_analysis=true to include)",
            context_skipped,
        )

    if not images:
        logger.warning("No matched images found in %s", matched_path)
        summary = {"total": 0, "succeeded": 0, "failed": 0, "output_file": str(output_file)}
        _write_json(output_file, {"summary": summary, "results": []})
        return summary

    logger.info(
        "Analyzing %d matched images with Ollama model '%s' at %s",
        len(images), model, url,
    )

    results = []
    succeeded = 0
    failed = 0

    for idx, (keyword, image_path) in enumerate(images, 1):
        logger.info(
            "[%d/%d] Analyzing %s (keyword: %s)...",
            idx, len(images), image_path.name, keyword,
        )
        result = _analyze_single(image_path, url, model, prompt, timeout)
        result["keyword"] = keyword
        results.append(result)

        if result["error"]:
            failed += 1
            logger.warning(
                "  Failed: %s", result["error"]
            )
        else:
            succeeded += 1
            preview = (result["analysis"] or "")[:80].replace("\n", " ")
            logger.info("  Done (%.1fs): %s...", result["elapsed_seconds"], preview)

    summary = {
        "total": len(results),
        "succeeded": succeeded,
        "failed": failed,
        "model": model,
        "ollama_url": url,
        "output_file": str(output_file),
    }

    _write_json(output_file, {"summary": summary, "results": results})
    logger.info(
        "Ollama analysis complete: %d succeeded, %d failed → %s",
        succeeded, failed, output_file,
    )

    return summary


def _write_json(path: Path, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
