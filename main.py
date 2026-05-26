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


def load_config(config_path: str, ocr_only: bool = False) -> dict:
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

    # video_path is only required for the full pipeline
    if not ocr_only and "video_path" not in config:
        print("Error: 'video_path' is required in config for the full pipeline")
        sys.exit(1)

    if "match_keywords" not in config:
        print("Error: 'match_keywords' is required in config")
        sys.exit(1)

    return config


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        prog="localocr",
        description="LOCALOCR - Local Video Screen OCR Pipeline for macOS",
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
    args = parser.parse_args()

    mode_label = "OCR-Only Mode" if args.ocr_only else "Full Pipeline"
    print(f"LOCALOCR - Local Video Screen OCR Pipeline  [{mode_label}]")
    print(f"Config: {args.config}")
    if args.ocr_only:
        print(f"Frames: {args.frames_dir or '<output_directory>/all_frames (default)'}")
    print()

    # Load config
    config = load_config(args.config, ocr_only=args.ocr_only)

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
