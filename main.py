#!/usr/bin/env python3
"""
LOCALOCR - Local Video Screen OCR Pipeline for macOS
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Main entry point. Run with: python main.py [config_path]
"""

import argparse
from copy import deepcopy
import json
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from src.pipeline import run_pipeline, run_ocr_only_pipeline, PipelineError


_SUPPORTED_VIDEO_EXTS = {".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v"}


def setup_logging(log_dir: str = "./logs"):
    """Configure logging to both console and file."""
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    log_file = log_path / "localocr.log"

    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    # Console handler (INFO level)
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console_fmt = logging.Formatter("[%(levelname)s] %(message)s")
    console.setFormatter(console_fmt)

    # File handler (DEBUG level)
    file_handler = logging.FileHandler(str(log_file), mode="a", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(file_fmt)

    root_logger.addHandler(console)
    root_logger.addHandler(file_handler)

    return log_file


def load_config(config_path: str, ocr_only: bool = False, input_override: str = None) -> dict:
    """Load and validate JSON config file."""
    path = Path(config_path).resolve()

    if not path.exists():
        print(f"Error: Config file not found: {path}")
        sys.exit(1)

    try:
        with open(path, "r", encoding="utf-8") as f:
            config = json.load(f)
    except json.JSONDecodeError as exc:
        print(f"Error: Invalid JSON in config file: {exc}")
        sys.exit(1)

    # video_path is required for the full pipeline unless supplied via --input
    if not ocr_only and "video_path" not in config and not input_override:
        print("Error: 'video_path' is required in config (or pass --input <video>) for the full pipeline")
        sys.exit(1)

    if "match_keywords" not in config:
        print("Error: 'match_keywords' is required in config")
        sys.exit(1)

    return config


def _resolve_video_inputs(input_path: str) -> list[Path]:
    """Return one video path, or supported videos directly inside a directory."""
    path = Path(input_path).expanduser().resolve()

    if not path.exists():
        raise PipelineError(f"Input path not found: {path}")

    if path.is_dir():
        videos = sorted(
            p for p in path.iterdir()
            if p.is_file() and p.suffix.lower() in _SUPPORTED_VIDEO_EXTS
        )
        if not videos:
            raise PipelineError(
                f"No supported video files found in directory: {path}\n"
                f"Supported extensions: {', '.join(sorted(_SUPPORTED_VIDEO_EXTS))}"
            )
        return videos

    return [path]


def _batch_output_dir(base_output: Path, video_path: Path, used_names: set[str]) -> Path:
    """Return a deterministic per-video output directory under base_output."""
    stem = video_path.stem.strip() or "video"
    name = stem
    counter = 2

    while name in used_names:
        name = f"{stem}_{counter}"
        counter += 1

    used_names.add(name)
    return base_output / name


_CONFIG_HELP = """
config file reference:
  Required keys:
        video_path               string   Path to the input video file or directory
                                      (not required when using --ocr-only)
    match_keywords           list     Keywords to search for in OCR text
                                      e.g. ["FII", "Sethi", "Jain"]

  Optional keys:
    mode                     string   "accurate" | "context"
                                      default: "accurate"
                                      - accurate: multi-language OCR (incl. Hindi),
                                                  no context expansion
                                      - context : English-only OCR (forced),
                                                  each match spawns a ±N frame
                                                  window copied as ctx_*.png
    context_mode             object   {"frames_before": 5, "frames_after": 5}
                                      how many neighbors on each side of an
                                      anchor to copy in context mode
    frame_interval_seconds   int      Seconds between captured frames
                                      default: 2
    languages                list     OCR language codes
                                      e.g. ["en"] or ["hi", "en"]
                                      IGNORED in context mode (forced to ["en"])
    ocr_engine               string   "auto" | "apple_vision" | "windows_media_ocr"
                                      | "rapidocr" | "easyocr" | "composite"
                                      default: "auto"  (OS-aware: macOS→apple_vision,
                                      Windows→windows_media_ocr, Linux→rapidocr,
                                      composite for mixed Latin+Indic)
    match_mode               string   "contains" | "exact" | "regex"
                                      default: "contains"
    output_directory         string   Base output path
                                      default: "./output"
    log_directory            string   Log file destination
                                      default: "./logs"

  ocr_config (nested object):
    apple_vision_workers     int      Parallel processes for Apple Vision OCR
                                      default: 4
    recognition_level        string   "accurate" | "fast"
                                      default: "accurate"
    use_language_correction  bool     Apply language correction heuristics
                                      default: false
    easyocr_gpu              bool     Use GPU acceleration for EasyOCR
                                      default: false
    easyocr_confidence_threshold
                             float    Minimum confidence score (0.0-1.0)
                                      default: 0.3
    ocr_workers              int      Worker threads for OCR dispatch
                                      default: 1

examples:
  python main.py                                        run with default config
  python main.py ./config/config.json                   run with explicit config
  python main.py --input ./video.mp4 --output ./out     override input/output via args
    python main.py --input ./input_videos --output ./out  process every video in directory,
                                                                                                                one by one, into ./out/<video>/
  python main.py --video-path ./video.mp4               same as --input (alias)
  python main.py --ocr-only                             skip extraction, OCR existing frames
  python main.py --ocr-only --frames-dir ./my_frames    OCR frames from a custom directory
  python main.py --mode context                         English-only OCR + ±5 context window
  python main.py --mode context --context 3             English-only OCR + ±3 context window
  python main.py --mode context --context-before 2 --context-after 8
                                                        asymmetric context window
  python main.py --video-path ./video.mp4 --mode context --context 3
                                                        run a specific video in context mode
"""


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        prog="localocr",
        description="LOCALOCR - Local Video Screen OCR Pipeline for macOS",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_CONFIG_HELP,
    )
    parser.add_argument(
        "config",
        nargs="?",
        default="./config/config.json",
        help="Path to JSON config file (default: ./config/config.json)",
    )
    parser.add_argument(
        "--ocr-only",
        action="store_true",
        help="Skip video extraction and run OCR on already-extracted frames",
    )
    parser.add_argument(
        "--frames-dir",
        metavar="PATH",
        default=None,
        help="Directory containing pre-extracted frames (default: <output_directory>/all_frames)",
    )
    parser.add_argument(
        "--input", "--video-path",
        dest="input",
        metavar="VIDEO",
        default=None,
        help="Path to input video file (overrides config video_path). "
             "Alias: --video-path.",
    )
    parser.add_argument(
        "--output",
        metavar="DIR",
        default=None,
        help="Base output directory (overrides config output_directory)",
    )
    parser.add_argument(
        "--mode",
        choices=("accurate", "context"),
        default=None,
        help=(
            "Pipeline mode: 'accurate' (default; multi-language OCR incl. Hindi, "
            "no context expansion) or 'context' (English-only OCR + ±N context "
            "window). Overrides config 'mode' when passed."
        ),
    )
    parser.add_argument(
        "--context-before",
        type=int,
        metavar="N",
        default=None,
        help="Frames BEFORE each anchor to include as context (context mode only). "
             "Overrides context_mode.frames_before.",
    )
    parser.add_argument(
        "--context-after",
        type=int,
        metavar="N",
        default=None,
        help="Frames AFTER each anchor to include as context (context mode only). "
             "Overrides context_mode.frames_after.",
    )
    parser.add_argument(
        "--context",
        type=int,
        metavar="N",
        default=None,
        help="Shorthand for --context-before N --context-after N (context mode only).",
    )
    args = parser.parse_args()

    mode_label = "OCR-Only Mode" if args.ocr_only else "Full Pipeline"
    print(f"LOCALOCR - Local Video Screen OCR Pipeline  [{mode_label}]")
    print(f"Config: {args.config}")
    if args.ocr_only:
        print(f"Frames: {args.frames_dir or '<output_directory>/all_frames (default)'}")
    print()

    # Load config
    config = load_config(args.config, ocr_only=args.ocr_only, input_override=args.input)

    # CLI arg overrides (take priority over config file values)
    if args.input:
        config["video_path"] = str(Path(args.input).resolve())
    if args.output:
        config["output_directory"] = str(Path(args.output).resolve())
    if args.mode:
        config["mode"] = args.mode

    # Context-window overrides (only meaningful in context mode, but we apply
    # them unconditionally so the config reflects what the user asked for).
    ctx_cfg = config.setdefault("context_mode", {})
    if args.context is not None:
        ctx_cfg["frames_before"] = args.context
        ctx_cfg["frames_after"] = args.context
    if args.context_before is not None:
        ctx_cfg["frames_before"] = args.context_before
    if args.context_after is not None:
        ctx_cfg["frames_after"] = args.context_after

    # Setup logging
    log_file = setup_logging(config.get("log_directory", "./logs"))
    print(f"Logs: {log_file}")
    print()

    logger = logging.getLogger(__name__)

    try:
        if args.ocr_only:
            summary = run_ocr_only_pipeline(config, frames_dir=args.frames_dir)
            print()
            print("OCR-only pipeline completed successfully!")
            print(f"  Mode: {summary.get('mode', 'accurate')}")
            print(f"  Frames directory: {summary['frames_dir']}")
            print(f"  Total frames processed: {summary['total_frames']}")
            print(f"  Matched frames: {summary['matched_frames']}")
            if summary.get("context_frames"):
                print(f"  Context frames: {summary['context_frames']}")
            print(f"  Categories: {summary['categories']}")
            print(f"  Processing time: {summary['processing_time_seconds']}s")
            print(f"  Metadata: {summary['metadata_file']}")
        else:
            input_source = Path(config["video_path"]).expanduser().resolve()
            videos = _resolve_video_inputs(config["video_path"])

            if not input_source.is_dir():
                config["video_path"] = str(videos[0])
                summary = run_pipeline(config)
                print()
                print("Pipeline completed successfully!")
                print(f"  Mode: {summary.get('mode', 'accurate')}")
                print(f"  Total frames extracted: {summary['total_frames']}")
                print(f"  Matched frames: {summary['matched_frames']}")
                if summary.get("context_frames"):
                    print(f"  Context frames: {summary['context_frames']}")
                print(f"  Categories: {summary['categories']}")
                print(f"  Processing time: {summary['processing_time_seconds']}s")
                print(f"  Metadata: {summary['metadata_file']}")
            else:
                base_output = Path(config.get("output_directory", "./output")).expanduser().resolve()
                used_output_names: set[str] = set()
                summaries = []

                print(f"Batch mode: found {len(videos)} video(s) in {input_source}")
                print(f"Base output: {base_output}")

                for index, video in enumerate(videos, 1):
                    video_config = deepcopy(config)
                    video_output = _batch_output_dir(base_output, video, used_output_names)
                    video_config["video_path"] = str(video)
                    video_config["output_directory"] = str(video_output)

                    print()
                    print(f"[{index}/{len(videos)}] Processing: {video.name}")
                    print(f"  Output: {video_output}")

                    try:
                        summary = run_pipeline(video_config)
                    except PipelineError as exc:
                        raise PipelineError(
                            f"Batch item {index}/{len(videos)} failed for {video.name}: {exc}"
                        ) from exc

                    summaries.append(summary)
                    print(
                        f"  Done: {summary['total_frames']} frames, "
                        f"{summary['matched_frames']} matched, "
                        f"{summary['processing_time_seconds']}s"
                    )

                total_frames = sum(s["total_frames"] for s in summaries)
                total_matches = sum(s["matched_frames"] for s in summaries)
                total_context = sum(s.get("context_frames", 0) for s in summaries)
                total_seconds = round(sum(s["processing_time_seconds"] for s in summaries), 2)
                categories: dict[str, int] = {}
                for summary in summaries:
                    for category, count in summary["categories"].items():
                        categories[category] = categories.get(category, 0) + count

                print()
                print("Batch pipeline completed successfully!")
                print(f"  Videos processed: {len(summaries)}")
                print(f"  Mode: {summaries[0].get('mode', 'accurate') if summaries else config.get('mode', 'accurate')}")
                print(f"  Total frames extracted: {total_frames}")
                print(f"  Matched frames: {total_matches}")
                if total_context:
                    print(f"  Context frames: {total_context}")
                print(f"  Categories: {categories}")
                print(f"  Processing time: {total_seconds}s")
                print(f"  Output root: {base_output}")
    except PipelineError as exc:
        logger.error("Pipeline failed: %s", exc)
        print(f"\nError: {exc}")
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Pipeline interrupted by user")
        print("\nInterrupted.")
        sys.exit(130)


if __name__ == "__main__":
    main()
