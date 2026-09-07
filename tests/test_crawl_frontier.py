"""Tests for AI-crawler robots evaluation, static link extraction, and the
BFS crawl frontier — the three pieces that make a multi-page audit work
without the optional render backend."""

from __future__ import annotations

import urllib.robotparser

import pytest
from bs4 import BeautifulSoup

from braiaudit.fetch import (
    AI_CRAWLER_USER_AGENTS,
    _extract_internal_links,
    canonical_crawl_url,
)
from braiaudit.pipeline import _crawl_order, _diagnose

ROBOTS_AI_BLOCKED = """
User-agent: Googlebot
Allow: /

User-agent: GPTBot
Disallow: /

User-agent: ClaudeBot
Disallow: /
"""


def _access(robots_text: str, url: str = "https://example.com/") -> dict[str, bool]:
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(robots_text.splitlines())
    return {a: rp.can_fetch(a, url) for a in AI_CRAWLER_USER_AGENTS}


def test_ai_crawlers_blocked_while_googlebot_allowed():
    access = _access(ROBOTS_AI_BLOCKED)
    assert access["GPTBot"] is False
    assert access["ClaudeBot"] is False
    # An agent the file never names is not blocked by someone else's rule.
    assert access["PerplexityBot"] is True

    rp = urllib.robotparser.RobotFileParser()
    rp.parse(ROBOTS_AI_BLOCKED.splitlines())
    assert rp.can_fetch("Googlebot", "https://example.com/") is True


def test_permissive_robots_blocks_nothing():
    assert all(_access("User-agent: *\nAllow: /\n").values())


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://example.com/pricing#plans", "https://example.com/pricing"),
        ("https://example.com/pricing/", "https://example.com/pricing"),
        ("https://example.com/", "https://example.com/"),
    ],
)
def test_canonical_crawl_url_collapses_spellings(raw, expected):
    assert canonical_crawl_url(raw) == expected


def test_extract_internal_links_filters_and_dedupes():
    html = """
    <a href="/pricing">Pricing</a>
    <a href="/pricing/">Pricing again</a>
    <a href="/pricing#plans">Pricing anchor</a>
    <a href="https://example.com/about">About</a>
    <a href="https://other.com/x">Offsite</a>
    <a href="/brochure.pdf">PDF</a>
    <a href="mailto:hi@example.com">Mail</a>
    <a href="#top">Anchor</a>
    """
    links = _extract_internal_links(BeautifulSoup(html, "html.parser"), "https://example.com/")
    assert links == ["https://example.com/about", "https://example.com/pricing"]


def test_crawl_order_prefers_query_relevant_urls():
    links = [
        "https://example.com/privacy-policy",
        "https://example.com/pricing-plans",
        "https://example.com/careers",
    ]
    ordered = _crawl_order(links, ["what does this product cost pricing"])
    assert ordered[0] == "https://example.com/pricing-plans"
    # Without queries the caller's order is preserved — no wasted scoring.
    assert _crawl_order(links, []) == links


def test_raw_html_never_leaks_into_finding_evidence():
    bundle = {
        "signals": ["missing_schema_org"],
        "raw_html": "<html>" + "x" * 5000 + "</html>",
        "clean_text": "y" * 5000,
        "raw_text_length": 12,
    }
    findings = _diagnose("https://example.com/", bundle)
    evidence = findings[0]["evidence"]
    assert "xxxx" not in evidence
    assert "yyyy" not in evidence
    assert "raw_text_length=12" in evidence
    assert len(evidence) < 400


def test_crawl_budget_is_reported_not_silently_applied():
    """A partial crawl must never read as a complete one: when a budget stops
    the frontier, the report has to say so."""
    from braiaudit.report import assemble_report

    report = assemble_report(
        site="example.com",
        findings_by_url={},
        pages_crawled=3,
        crawl_note="Crawl stopped at the 240s time budget with 9 URL(s) still queued.",
    )
    crawl = report["meta"]["crawl"]
    assert crawl["stopped_early"] is True
    assert "still queued" in crawl["note"]

    complete = assemble_report(site="example.com", findings_by_url={}, pages_crawled=3)
    assert complete["meta"]["crawl"]["stopped_early"] is False
    assert complete["meta"]["crawl"]["note"] == ""


def test_full_urls_are_normalised_not_blindly_prefixed():
    """`site` is documented as "bare domain or full URL". Prefixing a full URL
    built https://https://host/, which resolves nowhere — the audit then
    reported zero pages crawled with no visible cause."""
    from braiaudit.fetch import normalize_url, site_label

    for typed in (
        "caterworld.ai",
        "www.caterworld.ai",
        "https://www.caterworld.ai/",
        "http://www.caterworld.ai/some/path",
    ):
        seed = normalize_url(site_label(typed))
        assert seed.count("https://") == 1, seed
        assert "caterworld.ai" in site_label(typed)
        assert not site_label(typed).startswith("http")


def test_render_degrades_to_a_coverage_gap_when_the_browser_cannot_launch(monkeypatch):
    """Installing the playwright package without a usable browser must not
    take the audit down. Before this, a launch failure propagated out of
    render() and killed the whole run — installing the optional extra made
    the tool strictly worse than not having it."""
    from braiaudit import render as render_mod

    def boom(*args, **kwargs):
        raise RuntimeError("Executable doesn't exist at ...chrome.exe")

    monkeypatch.setattr(render_mod, "_PLAYWRIGHT_AVAILABLE", True)
    monkeypatch.setattr(render_mod, "_render_with_browser", boom)

    result = render_mod.render("https://example.com/")
    assert result["available"] is False
    assert result["signals"] == ["render_backend_unavailable"]


def test_render_reports_unavailable_without_the_package(monkeypatch):
    from braiaudit import render as render_mod

    monkeypatch.setattr(render_mod, "_PLAYWRIGHT_AVAILABLE", False)
    assert render_mod.render("https://example.com/")["available"] is False


@__import__("responses").activate
def test_frontier_never_leaves_the_audited_host():
    """A link to a third party — a social profile, a partner site — must not
    pull the crawler off the brand's own site."""
    import responses

    from braiaudit.pipeline import AuditOptions, run_audit

    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://example.com/sitemap.xml", status=404)
    body = (
        '<html><body><main><h1>Home</h1>'
        '<a href="/about">About</a>'
        '<a href="https://www.linkedin.com/company/example/">LinkedIn</a>'
        "<p>" + "Example sells things. " * 30 + "</p></main></body></html>"
    )
    responses.add(
        responses.GET, "https://example.com/", body=body, status=200, content_type="text/html"
    )
    responses.add(
        responses.GET,
        "https://example.com/about",
        body=body,
        status=200,
        content_type="text/html",
    )

    report = run_audit(
        "example.com", options=AuditOptions(max_pages=10, max_render_pages=0, max_depth=2)
    )

    touched = {u for f in report["findings"] for u in f["affected_urls"]}
    touched |= set(report["meta"]["crawl"]["pages_unreachable"])
    assert all("example.com" in u for u in touched), touched
    assert not any("linkedin" in u for u in touched)


@__import__("responses").activate
def test_robots_and_sitemap_are_fetched_once_per_origin_not_once_per_page():
    """They are per-site documents. Refetching them for every page cost two
    extra round trips per page — most of the wall-clock time on a slow host,
    and the difference between finishing inside the runtime budget or not."""
    import responses

    from braiaudit.pipeline import AuditOptions, run_audit

    responses.add(responses.GET, "https://example.com/robots.txt", body="", status=200)
    responses.add(responses.GET, "https://example.com/sitemap.xml", status=404)
    page = (
        '<html><body><main><h1>Hi</h1><a href="/a">a</a><a href="/b">b</a>'
        "<p>" + "Words about the product. " * 30 + "</p></main></body></html>"
    )
    for path in ("/", "/a", "/b"):
        responses.add(
            responses.GET,
            f"https://example.com{path}",
            body=page,
            status=200,
            content_type="text/html",
        )

    report = run_audit(
        "example.com", options=AuditOptions(max_pages=5, max_render_pages=0, max_depth=2)
    )

    assert report["meta"]["crawl"]["pages_crawled"] == 3
    robots_calls = [c for c in responses.calls if c.request.url.endswith("/robots.txt")]
    assert len(robots_calls) == 1, f"robots.txt fetched {len(robots_calls)} times"


@__import__("responses").activate
def test_rendered_links_are_canonicalised_before_entering_the_frontier(monkeypatch):
    """The static extractor canonicalises; rendered links came straight from
    the DOM. `/a`, `/a/` and `/a#top` are one page, and treating them as three
    burns the page budget re-auditing it."""
    import responses

    from braiaudit import render as render_mod
    from braiaudit.pipeline import AuditOptions, run_audit

    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://example.com/sitemap.xml", status=404)
    body = (
        '<html><head><title>t</title></head><body><main><h1>Hi</h1>'
        "<p>" + "Words about the product. " * 30 + "</p></main></body></html>"
    )
    for path in ("/", "/a"):
        responses.add(
            responses.GET,
            f"https://example.com{path}",
            body=body,
            status=200,
            content_type="text/html",
        )

    def fake_render(url, **kwargs):
        return {
            "url": url,
            "available": True,
            "render_timed_out": False,
            "rendered_html": body,
            "discovered_internal_links": [
                "https://example.com/a",
                "https://example.com/a/",
                "https://example.com/a#top",
            ],
            "signals": [],
        }

    monkeypatch.setattr(render_mod, "render", fake_render)
    monkeypatch.setattr(
        "braiaudit.pipeline._RENDER_TRIGGER_SIGNALS", {"missing_schema_org"}
    )

    report = run_audit(
        "example.com", options=AuditOptions(max_pages=10, max_render_pages=5, max_depth=2)
    )

    assert report["meta"]["crawl"]["pages_crawled"] == 2, "one page audited under three spellings"
