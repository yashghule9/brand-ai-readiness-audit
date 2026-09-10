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
        "example.com",
        options=AuditOptions(
            max_pages=5, max_render_pages=0, max_depth=2, corroborate=False
        ),
    )

    assert report["meta"]["crawl"]["pages_crawled"] == 3
    # Scoped to the audited host: off-site corroboration legitimately reads
    # robots.txt from the third parties it consults.
    robots_calls = [
        c for c in responses.calls if c.request.url == "https://example.com/robots.txt"
    ]
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


@__import__("responses").activate
def test_error_status_with_no_body_is_a_finding_not_a_false_clean():
    """A 403 with zero bytes and no matching anti-bot fingerprint used to
    produce zero signals and count as a successfully crawled page â€” a site
    scored 100 while we had actually retrieved nothing at all. Found live
    against a real site returning a bare 403."""
    import responses

    from braiaudit.pipeline import AuditOptions, run_audit

    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(
        responses.GET,
        "https://example.com/",
        body="",
        status=403,
        content_type="text/html",
    )

    report = run_audit(
        "example.com",
        options=AuditOptions(max_pages=1, max_render_pages=0, corroborate=False),
    )

    titles = {f["title"] for f in report["findings"]}
    assert "Page Returns An Error Status With No Retrievable Content" in titles
    assert report["summary"]["critical"] >= 1
    # And it must not silently be counted as a clean, fully-audited page.
    assert report["summary"]["readiness_score"] < 100


@__import__("responses").activate
def test_seed_redirect_expands_allowed_hosts_but_a_later_redirect_does_not():
    """A bare-domain seed that 301s to www is the ordinary case (zoho.com ->
    www.zoho.com is what typing the domain into a browser does) — every link
    on that page lives on the resolved host, and rejecting them silently
    degraded a real audit to a single page. Scoped to the seed only: a
    redirect met deeper in the crawl must never expand scope, or a page could
    walk the crawler onto a third party through an ordinary link."""
    import responses

    from braiaudit.pipeline import AuditOptions, run_audit

    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://www.example.com/robots.txt", status=404)
    home = (
        '<html><body><main><h1>Home</h1><a href="/pricing">Pricing</a>'
        "<p>" + "Example sells things. " * 30 + "</p></main></body></html>"
    )
    pricing = (
        '<html><body><main><h1>Pricing</h1>'
        '<a href="https://evil.example.org/">redirected elsewhere</a>'
        "<p>" + "Pricing details. " * 30 + "</p></main></body></html>"
    )
    responses.add(
        responses.GET,
        "https://example.com/",
        status=301,
        headers={"Location": "https://www.example.com/"},
    )
    responses.add(
        responses.GET, "https://www.example.com/", body=home, status=200,
        content_type="text/html",
    )
    responses.add(
        responses.GET, "https://www.example.com/pricing", body=pricing, status=200,
        content_type="text/html",
    )

    report = run_audit(
        "example.com",
        options=AuditOptions(max_pages=10, max_render_pages=0, max_depth=2, corroborate=False),
    )

    crawled = {u for f in report["findings"] for u in f["affected_urls"]}
    assert "https://www.example.com/pricing" in crawled, "seed redirect did not expand scope"
    assert not any("evil.example.org" in u for u in crawled), (
        "a redirect encountered mid-crawl must never expand allowed hosts"
    )


@__import__("responses").activate
def test_403_with_unrecognised_body_is_unconfirmed_not_analysed_as_content():
    """Infosys/Meesho-style case: a 403 with a real, non-empty HTML body and
    no matching anti-bot fingerprint. Analysing that body as the site's real
    content fabricates content-quality findings about a page never actually
    seen — this must land as an explicit "could not confirm" limitation
    instead, never as an ordinary defect, and never with content signals
    computed from the block page's own thin text."""
    import responses

    from braiaudit.fetch import observe

    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(
        responses.GET,
        "https://example.com/",
        body="<html><head><title>Forbidden</title></head>"
        "<body>Access to this resource is denied.</body></html>",
        status=403,
        content_type="text/html",
    )

    result = observe("https://example.com/")

    assert result["signals"] == ["http_error_status_unconfirmed"]
    assert result["parse_status"] == "ok"
    assert result.get("raw_html") is None, "block page body must not be analysed as real content"
    assert "confirm" in result["http_error_evidence"].lower()


@__import__("responses").activate
def test_403_unconfirmed_is_an_unscored_limitation_not_a_defect():
    """The critical requirement: unknown must not become an ordinary
    website defect. It must not count against the readiness score and must
    not appear in `findings` at all."""
    import responses

    from braiaudit.pipeline import AuditOptions, run_audit

    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(
        responses.GET,
        "https://example.com/",
        body="<html><body>Access denied for this request.</body></html>",
        status=403,
        content_type="text/html",
    )

    report = run_audit(
        "example.com",
        options=AuditOptions(max_pages=1, max_render_pages=0, corroborate=False),
    )

    titles = {f["title"] for f in report["findings"]}
    assert "Error-Status Response Could Not Be Confirmed As Blocked Or Genuine" not in titles
    limitation_titles = {lim["title"] for lim in report["audit_limitations"]}
    assert "Error-Status Response Could Not Be Confirmed As Blocked Or Genuine" in limitation_titles
    assert report["summary"]["readiness_score"] == 100


@__import__("responses").activate
def test_a_genuine_404_with_real_content_is_still_processed_normally():
    """The conservative scope matters: only 403/503 (the same pair the
    anti-bot fingerprint check already treats specially) are diverted to the
    "unconfirmed" limitation. An ordinary 404 with a real body â€” the
    overwhelmingly common case when a crawled link is simply dead â€” must
    keep going through normal content analysis exactly as before, so a
    legitimate error response is never swept into "possibly blocked"."""
    import responses

    from braiaudit.fetch import observe

    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(
        responses.GET,
        "https://example.com/gone",
        body="<html><head><title>Not Found</title></head>"
        "<body><h1>Page not found</h1></body></html>",
        status=404,
        content_type="text/html",
    )

    result = observe("https://example.com/gone")

    assert "http_error_status_unconfirmed" not in result["signals"]
    assert "http_error_status_blocked" not in result["signals"]
    assert result.get("raw_html") is not None
    assert result["soft_404_suspected"] is False  # status isn't 200, so N/A


@__import__("responses").activate
def test_discovery_callback_refuses_off_host_urls(monkeypatch):
    """The frontier's host guard was not the only way off-host URLs could be
    fetched. `observe()` follows redirects, so a depth-1 page that 301s
    off-host makes `_extract_internal_links` use the *redirected* host as its
    base — and the resulting third-party links, while correctly rejected by
    the frontier, were still handed to `discovery` in the same unfiltered
    list. Its `fetch_page_text` callback then fetched them, letting a third
    party's text feed answer-completeness for signals about the audited site.

    Reproduced before the fix: `https://evil.test/secret` — a URL appearing
    only on the off-host page — was fetched.
    """
    import responses

    from braiaudit import pipeline
    from braiaudit.pipeline import AuditOptions, run_audit

    fetched_by_callback: list[str] = []
    real = pipeline._observe_and_clean_text

    def spy(url, options, session, origin_cache=None):
        fetched_by_callback.append(url)
        return real(url, options, session, origin_cache)

    monkeypatch.setattr(pipeline, "_observe_and_clean_text", spy)

    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://example.com/sitemap.xml", status=404)
    responses.add(responses.GET, "https://evil.test/robots.txt", status=404)
    responses.add(responses.GET, "https://evil.test/sitemap.xml", status=404)

    home = (
        '<html><body><main><h1>Home</h1><a href="/hop">hop</a>'
        "<p>" + "Short. " * 5 + "</p></main></body></html>"
    )
    responses.add(
        responses.GET, "https://example.com/", body=home, status=200, content_type="text/html"
    )
    # An on-host page that redirects off-host mid-crawl — the only way a
    # third-party URL can reach `internal_links` at all, since the static
    # extractor filters plain off-host links at the producer.
    responses.add(
        responses.GET,
        "https://example.com/hop",
        status=301,
        headers={"Location": "https://evil.test/landing"},
    )
    offhost = (
        '<html><body><main><h1>Elsewhere</h1>'
        '<a href="https://evil.test/secret">secret</a>'
        "<p>" + "Third-party words about cost and support. " * 20 + "</p></main></body></html>"
    )
    responses.add(
        responses.GET,
        "https://evil.test/landing",
        body=offhost,
        status=200,
        content_type="text/html",
    )
    responses.add(
        responses.GET,
        "https://evil.test/secret",
        body=offhost,
        status=200,
        content_type="text/html",
    )

    run_audit(
        "example.com",
        options=AuditOptions(
            max_pages=10, max_render_pages=0, max_depth=2, corroborate=False
        ),
    )

    # The callback refused it: the fetch helper was never even reached for an
    # off-host URL, so nothing was fetched, rendered, cleaned or diagnosed.
    assert not any("evil.test" in u for u in fetched_by_callback), (
        f"discovery callback accepted an off-host URL: {fetched_by_callback}"
    )

    # And no request for it was ever issued.
    requested = [c.request.url for c in responses.calls]
    assert not any("evil.test/secret" in u for u in requested), (
        "an off-host URL discovered only on a third-party page was fetched"
    )

    # On-host candidates are unaffected — the guard rejects by host, not by
    # refusing to run discovery at all.
    assert any("example.com" in u for u in fetched_by_callback)
