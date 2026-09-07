"""Implementation of the `crawl-render-audit` skill.

Headless-browser rendering via Playwright, when the optional `render`
extra (`pip install braiaudit[render]` + `playwright install chromium`) is
present. Without it, this module degrades exactly the way
skills/crawl-render-audit/SKILL.md's "Render budget / backend unavailable"
error-handling section specifies: it reports `available: false` rather
than raising, so the pipeline can add an honest coverage-gap finding
instead of crashing or silently skipping render-dependent checks.
"""

from __future__ import annotations

import logging
from typing import Any

from braiaudit.schemas import validate

try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    _PLAYWRIGHT_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only when the extra isn't installed
    _PLAYWRIGHT_AVAILABLE = False

_INTERACTION_SELECTORS = (
    "[role=tab]",
    "[aria-controls][aria-expanded='false']",
    "button:has-text('Load more')",
    "button:has-text('Show more')",
)

logger = logging.getLogger("braiaudit.render")

# Launching a browser is the step most likely to fail on a machine that has
# the Python package but an unusable browser install. Bounded so a bad
# environment degrades to a coverage gap instead of hanging the audit.
_LAUNCH_TIMEOUT_MS = 20000

_MAX_SCROLL_STEPS = 20
_MAX_INTERACTIONS = 10


def is_available() -> bool:
    """Whether the render backend is importable.

    Not a promise that a browser will actually launch — binaries can be
    missing or unusable even when the package imports cleanly. `render()`
    therefore treats any launch failure as a coverage gap rather than
    trusting this flag.
    """
    return _PLAYWRIGHT_AVAILABLE


def _unavailable(url: str, reason: str) -> dict[str, Any]:
    logger.warning("render backend unavailable for %s: %s", url, reason)
    result = {
        "url": url,
        "available": False,
        "render_timed_out": False,
        "signals": ["render_backend_unavailable"],
    }
    validate(result, "crawl-render-audit")
    return result


def render(
    url: str,
    pre_render_text_length: int | None = None,
    user_agent: str = "BrandAIReadinessAuditBot/1.0",
    max_wait_ms: int = 8000,
    drive_scroll: bool = True,
    drive_interactions: bool = True,
    traverse_shadow_dom: bool = True,
) -> dict[str, Any]:
    """Run the full crawl-render-audit pass for one URL.

    Returns a dict validated against
    schemas/crawl-render-audit.output.schema.json. When no render backend
    is installed, returns immediately with `available: False` and no
    further fields populated — callers must check `available` before
    trusting DOM metrics.
    """
    if not _PLAYWRIGHT_AVAILABLE:
        return _unavailable(url, "playwright is not installed")

    # Any browser-side failure — missing or broken binaries, a sandbox that
    # forbids launching one, a driver that never comes up — degrades to the
    # documented coverage gap. Rendering is an optional enhancement and is
    # never allowed to take the whole audit down with it.
    try:
        return _render_with_browser(
            url,
            pre_render_text_length,
            user_agent,
            max_wait_ms,
            drive_scroll,
            drive_interactions,
            traverse_shadow_dom,
        )
    except Exception as exc:  # noqa: BLE001 - degrade on anything the backend raises
        return _unavailable(url, f"{type(exc).__name__}: {exc}")


def _render_with_browser(
    url: str,
    pre_render_text_length: int | None,
    user_agent: str,
    max_wait_ms: int,
    drive_scroll: bool,
    drive_interactions: bool,
    traverse_shadow_dom: bool,
) -> dict[str, Any]:
    """The real render pass. Exiting the sync_playwright context tears the
    driver (and any browser it started) down, so a raise here cannot leak a
    browser process — the caller turns it into a coverage gap."""
    with sync_playwright() as p:
        browser = p.chromium.launch(timeout=_LAUNCH_TIMEOUT_MS)
        page = browser.new_page(user_agent=user_agent)
        render_timed_out = False
        try:
            page.goto(url, wait_until="networkidle", timeout=max_wait_ms)
        except PlaywrightTimeoutError:
            render_timed_out = True

        post_text = page.evaluate("document.body ? document.body.innerText.length : 0")

        scroll_triggered_nodes_found = 0
        if drive_scroll:
            scroll_triggered_nodes_found = _drive_scroll(page)

        interactions_performed: list[dict[str, Any]] = []
        if drive_interactions:
            interactions_performed = _drive_interactions(page)

        shadow_components, shadow_text = ([], 0)
        if traverse_shadow_dom:
            shadow_components, shadow_text = _traverse_shadow_dom(page)

        # Same-origin only. The field is called *internal* links and feeds
        # the crawl frontier, so leaking third-party hrefs here sends the
        # audit off to crawl someone else's site.
        discovered_links = page.evaluate(
            """
            () => Array.from(document.querySelectorAll('a[href]'))
              .map(a => a.href)
              .filter(href => {
                try {
                  const u = new URL(href, location.href);
                  return u.host === location.host
                    && (u.protocol === 'http:' || u.protocol === 'https:');
                } catch (e) { return false; }
              })
            """
        )
        nav_trap = page.evaluate(
            """
            () => ({
              onclickDivs: document.querySelectorAll('[onclick]:not(a)').length,
              jsVoidLinks: Array.from(document.querySelectorAll('a[href]')).filter(
                a => a.getAttribute('href').trim().toLowerCase().startsWith('javascript:')
              ).length,
            })
            """
        )
        rendered_html = page.content()
        browser.close()

    pre_len = pre_render_text_length or 0
    dom_diff_ratio = round(post_text / pre_len, 2) if pre_len else None

    signals = []
    if dom_diff_ratio is not None and dom_diff_ratio > 5:
        signals.append("app_shell_confirmed")
    if scroll_triggered_nodes_found > 0:
        signals.append("lazy_load_confirmed")
    if any(i.get("revealed_text_delta") for i in interactions_performed):
        signals.append("dynamic_interaction_confirmed")
    if shadow_components:
        signals.append("shadow_dom_encapsulation_confirmed")
    if nav_trap["onclickDivs"] > 0:
        signals.append("onclick_div_navigation")
    if nav_trap["jsVoidLinks"] > 0:
        signals.append("javascript_void_href")

    result = {
        "url": url,
        "available": True,
        "pre_render_text_length": pre_len or None,
        "post_render_text_length": post_text,
        "dom_diff_ratio": dom_diff_ratio,
        "render_timed_out": render_timed_out,
        "scroll_triggered_nodes_found": scroll_triggered_nodes_found,
        "interactions_performed": interactions_performed,
        "shadow_dom_components_found": shadow_components,
        "shadow_dom_text_recovered": shadow_text,
        "discovered_internal_links": sorted(set(discovered_links)),
        "onclick_div_count": nav_trap["onclickDivs"],
        "javascript_void_link_count": nav_trap["jsVoidLinks"],
        "rendered_html": rendered_html,
        "signals": signals,
    }
    validate(result, "crawl-render-audit")
    return result


def _drive_scroll(page: Any) -> int:
    before = page.evaluate("document.querySelectorAll('*').length")
    last_height = 0
    for _ in range(_MAX_SCROLL_STEPS):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(250)
        height = page.evaluate("document.body.scrollHeight")
        if height == last_height:
            break
        last_height = height
    after = page.evaluate("document.querySelectorAll('*').length")
    return max(after - before, 0)


def _drive_interactions(page: Any) -> list[dict[str, Any]]:
    performed: list[dict[str, Any]] = []
    for selector in _INTERACTION_SELECTORS:
        elements = page.query_selector_all(selector)
        for el in elements[:_MAX_INTERACTIONS]:
            before = page.evaluate("document.body.innerText.length")
            try:
                el.click(timeout=2000)
                page.wait_for_timeout(200)
                after = page.evaluate("document.body.innerText.length")
                performed.append(
                    {"type": "click", "selector": selector, "revealed_text_delta": after - before}
                )
            except Exception as exc:  # noqa: BLE001 - a failed interaction must not abort the audit
                performed.append(
                    {
                        "type": "click",
                        "selector": selector,
                        "revealed_text_delta": None,
                        "error": str(exc),
                    }
                )
            if len(performed) >= _MAX_INTERACTIONS:
                return performed
    return performed


def _traverse_shadow_dom(page: Any) -> tuple[list[str], int]:
    data = page.evaluate(
        """
        () => {
          const found = [];
          let totalText = 0;
          document.querySelectorAll('*').forEach(el => {
            if (el.shadowRoot) {
              found.push(el.tagName.toLowerCase());
              totalText += (el.shadowRoot.textContent || '').length;
            }
          });
          return { found, totalText };
        }
        """
    )
    return data.get("found", []), data.get("totalText", 0)
