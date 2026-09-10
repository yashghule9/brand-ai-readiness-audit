"""End-to-end test of braiaudit.pipeline.run_audit against mocked HTTP,
exercising the full six-step sequence without a network or a browser."""

from __future__ import annotations

import requests
import responses

from braiaudit.pipeline import AuditOptions, run_audit
from braiaudit.schemas import validate


@responses.activate
def test_full_pipeline_produces_a_schema_valid_report(fixture_html):
    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://example.com/sitemap.xml", status=404)
    responses.add(
        responses.GET,
        "https://example.com/",
        body=fixture_html("boilerplate_heavy.html"),
        status=200,
        content_type="text/html",
    )

    report = run_audit("example.com", options=AuditOptions(max_pages=1, max_render_pages=0))

    validate(report, "audit-report")
    assert report["site"] == "example.com"
    titles = {f["title"] for f in report["findings"]}
    assert "High Noise-to-Content Ratio Obstructs Data Extraction" in titles
    assert "No Schema.org / JSON-LD Structured Data Available" in titles

    # meta-analysis: coverage reflects what actually ran (no render backend
    # engaged, so render-only categories read visibly below 100%), and
    # structural validation of the report itself passes.
    meta = report["meta"]
    assert "website-observer" in meta["coverage"]["skills_engaged"]
    assert "crawl-render-audit" not in meta["coverage"]["skills_engaged"]
    assert meta["coverage"]["categories"]["rendering_and_execution"]["coverage_pct"] < 100.0
    assert meta["validation"]["passed"] is True


@responses.activate
def test_pipeline_engages_render_dependent_coverage_only_when_render_ran(fixture_html):
    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://example.com/sitemap.xml", status=404)
    responses.add(
        responses.GET,
        "https://example.com/",
        body=fixture_html("clean_article.html"),
        status=200,
        content_type="text/html",
    )

    # clean_article.html has no CSR/app-shell signals, so render never
    # triggers even with a nonzero budget — coverage should reflect that
    # crawl-render-audit's checks were not engaged for this page.
    report = run_audit("example.com", options=AuditOptions(max_pages=1, max_render_pages=5))

    assert "crawl-render-audit" not in report["meta"]["coverage"]["skills_engaged"]


@responses.activate
def test_robots_disallow_halts_that_url_without_crashing():
    responses.add(
        responses.GET,
        "https://example.com/robots.txt",
        body="User-agent: *\nDisallow: /\n",
        status=200,
    )

    report = run_audit("example.com", options=AuditOptions(max_pages=1, max_render_pages=0))

    validate(report, "audit-report")
    # A blanket `Disallow: /` blocks this auditor *and* every AI answer-engine
    # crawler — two distinct critical findings, since a brand can fix one
    # without fixing the other.
    assert report["summary"]["critical"] == 2
    titles = {f["title"] for f in report["findings"]}
    assert titles == {
        "Robots.txt Rules Block AI Scrapers",
        "Robots.txt Blocks AI Answer-Engine Crawlers",
    }


@responses.activate
def test_ai_crawler_block_is_caught_even_when_this_auditor_is_allowed(fixture_html):
    """The headline visibility failure: a site that welcomes classic search
    crawlers while turning away the agents that feed AI assistants."""
    responses.add(
        responses.GET,
        "https://example.com/robots.txt",
        body="User-agent: Googlebot\nAllow: /\n\nUser-agent: GPTBot\nDisallow: /\n",
        status=200,
    )
    responses.add(responses.GET, "https://example.com/sitemap.xml", status=404)
    responses.add(
        responses.GET,
        "https://example.com/",
        body=fixture_html("clean_article.html"),
        status=200,
        content_type="text/html",
    )

    report = run_audit("example.com", options=AuditOptions(max_pages=1, max_render_pages=0))

    validate(report, "audit-report")
    finding = next(
        f for f in report["findings"] if f["title"] == "Robots.txt Blocks AI Answer-Engine Crawlers"
    )
    assert finding["severity"] == "critical"
    assert "GPTBot" in finding["evidence"]
    assert "Googlebot" in finding["evidence"]
    # The action must tell the brand what to change, not name an audit tool.
    assert "playwright" not in finding["suggested_action"]["summary"].lower()


@responses.activate
def test_unreachable_seed_produces_no_fabricated_findings():
    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://example.com/sitemap.xml", status=404)
    responses.add(
        responses.GET,
        "https://example.com/",
        body=requests.exceptions.ConnectionError("boom"),
    )

    report = run_audit("example.com", options=AuditOptions(max_pages=1, max_render_pages=0))

    validate(report, "audit-report")
    assert report["summary"]["total_findings"] == 0


def _page(path: str, links: list[str]) -> str:
    anchors = "".join(f'<a href="{href}">{href}</a>' for href in links)
    body = "Garuda Footwear sells running shoes, cricket shoes and kids footwear. " * 12
    return (
        f"<html><head><title>{path}</title></head><body>"
        f"<nav>{anchors}</nav><main><h1>{path}</h1><p>{body}</p></main>"
        "</body></html>"
    )


@responses.activate
def test_frontier_crawls_link_graph_breadth_first_without_a_render_backend():
    """The multi-page crawl must work from static HTML alone: with no
    Playwright, links come from <a href> parsing, and depth 2 reaches a page
    only linked from a depth-1 page."""
    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://example.com/sitemap.xml", status=404)
    site = {
        "https://example.com/": ["/pricing", "/about"],
        "https://example.com/pricing": ["/pricing/enterprise"],
        "https://example.com/about": [],
        "https://example.com/pricing/enterprise": [],
        # Depth 3 — must NOT be crawled at max_depth=2.
        "https://example.com/pricing/enterprise/legal": [],
    }
    responses.add(
        responses.GET,
        "https://example.com/pricing/enterprise",
        body=_page("/pricing/enterprise", ["/pricing/enterprise/legal"]),
        status=200,
        content_type="text/html",
    )
    for url, links in site.items():
        if url == "https://example.com/pricing/enterprise":
            continue
        responses.add(
            responses.GET, url, body=_page(url, links), status=200, content_type="text/html"
        )

    report = run_audit(
        "example.com", options=AuditOptions(max_pages=10, max_render_pages=0, max_depth=2)
    )

    validate(report, "audit-report")
    crawled = {u for f in report["findings"] for u in f["affected_urls"]}
    assert "https://example.com/pricing" in crawled, "depth 1 not reached"
    assert "https://example.com/pricing/enterprise" in crawled, "depth 2 not reached"
    assert "https://example.com/pricing/enterprise/legal" not in crawled, "max_depth not enforced"


@responses.activate
def test_max_pages_caps_the_frontier():
    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://example.com/sitemap.xml", status=404)
    many = [f"/p{i}" for i in range(20)]
    responses.add(
        responses.GET,
        "https://example.com/",
        body=_page("/", many),
        status=200,
        content_type="text/html",
    )
    for path in many:
        responses.add(
            responses.GET,
            f"https://example.com{path}",
            body=_page(path, []),
            status=200,
            content_type="text/html",
        )

    report = run_audit(
        "example.com", options=AuditOptions(max_pages=3, max_render_pages=0, max_depth=2)
    )

    crawled = {u for f in report["findings"] for u in f["affected_urls"]}
    assert len(crawled) == 3
