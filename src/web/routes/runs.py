"""Runs routes: browse, create, view, delete pipeline runs.

Keeps route bodies thin -- all logic lives in ``services.db`` and
``services.run_manager``. Templates render with autoescape on so any
LLM-derived string in a pick renders as inert text.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..services.db import delete_run, get_run, list_picks, list_runs
from ..services.run_manager import get_run_manager

router = APIRouter()

# Templates directory + a custom ``from_json`` filter used by _pick_card
# to decode the transcript_context blob at render time.
_TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def _from_json(value):
    """Jinja filter: decode a JSON string, or return the value unchanged.

    Used by _pick_card.html to unpack pick.transcript_context. Returns
    ``None`` on parse errors so the template's `if ctx and ...` guard
    hides the section cleanly.
    """
    if value is None or value == "":
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


templates.env.filters["from_json"] = _from_json


# --- Route handlers --------------------------------------------------------

@router.get("/", include_in_schema=False)
def root():
    """Root convenience redirect to the runs list."""
    return RedirectResponse(url="/runs", status_code=307)


@router.get("/runs", response_class=HTMLResponse)
def runs_list_view(request: Request):
    """List all runs, most-recent first."""
    db_path: Path = request.app.state.db_path
    runs = list_runs(db_path)
    return templates.TemplateResponse(
        request, "runs_list.html", {"runs": runs},
    )


@router.get("/runs/new", response_class=HTMLResponse)
def run_new_form(request: Request):
    """Render the 'start a new run' form."""
    # Local import to avoid a circular init (videos router imports us
    # transitively via create_app in some layouts).
    from .videos import _list_input_videos
    return templates.TemplateResponse(
        request, "run_new.html",
        {"videos": _list_input_videos()},
    )


# Config profile -> file map. Kept small and hard-coded on purpose:
# any user who wants a bespoke config can drop their own file in and
# add an entry here. That's less magic than scanning a directory and
# far less risk than accepting arbitrary paths from the client.
_CONFIG_PROFILES = {
    "default":    "config/config.json",
    "transcript": "config/config.transcript.example.json",
}


@router.post("/runs")
def run_new_submit(
    request: Request,
    video_path: str = Form(...),
    config_profile: str = Form("default"),
):
    """Kick off a new run and redirect to its detail page."""
    db_path: Path = request.app.state.db_path

    cfg_file = _CONFIG_PROFILES.get(config_profile, _CONFIG_PROFILES["default"])
    cfg_path = Path(cfg_file)
    if not cfg_path.is_file():
        raise HTTPException(
            status_code=500,
            detail=f"Config profile file missing: {cfg_file}",
        )
    with cfg_path.open("r", encoding="utf-8") as f:
        config = json.load(f)

    # Override the config's video_path with the user's selection.
    # This is the ONLY user-controlled write into the config, keeping
    # the input surface small.
    config["video_path"] = video_path

    manager = get_run_manager(db_path)
    run_id = manager.start_run(config)
    # 303 See Other: POST -> GET redirect per HTTP semantics
    return RedirectResponse(url=f"/runs/{run_id}", status_code=303)


@router.get("/runs/{run_id}", response_class=HTMLResponse)
def run_detail_view(request: Request, run_id: int):
    """Render a single run's detail page + its picks."""
    db_path: Path = request.app.state.db_path
    run = get_run(db_path, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    picks = list_picks(db_path, run_id)
    return templates.TemplateResponse(
        request, "run_detail.html",
        {"run": run, "picks": picks},
    )


@router.delete("/runs/{run_id}", response_class=HTMLResponse)
def run_delete_view(request: Request, run_id: int):
    """Delete a run + its picks. Returns empty body so HTMX removes the row."""
    db_path: Path = request.app.state.db_path
    delete_run(db_path, run_id)
    return HTMLResponse("", status_code=200)
