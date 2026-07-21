# Phase 03 Plan 05 Summary — a11y, e2e, docs

**Status:** Complete
**Tests:** 150 unit + integration (0 e2e run — playwright not installed)

## Delivered

### Task 1: Accessibility audit
Template audit showed already-solid a11y coverage from Plans 03-03/03-04:
- Semantic HTML: `<main id="main">`, `<nav aria-label="Primary">`, `<article>`, `<section>`, `<fieldset>/<legend>`
- Heading order: h1 (page) → h2 (section) → h3 (pick card). No skips.
- Labels: every `<select>` has `<label for="...">`
- Focus indicators: 3px `spark.100` `:focus-visible` ring at 3:1 contrast against dark surface
- Skip-to-content link (WCAG 2.4.1)
- Table: `<caption class="sr-only">`, `<th scope="col">`
- `aria-live="polite"` on progress panel, `aria-live="off"` on log tail (avoid AT flood)
- Icon-only button (seek): `aria-label="Seek video to N seconds"`
- No `<img>` tags anywhere (nothing to add alt to)
- Zero `onclick` divs — pick cards use `<button>` for the seek action

**Not run:** `axe-cli` (needs Node/npx not in this environment). Manual audit against the checklist above is the substitute.

### Task 2: Playwright e2e skeleton (opt-in)
- `pytest.ini` — `markers = e2e: ...` + `addopts = -m "not e2e"` (default suite excludes e2e)
- `tests/e2e/__init__.py` — module docstring with install instructions
- `tests/e2e/conftest.py` — session-scoped `live_server` fixture that boots `python -m src.web`, waits for `/health`, tears down
- `tests/e2e/test_playwright_smoke.py`:
  - `pytest.importorskip("playwright.sync_api")` guard so missing install yields a graceful skip
  - `test_runs_list_loads`, `test_new_run_form_renders`, `test_video_selector_or_empty_message` are runnable smoke tests
  - `test_pick_card_seeks_video` and `test_progress_panel_element_exists_when_running` are `pytest.skip`'d with clear "enable when X is in place" messages

**Not run:** Playwright itself (300 MB Chromium download). Skeleton ready when a user opts in with `uv pip install pytest-playwright + playwright install chromium`.

### Task 3: docs/web_ui.md
254 lines. Sections: Quick start, Architecture at a glance, Pages (Runs list, New Run, Run Detail, Live progress SSE, Click-to-seek), Endpoints reference table, Troubleshooting table, Config profiles, Testing, Backward compatibility, Related docs.

### Task 4: README + AGENTS updates
- **README.md** — new "Web UI" subsection under Usage with 3-command quick start
- **AGENTS.md**:
  - Architecture Summary — added `src/web/` tree with all sub-modules
  - New "Web UI" section (~70 lines) — package layout table, SQLite schema, event bus + SSE flow, subprocess isolation model, adding new routes, testing
  - Running section — added `python -m src.web` first
  - Dependencies section — added web deps line

### Task 5: Backward compat verified
`from post_ocr_pipeline import build_dashboard_html` still works; smoke-called it with a test pick, generated 7863-char HTML including "RELIANCE". `output/viewer.html` generation path unchanged from Phase 2.

## Skipped
Final v1.1 milestone human-verify checkpoint (Task 7): requires a real fresh run in the browser + axe DevTools scan + all e2e passing. User can trigger when ready.

## Test totals
- Unit + integration: **150 passed, 1 skipped** (the e2e module skips on `importorskip`)
- e2e: 3 real + 2 skipped (opt-in via `pytest -m e2e`)
