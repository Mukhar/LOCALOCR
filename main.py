#!/usr/bin/env python3
"""
LOCALOCR - Local Video Screen OCR Pipeline for macOS
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Main entry point. Run with: python main.py [config_path]
"""

import argparse
import json
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from src.pipeline import run_pipeline, run_ocr_only_pipeline, PipelineError


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


_CONFIG_HELP = """
config file reference:
  Required keys:
    video_path               string   Path to the input video file
                                      (not required when using --ocr-only)
    match_keywords           list     Keywords to search for in OCR text
                                      e.g. ["FII", "Sethi", "Jain"]

  Optional keys:
    frame_interval_seconds   int      Seconds between captured frames
                                      default: 2
    languages                list     OCR language codes
                                      e.g. ["en"] or ["hi", "en"]
    ocr_engine               string   "auto" | "apple_vision" | "easyocr" | "composite"
                                      default: "auto"  (apple_vision for en-only, composite for others)
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
  python main.py --ocr-only                             skip extraction, OCR existing frames
  python main.py --ocr-only --frames-dir ./my_frames    OCR frames from a custom directory
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
        "--input",
        metavar="VIDEO",
        default=None,
        help="Path to input video file (overrides config video_path)",
    )
    parser.add_argument(
        "--output",
        metavar="DIR",
        default=None,
        help="Base output directory (overrides config output_directory)",
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
            print(f"  Frames directory: {summary['frames_dir']}")
            print(f"  Total frames processed: {summary['total_frames']}")
            print(f"  Matched frames: {summary['matched_frames']}")
            print(f"  Categories: {summary['categories']}")
            print(f"  Processing time: {summary['processing_time_seconds']}s")
            print(f"  Metadata: {summary['metadata_file']}")
        else:
            summary = run_pipeline(config)
            print()
            print("Pipeline completed successfully!")
            print(f"  Total frames extracted: {summary['total_frames']}")
            print(f"  Matched frames: {summary['matched_frames']}")
            print(f"  Categories: {summary['categories']}")
            print(f"  Processing time: {summary['processing_time_seconds']}s")
            print(f"  Metadata: {summary['metadata_file']}")
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
