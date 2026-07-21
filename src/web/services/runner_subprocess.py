"""Entry point for pipeline subprocesses spawned by :class:`RunManager`.

Contract:
  - Emits a stream of structured JSON events, one per line, to stdout.
    Each line is either a `type: log` (root-logger record) or a lifecycle
    event (`start`, `summary`, `error`, `done`).
  - The parent process (RunManager._pump) parses these lines and
    republishes them to the event bus + persists lifecycle transitions
    to the DB.
  - EVERY exit path emits `{"type": "done", "status": ...}` -- the parent
    depends on that terminal event to finalize the DB row and free the
    pump thread. If we get killed hard (SIGKILL), the pump falls back to
    proc.wait() returncode + a synthetic `done` (see RunManager._pump).

Called via:
    python -m src.web.services.runner_subprocess \\
        --run-id 42 --config '<json-encoded-config>'

Kept intentionally small -- no policy, no retry, no orchestration. That
all lives one level up in RunManager where it's testable without a real
subprocess.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from src.pipeline.pipeline_runner import run_pipeline


class StructuredEventHandler(logging.Handler):
    """Emit each log record as one JSON line to stdout.

    We keep the format flat: `{type, level, message, logger}` so the
    parent doesn't need to know log-record internals. `record.name` is
    the logger name (e.g. `src.pipeline.pipeline_runner`) which lets the
    UI group by pipeline phase.
    """

    def __init__(self, run_id: int):
        super().__init__()
        self.run_id = run_id

    def emit(self, record: logging.LogRecord) -> None:
        event = {
            "type": "log",
            "level": record.levelname,
            "message": self.format(record),
            "logger": record.name,
        }
        sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def _emit(event: dict) -> None:
    """Write one JSON line to stdout and flush immediately.

    Flushing per line matters: the parent reads line-by-line with
    ``for line in proc.stdout``, and CPython's default line-buffered
    behavior on pipes is unreliable enough that explicit flushes are
    the belt-and-suspenders play.
    """
    sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Subprocess entry point for LOCALOCR web-UI pipeline runs.",
    )
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--config", type=str, required=True,
                        help="JSON-encoded config dict")
    args = parser.parse_args()

    # Install structured logging BEFORE parsing config so any early
    # errors also flow through the event stream.
    handler = StructuredEventHandler(args.run_id)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)

    try:
        config = json.loads(args.config)
    except json.JSONDecodeError as exc:
        _emit({"type": "error", "message": f"invalid config JSON: {exc}"})
        _emit({"type": "done", "status": "failed"})
        return 2

    _emit({"type": "start", "run_id": args.run_id,
           "video_path": config.get("video_path", "")})

    try:
        summary = run_pipeline(config)
        _emit({"type": "summary", "summary": summary})
        _emit({"type": "done", "status": "completed"})
        return 0
    except KeyboardInterrupt:
        # Graceful shutdown path (SIGINT from parent via terminate()).
        _emit({"type": "done", "status": "interrupted"})
        return 130
    except Exception as exc:  # noqa: BLE001 -- boundary catch-all
        # Never let a pipeline exception bubble out unformatted. The
        # parent MUST see a terminal `done` event to finalize the DB.
        _emit({"type": "error", "message": str(exc),
               "exception": type(exc).__name__})
        _emit({"type": "done", "status": "failed"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
