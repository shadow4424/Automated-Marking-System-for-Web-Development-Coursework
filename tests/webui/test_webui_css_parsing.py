from __future__ import annotations

from pathlib import Path

import pytest

playwright_sync_api = pytest.importorskip("playwright.sync_api")
PlaywrightError = playwright_sync_api.Error
Page = playwright_sync_api.Page
sync_playwright = playwright_sync_api.sync_playwright


REPO_ROOT = Path(__file__).resolve().parents[2]

CSS_BASE_SELECTOR_CASES = [
    ("ams/static/css/components/cards.css", ".card"),
    ("ams/static/css/components/tables.css", ".table-container"),
    ("ams/static/css/components/layout.css", ".toolbar"),
    ("ams/static/css/components/tabs.css", ".detail-tab-nav"),
    ("ams/static/css/components/forms.css", ".form-group"),
    ("ams/static/css/components/alerts.css", ".alert"),
    ("ams/static/css/components/findings.css", "details"),
    ("ams/static/css/components/data-display.css", ".stat-card"),
]


def _launch_cssom_browser():
    playwright = sync_playwright().start()
    attempts: list[str] = []

    try:
        for label, launch in (
            ("bundled chromium", lambda: playwright.chromium.launch()),
            ("Microsoft Edge", lambda: playwright.chromium.launch(channel="msedge")),
            ("Google Chrome", lambda: playwright.chromium.launch(channel="chrome")),
        ):
            try:
                browser = launch()
                return playwright, browser
            except PlaywrightError as exc:
                attempts.append(f"{label}: {exc}")
    except Exception:
        playwright.stop()
        raise

    playwright.stop()
    pytest.skip(
        "No Playwright Chromium browser was available for CSS parsing regression checks: "
        + " | ".join(attempts)
    )


@pytest.fixture(scope="module")
def cssom_page() -> Page:
    playwright, browser = _launch_cssom_browser()
    page = browser.new_page()
    page.set_content("<!doctype html><html><head></head><body></body></html>")

    try:
        yield page
    finally:
        browser.close()
        playwright.stop()


def _parsed_selectors(page: Page, css_text: str) -> list[str]:
    return page.evaluate(
        """
        (source) => {
          const style = document.createElement("style");
          style.textContent = source;
          document.head.appendChild(style);
          const selectors = Array.from(style.sheet.cssRules)
            .map((rule) => rule.selectorText || null)
            .filter(Boolean);
          style.remove();
          return selectors;
        }
        """,
        css_text,
    )


@pytest.mark.parametrize("relative_path, expected_selector", CSS_BASE_SELECTOR_CASES)
def test_shared_css_base_selector_is_first_parsed_rule(
    cssom_page: Page,
    relative_path: str,
    expected_selector: str,
) -> None:
    css_path = REPO_ROOT / relative_path
    css_text = css_path.read_text(encoding="utf-8")

    selectors = _parsed_selectors(cssom_page, css_text)

    assert selectors, f"No CSS selectors were parsed from {relative_path}"
    assert selectors[0] == expected_selector, (
        f"Expected the first parsed selector in {relative_path} to be "
        f"{expected_selector!r}, got {selectors[0]!r}"
    )
    assert expected_selector in selectors
