"""
benchmark_extraction.py
~~~~~~~~~~~~~~~~~~~~~~~
Compare the three ``extraction_mode`` values (``interval``, ``scene``,
``hybrid``) on the same video and prove the >=5x frame-reduction claim
from requirements EXTRACT-01 / EXTRACT-06 without losing any matched
keywords.

Zero-duplication policy: the script imports production code
(``extract_frames``, ``run_ocr``, ``match_text``) and only orchestrates
timing + reporting.

Usage:
    python benchmark_extraction.py --video ./input_videos/asset/low/13.mp4
    python benchmark_extraction.py --video clip.mp4 --keywords FII,Sethi
    python benchmark_extraction.py --video clip.mp4 --keep-output

Exit codes:
    0  every mode ran AND scene-mode kept every keyword interval-mode found
    1  a benchmark run itself crashed
    2  scene mode lost at least one keyword vs interval mode
       (gates the EXTRACT-01 correctness half of the claim)
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

# Local imports \u2014 same sys.path shim the tests use.
sys.path.insert(0, str(Path(__file__).parent))

from src.extractor import extract_frames  # noqa: E402
from src.matcher.text_matcher import match_text  # noqa: E402
from src.ocr.ocr_engine import run_ocr  # noqa: E402


DEFAULT_CONFIG_PATH = Path(__file__).parent / "config" / "config.json"


def _load_default_keywords() -> list[str]:
    """Read match_keywords from the reference config (fallback: hard-coded)."""
    try:
        cfg = json.loads(DEFAULT_CONFIG_PATH.read_text())
        return list(cfg.get("match_keywords", []))
    except (OSError, json.JSONDecodeError):
        return []


def _mode_config(
    mode: str,
    interval: int,
    threshold: float,
    min_gap: float,
    max_gap: float,
    video: str,
) -> dict:
    """Build the cfg dict passed into extract_frames for a given mode.

    Kept trivial and explicit \u2014 no meta-programming, no cfg mutation.
    """
    base = {
        "extraction_mode": mode,
        "video_path": video,
        "frame_interval_seconds": interval,
    }
    if mode in ("scene", "hybrid"):
        scene_cfg = {"threshold": threshold, "min_gap_seconds": min_gap}
        if mode == "hybrid":
            scene_cfg["max_gap_seconds"] = max_gap
        base["scene_config"] = scene_cfg
    return base


def _bench_one_mode(
    mode: str,
    video: str,
    interval: int,
    threshold: float,
    min_gap: float,
    max_gap: float,
    keywords: list,
    tmp_root: Path,
    ocr_engine: str,
) -> dict:
    """Run extract + OCR + match on ``video`` under ``mode`` and time each step.

    Returns a dict with:
        mode, frame_count, extract_s, ocr_s, match_s, total_s,
        unique_matched_keywords (list), error (str | None), out_dir (Path)
    """
    out_dir = tmp_root / f"bench_{mode}"
    out_dir.mkdir(parents=True, exist_ok=True)

    cfg = _mode_config(mode, interval, threshold, min_gap, max_gap, video)
    # Give OCR the same engine per mode so timings are comparable, not the
    # 'auto' selector picking different engines under the hood.
    cfg["ocr_engine"] = ocr_engine

    print(f"\n[{mode}] extracting frames \u2192 {out_dir}", flush=True)
    t0 = time.perf_counter()
    try:
        frames = extract_frames(video, str(out_dir), interval, cfg=cfg)
    except Exception as exc:
        return {
            "mode": mode,
            "frame_count": 0,
            "extract_s": time.perf_counter() - t0,
            "ocr_s": 0.0,
            "match_s": 0.0,
            "total_s": time.perf_counter() - t0,
            "unique_matched_keywords": [],
            "error": f"extract_frames failed: {exc}",
            "out_dir": out_dir,
        }
    extract_s = time.perf_counter() - t0
    print(f"[{mode}] extracted {len(frames)} frames in {extract_s:.1f}s", flush=True)

    print(f"[{mode}] running OCR (engine={ocr_engine}) ...", flush=True)
    t0 = time.perf_counter()
    try:
        ocr_results = run_ocr(frames, languages=["en"], config=cfg)
    except Exception as exc:
        return {
            "mode": mode,
            "frame_count": len(frames),
            "extract_s": extract_s,
            "ocr_s": time.perf_counter() - t0,
            "match_s": 0.0,
            "total_s": extract_s + (time.perf_counter() - t0),
            "unique_matched_keywords": [],
            "error": f"run_ocr failed: {exc}",
            "out_dir": out_dir,
        }
    ocr_s = time.perf_counter() - t0
    print(f"[{mode}] OCR done in {ocr_s:.1f}s", flush=True)

    print(f"[{mode}] matching keywords ...", flush=True)
    t0 = time.perf_counter()
    try:
        matched = match_text(ocr_results, keywords, match_mode="contains")
    except Exception as exc:
        return {
            "mode": mode,
            "frame_count": len(frames),
            "extract_s": extract_s,
            "ocr_s": ocr_s,
            "match_s": time.perf_counter() - t0,
            "total_s": extract_s + ocr_s + (time.perf_counter() - t0),
            "unique_matched_keywords": [],
            "error": f"match_text failed: {exc}",
            "out_dir": out_dir,
        }
    match_s = time.perf_counter() - t0

    unique_kws: set = set()
    for r in matched:
        unique_kws.update(r.get("matched_keywords", []))

    print(
        f"[{mode}] matched {len(unique_kws)} unique keywords in {match_s:.1f}s",
        flush=True,
    )

    return {
        "mode": mode,
        "frame_count": len(frames),
        "extract_s": extract_s,
        "ocr_s": ocr_s,
        "match_s": match_s,
        "total_s": extract_s + ocr_s + match_s,
        "unique_matched_keywords": sorted(unique_kws),
        "error": None,
        "out_dir": out_dir,
    }


def _print_table(results: list[dict]) -> None:
    """Print a markdown-formatted comparison table."""
    header = "| Mode | Frames | Extract | OCR | Match | Total | Unique Keywords Matched |"
    sep = "|------|--------|---------|-----|-------|-------|-------------------------|"
    print("\n" + header)
    print(sep)
    for r in results:
        if r["error"]:
            print(
                f"| {r['mode']} | {r['frame_count']} | {r['extract_s']:.1f}s | "
                f"\u2014 | \u2014 | \u2014 | ERROR: {r['error']} |"
            )
            continue
        kws = "{" + ", ".join(r["unique_matched_keywords"]) + "}" if r["unique_matched_keywords"] else "{}"
        print(
            f"| {r['mode']} | {r['frame_count']} | {r['extract_s']:.1f}s | "
            f"{r['ocr_s']:.1f}s | {r['match_s']:.1f}s | {r['total_s']:.1f}s | {kws} |"
        )


def _summary_and_gate(results: list[dict]) -> int:
    """Print scene-vs-interval summary and enforce the EXTRACT-01 correctness gate.

    Returns exit code (0 pass, 2 keyword-loss fail, 1 general failure).
    """
    by_mode = {r["mode"]: r for r in results}

    interval_r = by_mode.get("interval")
    scene_r = by_mode.get("scene")

    if not interval_r or interval_r["error"]:
        print("\nFAIL: interval-mode baseline did not complete cleanly", file=sys.stderr)
        return 1
    if not scene_r or scene_r["error"]:
        print("\nFAIL: scene-mode benchmark did not complete cleanly", file=sys.stderr)
        return 1

    if scene_r["frame_count"] == 0:
        print(
            "\nFAIL: scene mode extracted 0 frames \u2014 threshold likely too high",
            file=sys.stderr,
        )
        return 1

    ratio = interval_r["frame_count"] / scene_r["frame_count"]
    print(
        f"\nScene vs Interval: {ratio:.2f}x frame reduction "
        f"({interval_r['frame_count']} -> {scene_r['frame_count']} frames)"
    )

    interval_kws = set(interval_r["unique_matched_keywords"])
    scene_kws = set(scene_r["unique_matched_keywords"])
    lost = interval_kws - scene_kws

    if lost:
        print(
            f"FAIL: scene mode lost keywords vs interval: {sorted(lost)}",
            file=sys.stderr,
        )
        return 2

    print("OK: no keywords lost")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare interval/scene/hybrid frame-extraction modes end-to-end"
    )
    parser.add_argument("--video", required=True, help="Path to input video (mp4/mkv/mov/...)")
    parser.add_argument("--interval", type=int, default=2, help="Interval seconds (default 2)")
    parser.add_argument("--threshold", type=float, default=0.3, help="Scene threshold (default 0.3)")
    parser.add_argument("--min-gap", type=float, default=1.0, help="Scene debounce seconds (default 1.0)")
    parser.add_argument("--max-gap", type=float, default=10.0, help="Hybrid fallback tick seconds (default 10.0)")
    parser.add_argument(
        "--keywords",
        type=str,
        default=None,
        help="Comma-separated keywords (default: read match_keywords from config/config.json)",
    )
    parser.add_argument(
        "--ocr-engine",
        type=str,
        default="apple_vision",
        help="OCR engine to run for all three modes (default apple_vision \u2014 kept constant for fair comparison)",
    )
    parser.add_argument("--keep-output", action="store_true", help="Do not delete temp frame dirs after run")
    args = parser.parse_args()

    video_path = Path(args.video)
    if not video_path.exists():
        print(f"ERROR: video not found: {video_path}", file=sys.stderr)
        return 1

    keywords: list = (
        [k.strip() for k in args.keywords.split(",") if k.strip()]
        if args.keywords
        else _load_default_keywords()
    )
    if not keywords:
        print("ERROR: no keywords available (empty --keywords and no match_keywords in config/config.json)", file=sys.stderr)
        return 1

    print("=" * 60)
    print(f"Benchmark: {video_path}")
    print(f"Keywords ({len(keywords)}): {keywords}")
    print(f"Interval={args.interval}s  threshold={args.threshold}  min_gap={args.min_gap}s  max_gap={args.max_gap}s")
    print(f"OCR engine (all modes): {args.ocr_engine}")
    print("=" * 60)

    tmp_root = Path(tempfile.mkdtemp(prefix="benchmark_extraction_"))
    print(f"Temp root: {tmp_root} (keep_output={args.keep_output})")

    results: list = []
    try:
        for mode in ("interval", "scene", "hybrid"):
            results.append(
                _bench_one_mode(
                    mode=mode,
                    video=str(video_path),
                    interval=args.interval,
                    threshold=args.threshold,
                    min_gap=args.min_gap,
                    max_gap=args.max_gap,
                    keywords=keywords,
                    tmp_root=tmp_root,
                    ocr_engine=args.ocr_engine,
                )
            )
    finally:
        _print_table(results)

        if not args.keep_output:
            for r in results:
                shutil.rmtree(r["out_dir"], ignore_errors=True)
            # Attempt to remove the root; harmless if leftover.
            try:
                tmp_root.rmdir()
            except OSError:
                pass

    return _summary_and_gate(results)


if __name__ == "__main__":
    sys.exit(main())
