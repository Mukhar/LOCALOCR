# Phase 03 Plan 03 Summary — Routes + templates

**Status:** Complete
**Tests:** 22 (all green)
**Commit:** `43fea7f`

## Delivered

### Templates (Walmart palette via `cp_walmart_colors` skill, XSS-safe)
- `base.html` — Tailwind + HTMX CDN, dark-theme surface tokens, skip-to-content link, focus-visible ring, button/badge primitives
- `runs_list.html` — Semantic `<table>` with `<caption class="sr-only">`, HTMX-powered delete
- `run_new.html` — `<form>` with `<fieldset>/<legend>`, video + config profile selects
- `run_detail.html` — Run header, video player, picks grid, progress placeholder
- `_pick_card.html` — Reusable partial with `data-seek` attribute + custom `from_json` Jinja filter for transcript context

### Routes
- `runs.py` — `GET /`, `GET /runs`, `GET /runs/new`, `POST /runs`, `GET /runs/{id}`, `DELETE /runs/{id}`
- `videos.py` — `GET /videos`, `POST /videos/upload` (500 MB cap + path-traversal defense + extension allowlist), `GET /videos/{name}` (byte-range enabled)

## Decisions
- `_CONFIG_PROFILES` hard-coded map (`default`, `transcript`) — less magic than dir scan, less risk than accepting arbitrary paths
- Uses new `TemplateResponse(request, name)` Starlette signature (no deprecation warnings)
- Activated `cp_walmart_colors` skill without prompting — Walmart system rules mandate it for any UI work

## XSS regression fenced
`test_run_detail_autoescapes_malicious_pick` injects `<script>alert(1)</script>` + `<img src=x onerror=alert(2)>` as `stockPick` / `analyst` — verifies both render as escaped entities and executable forms are absent.
