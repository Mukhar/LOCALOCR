"""Videos routes: list input videos, upload new ones, stream for playback.

Path-traversal defense: every filename that comes from the client goes
through ``Path(name).name`` which strips any leading directory components
(``../etc/passwd`` -> ``passwd``). Combined with an extension allowlist
and a size cap on uploads, this keeps ``input_videos/`` from becoming
an attacker's dumping ground.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse

router = APIRouter()

_INPUT_DIR = Path("input_videos").resolve()
_SUPPORTED = {".mp4", ".webm", ".mkv", ".mov", ".m4v", ".avi"}
_MAX_UPLOAD_MB = 500
_MAX_UPLOAD_BYTES = _MAX_UPLOAD_MB * 1024 * 1024


def _list_input_videos() -> List[dict]:
    """Return sorted list of playable video files in the input directory.

    Missing directory -> empty list (not an error). Non-file entries and
    unsupported extensions are silently filtered out.
    """
    if not _INPUT_DIR.exists():
        return []
    return [
        {"name": p.name, "path": str(p)}
        for p in sorted(_INPUT_DIR.iterdir())
        if p.is_file() and p.suffix.lower() in _SUPPORTED
    ]


@router.get("/videos")
def videos_list():
    """JSON list of playable input videos. Used by the /runs/new form."""
    return _list_input_videos()


@router.post("/videos/upload")
async def upload_video(file: UploadFile = File(...)):
    """Accept a multipart upload with an extension allowlist and size cap.

    Two defenses:
      - ``Path(file.filename).name`` strips any directory components
        (``../etc/passwd`` -> ``passwd``), then we compare against the
        extension allowlist.
      - Chunked read with a running byte counter: bail + unlink the
        partial file the moment we cross ``_MAX_UPLOAD_BYTES``. No
        reliance on Content-Length headers.
    """
    raw_name = file.filename or "upload.mp4"
    safe_name = Path(raw_name).name  # strip traversal
    if Path(safe_name).suffix.lower() not in _SUPPORTED:
        raise HTTPException(400, f"Unsupported extension: {safe_name}")

    _INPUT_DIR.mkdir(parents=True, exist_ok=True)
    dest = _INPUT_DIR / safe_name

    written = 0
    try:
        with dest.open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)  # 1 MiB per read
                if not chunk:
                    break
                written += len(chunk)
                if written > _MAX_UPLOAD_BYTES:
                    out.close()
                    dest.unlink(missing_ok=True)
                    raise HTTPException(
                        413,
                        f"Upload exceeds {_MAX_UPLOAD_MB} MB cap",
                    )
                out.write(chunk)
    except HTTPException:
        raise
    except Exception as exc:
        dest.unlink(missing_ok=True)
        raise HTTPException(500, f"Upload failed: {exc}") from exc

    return JSONResponse({"name": safe_name, "size": written})


@router.get("/videos/{name}")
def video_stream(name: str):
    """Serve a video file with automatic HTTP byte-range support.

    FastAPI's ``FileResponse`` returns ``Accept-Ranges: bytes`` and
    handles ``Range: bytes=...`` requests transparently -- exactly
    what the ``<video>`` element needs for seek.
    """
    safe_name = Path(name).name  # strip any path traversal
    path = _INPUT_DIR / safe_name
    if not path.is_file():
        raise HTTPException(404, "Video not found")
    return FileResponse(path)
