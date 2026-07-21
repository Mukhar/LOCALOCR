"""Server-Sent Events endpoint for live run progress.

Two routes:
  GET /runs/{id}/progress -- initial HTML panel (rendered once when the
                             detail page loads; contains the sse-connect
                             attribute that hands off to the browser's
                             EventSource + HTMX SSE extension).
  GET /runs/{id}/events   -- SSE stream. Emits one message per event
                             the RunManager publishes to the event bus.
                             JSON payload; the client-side JS in
                             run_progress.html parses and applies it.

The SSE stream terminates when the run's `done` event is published --
event_bus.subscribe() itself yields that terminal event and then stops.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import AsyncIterator, Dict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sse_starlette.sse import EventSourceResponse

from ..services.db import get_run
from ..services.event_bus import event_bus

router = APIRouter()

_TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


async def _sse_generator(request: Request, run_id: int) -> AsyncIterator[Dict[str, str]]:
    """Yield SSE messages for one subscriber.

    Contract with sse_starlette: yield dicts with keys ``event`` and
    ``data``. We forward the ORIGINAL event type as the SSE event
    name (so hypothetical typed listeners work) AND stringify the
    whole event as JSON in ``data`` (so a single ``htmx:sseMessage``
    listener can dispatch on ``type`` client-side).

    We poll ``request.is_disconnected()`` between events so a client
    that closes their tab frees the pump-side queue promptly. The
    ``subscribe`` iterator ALSO cleans up on cancel via its finally
    block, so this is defense in depth.
    """
    async for event in event_bus.subscribe(run_id):
        if await request.is_disconnected():
            break
        yield {
            "event": event.get("type", "message"),
            "data": json.dumps(event, ensure_ascii=False),
        }


@router.get("/runs/{run_id}/events")
async def stream_events(request: Request, run_id: int):
    """Open an SSE stream for one run. Client keeps the connection
    open; sse_starlette handles the heartbeat + framing."""
    return EventSourceResponse(_sse_generator(request, run_id))


@router.get("/runs/{run_id}/progress", response_class=HTMLResponse)
def progress_panel(request: Request, run_id: int):
    """Render the initial progress panel HTML.

    Loaded eagerly by run_detail.html via ``hx-get`` on page load. Once
    swapped in, the sse-connect attribute on the panel activates HTMX's
    SSE extension which opens the stream.
    """
    db_path: Path = request.app.state.db_path
    run = get_run(db_path, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return templates.TemplateResponse(
        request, "run_progress.html", {"run": run},
    )
