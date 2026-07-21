"""End-to-end Playwright smoke tests for the LOCALOCR web UI.

Opt-in: marked with ``@pytest.mark.e2e`` and excluded from the default
test run via ``pytest.ini`` addopts. Run explicitly with:

    uv pip install pytest-playwright --index-url <walmart index>
    playwright install chromium
    pytest -m e2e tests/e2e/ -v

These tests are the belt to the unit tests' suspenders -- they prove
the whole stack (FastAPI + Jinja + HTMX + SSE + seek.js) works in a
real browser, not just in TestClient.
"""
