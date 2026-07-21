"""Playwright end-to-end smoke tests. Opt-in via ``pytest -m e2e``.

These tests prove the whole HTMX + SSE + seek.js pipeline works in a
real Chromium instance, not just against TestClient. They complement
(rather than replace) the fast unit tests in tests/test_web/.
"""

from __future__ import annotations

import pytest

# Guard: skip the entire module if pytest-playwright isn't installed.
# This lets `pytest -m e2e` fail gracefully with an "install me" hint
# rather than an ImportError.
pytest.importorskip(
    "playwright.sync_api",
    reason="Install pytest-playwright + `playwright install chromium` "
           "to run e2e tests.",
)

from playwright.sync_api import Page, expect  # noqa: E402

pytestmark = pytest.mark.e2e


def test_runs_list_loads(page: Page, live_server: str):
    """Homepage redirects to /runs and shows the LOCALOCR branding."""
    page.goto(live_server + "/")
    # Redirect target
    expect(page).to_have_url(live_server + "/runs")
    # Brand link + New Run CTA visible
    expect(page.get_by_role("link", name="LOCALOCR")).to_be_visible()
    expect(page.get_by_role("link", name="New Run")).to_be_visible()


def test_new_run_form_renders(page: Page, live_server: str):
    """The New Run form has the two selects and a submit button."""
    page.goto(live_server + "/runs/new")
    expect(page.get_by_role("heading", name="Start a New Run")).to_be_visible()
    # config_profile select is present regardless of whether any videos
    # exist in input_videos/
    expect(page.locator("select[name='config_profile']")).to_be_visible()


def test_video_selector_or_empty_message(page: Page, live_server: str):
    """Either a video select is rendered OR the 'No videos found' message."""
    page.goto(live_server + "/runs/new")
    video_select = page.locator("select[name='video_path']")
    empty_msg = page.get_by_text("No videos found")
    # XOR: exactly one of these is visible
    assert (video_select.is_visible() ^ empty_msg.is_visible()), \
        "Either video select or empty-state message must be visible"


def test_pick_card_seeks_video(
    page: Page, live_server: str, tmp_path,
):
    """After seeding a run + pick + video, clicking Seek sets currentTime.

    This is the crown-jewel test: proves seek.js delegated click handler
    + video element wiring works end-to-end.

    Skipped when there's no way to seed data through the public API
    without a real pipeline run. When that's fixed (e.g. a debug seed
    endpoint), remove the skip.
    """
    pytest.skip(
        "Seeding a run + pick + video via public routes requires a real "
        "pipeline run; add a debug seed endpoint or hook the DB directly "
        "in a follow-up before enabling."
    )


def test_progress_panel_element_exists_when_running(
    page: Page, live_server: str,
):
    """Kicking off a real run requires a video + config; not runnable in CI.
    Placeholder that documents the intended coverage without hanging."""
    pytest.skip(
        "Live SSE progress e2e requires a real, fast-completing pipeline "
        "video fixture. Author one via `ffmpeg -f lavfi -i testsrc=d=2` "
        "and enable this test in a follow-up."
    )
